"""
Per-player factor values for every draftable player.

    python tools/build_factors.py
    python tools/build_factors.py --season 2025 --limit 300
    python tools/build_factors.py --league 1312218810614300672

Writes artifacts/player_factors.json for DraftLab to import.

WHY THIS EXISTS
---------------
build_benchmarks.py produces the denominator: what the best player at a
position does. This produces the numerator: what each actual player does. A
benchmark with no players measured against it cannot rank anybody, and DraftLab
shipped with twelve hand-authored players whose factor values were back-solved
from the grade they were supposed to receive.

Same ownership boundary as everywhere else in this repo:

    this side   what the numbers are    factor values, provenance, the crosswalk
    that side   what they mean          grading, archetypes, risk, strategy

So nothing here grades, scores, or ranks. It reports measurements and says
where each one came from.

FACTOR VALUES ARE FORMAT-INVARIANT
----------------------------------
Unlike benchmarks, these carry no std/half/ppr split. A player's targets per
game is the same number in every format. Scoring format only ever mattered for
*cohort selection* — which players counted as the top 3 — and there is no
cohort here. Emitting three identical copies would imply a distinction that
does not exist.

THE TEAM-CHANGE TRAP
--------------------
Team-context factors (off_ppg_rank, team_pass_attempts, team_pass_att_rank,
team_target_rank, rec_td_rank) describe the offence a player was part of LAST
season. ADP describes the roster he is on THIS season. For anyone who moved,
those disagree, and silently attaching last year's team context to this year's
player is exactly the class of bug this project keeps hitting: a plausible
number, no error, wrong answer.

Every such player is flagged `team_changed: true`, and his team-context factors
are emitted with provenance `stale:team_changed` rather than being passed off as
current. Counted in the summary so the size of the problem is visible.

AN INJURED VETERAN IS NOT A ROOKIE
----------------------------------
Both have no row in the target season, and calling both null tells a consumer
"unknown" when for one the honest answer is "known, just older". Aiyuk, Dell,
Brooks and Watson all missed 2025 and all have real histories; grading them as
blanks alongside genuine unknowns throws away the thing that makes them
draftable.

So a miss falls back through `--lookback` earlier seasons, and anything found
is tagged `measured:<year>` rather than passed off as current. Team context is
NOT recovered — those factors describe an offence in a season the player may
not have been part of, and inventing them is the exact failure this file
exists to prevent. They come back `missing:no_team_context`.

The same mechanism recovers two-way players. load_player_seasons buckets by
the source's position label, so a receiver filed under CB is dropped despite
having real receiving lines; the recovery pass ignores the label entirely.

A true rookie still yields `missing:no_prior_season` everywhere, because there
is genuinely nothing to find. Filling that needs projections, which this script
has no business inventing. Provenance is per-field precisely so a later pass
can fill selectively without a schema change.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_benchmarks import (                      # noqa: E402
    COMPUTABLE, FACTORS, _OFF_PPG_SOURCE, _gap_note,
    load_player_seasons, per_game,
)
from sleeper_core.offense import safe_float        # noqa: E402
from sleeper_core import values                     # noqa: E402
from sleeper_core.adp import (                      # noqa: E402
    fetch_adp, name_keys, normalize_name, strip_suffix,
    sleeper_id_index, lookup_sleeper_id,
)
from sleeper_core.config import DEFAULT_LEAGUE_ID, STATS_CACHE_TTL  # noqa: E402
from sleeper_core.http import nflverse_csv         # noqa: E402
from sleeper_core.stats import to_nflverse_team     # noqa: E402

# Factors describing the offence rather than the player. These go stale the
# moment a player changes teams.
TEAM_CONTEXT = {"off_ppg_rank", "team_pass_attempts", "team_pass_att_rank",
                "team_target_rank", "rec_td_rank"}


def index_measurements(season: int) -> dict:
    """Per-game factor values for one season, plus indexes for diagnosing misses.

    `by_key_pos` does the real join. The other two exist only so an unmatched
    player can be classified mechanically instead of eyeballed: a name present
    under a different position is a join bug, a name absent entirely is a
    player with no season. Those need opposite responses and look identical in
    a flat list.
    """
    rows = load_player_seasons([season])
    by_key_pos: dict[tuple[str, str], dict] = {}
    by_key: dict[str, list[dict]] = defaultdict(list)
    by_last: dict[str, list[dict]] = defaultdict(list)
    for ps in rows:
        vals = per_game(ps)
        entry = {"values": vals, "team": ps["team"], "games": ps["games"],
                 "name": ps["name"], "position": ps["position"]}
        for key in name_keys(ps["name"]):
            by_key_pos[(key, ps["position"])] = entry
            by_key[key].append(entry)
        sn = surname(ps["name"])
        if sn:
            by_last[sn].append(entry)
    return {"by_key_pos": by_key_pos, "by_key": by_key, "by_last": by_last}


def match(measured: dict, name: str, position: str) -> dict | None:
    """Name+position join, position first — the same rule the ADP crosswalk uses."""
    for key in name_keys(name):
        hit = measured["by_key_pos"].get((key, (position or "").upper()))
        if hit:
            return hit
    return None


def all_positions_index(season: int) -> dict:
    """name_key -> every position the source lists, including ones we drop.

    load_player_seasons keeps only QB/RB/WR/TE, so `by_key` cannot see a
    receiver the source files under DB. Two-way players and position changes
    then read as "absent" when the data is really there. This index exists
    solely to tell those apart.
    """
    rows = nflverse_csv("stats_player", f"stats_player_week_{season}.csv",
                        ttl=STATS_CACHE_TTL)
    out: dict[str, set] = defaultdict(set)
    for r in rows or []:
        nm = r.get("player_display_name") or ""
        pos = (r.get("position") or "").upper()
        if nm and pos:
            for k in name_keys(nm):
                out[k].add(pos)
    return out


def recover(season: int, name: str) -> dict | None:
    """Aggregate one player's raw weekly rows, ignoring the position label.

    Two different misses turn out to have the same fix, because in both the
    data exists and the main path simply cannot reach it.

      Position mismatch. load_player_seasons keeps only QB/RB/WR/TE, so a
      two-way player the source files under CB is dropped despite having real
      receiving lines. Bucketing by the source's label is what loses him.

      Missed season. A veteran who was injured has no row in the target year
      but a full history the year before. That is not the same as a rookie,
      who has none anywhere — treating both as null tells a consumer "unknown"
      when the honest answer is "known, just older".

    Player factors only. Team context is deliberately not recovered: those
    describe an offence in a season this player may not have been part of, and
    inventing them is the failure mode this whole file is built to avoid.
    """
    rows = nflverse_csv("stats_player", f"stats_player_week_{season}.csv",
                        ttl=STATS_CACHE_TTL)
    keys = set(name_keys(name))
    agg = {k: 0.0 for k in ("attempts", "passing_tds", "carries", "rushing_tds",
                            "targets", "receptions", "receiving_tds")}
    games = 0
    teams: Counter = Counter()
    for r in rows or []:
        if not keys & set(name_keys(r.get("player_display_name") or "")):
            continue
        try:
            wk = int(float(r.get("week") or 0))
        except (TypeError, ValueError):
            continue
        if wk < 1:
            continue
        games += 1
        for k in agg:
            agg[k] += safe_float(r.get(k))
        t = (r.get("team") or "").upper()
        if t:
            teams[t] += 1
    if not games:
        return None
    g = max(games, 1)
    return {
        "values": {
            "pass_attempts": agg["attempts"] / g,
            "passing_tds": agg["passing_tds"] / g,
            "rush_attempts": agg["carries"] / g,
            "rushing_tds": agg["rushing_tds"] / g,
            "targets": agg["targets"] / g,
            "receptions": agg["receptions"] / g,
            "touchdowns": (agg["rushing_tds"] + agg["receiving_tds"]) / g,
            "touches": (agg["carries"] + agg["receptions"]) / g,
        },
        "team": teams.most_common(1)[0][0] if teams else None,
        "games": games,
        "name": name,
        "season": season,
    }


def surname(name: str) -> str:
    """Last name, suffix removed.

    Taking the final token naively made "Omar Cooper Jr." match every other
    player whose name ends in "Jr." — the suffix became the surname.
    """
    parts = strip_suffix((name or "").strip()).split()
    return normalize_name(parts[-1]) if parts else ""


def classify_miss(measured: dict, all_pos: dict, name: str, position: str) -> str:
    """Why did this player not join? Answers the question the list used to pose."""
    pos = (position or "").upper()

    # Present under another MODELLED position: a genuine join bug.
    for key in name_keys(name):
        others = [e for e in measured["by_key"].get(key, []) if e["position"] != pos]
        if others:
            got = ", ".join(sorted({e["position"] for e in others}))
            return f"JOIN BUG: present as {got}, ADP says {pos}"

    # Present under a position we drop at load. Real data, filtered out.
    for key in name_keys(name):
        seen = all_pos.get(key)
        if seen and pos not in seen:
            got = ", ".join(sorted(seen))
            return f"DROPPED: source lists him as {got}; only QB/RB/WR/TE are loaded"

    # Surname alone is almost no evidence — there are many Williamses. Require
    # the first initial to agree as well, or this reports noise as findings.
    sn = surname(name)
    first = normalize_name((name or " ").split()[0])[:1]
    if sn:
        near = [e for e in measured["by_last"].get(sn, [])
                if e["position"] == pos
                and normalize_name((e["name"] or " ").split()[0])[:1] == first]
        if near:
            names = ", ".join(sorted({e["name"] for e in near})[:3])
            return f"possible name variant -> {names}"

    return "absent from source: no season played"


def build_player(p: dict, measured: dict, sid: str | None,
                 fallback: dict | None = None) -> dict:
    pos = (p.get("position") or "").upper()
    adp_team = to_nflverse_team((p.get("team") or "").upper())
    hit = match(measured, p.get("name") or "", pos)

    # Nothing in the target season, but the player exists elsewhere in the
    # source — a two-way player filed under defence, or a veteran who missed
    # the year. Personal factors carry over; team context does not.
    recovered_from = None
    if not hit and fallback:
        hit = fallback
        recovered_from = fallback["season"]

    prior_team = hit["team"] if hit else None
    team_changed = bool(hit and prior_team and adp_team and prior_team != adp_team)

    factors: dict[str, dict] = {}
    for fid, src in FACTORS.get(pos, []):
        if fid not in COMPUTABLE:
            factors[fid] = {"value": None, "provenance": "unsourced",
                            "note": _gap_note(src)}
            continue
        if not hit:
            factors[fid] = {"value": None, "provenance": "missing:no_prior_season",
                            "note": "no measured season for this player"}
            continue
        # Order matters. recover() deliberately produces no team-context keys,
        # so testing `raw is None` first swallowed every one of them into
        # missing:not_recorded and left this branch unreachable. Null either
        # way, but the labels mean opposite things to a consumer: "the source
        # lacks this" versus "we withheld this on purpose".
        if fid in TEAM_CONTEXT and recovered_from:
            factors[fid] = {
                "value": None, "provenance": "missing:no_team_context",
                "note": f"player recovered from {recovered_from}; team context "
                        f"for that season describes a different situation",
            }
            continue
        raw = hit["values"].get(fid)
        if raw is None:
            factors[fid] = {"value": None, "provenance": "missing:not_recorded",
                            "note": "player matched but factor absent from source"}
            continue
        if recovered_from:
            factors[fid] = {
                "value": round(raw, 3), "provenance": f"measured:{recovered_from}",
                "note": f"no {p.get('_target_season', 'target')} season; "
                        f"measured from {recovered_from}",
            }
            continue
        if fid in TEAM_CONTEXT and team_changed:
            factors[fid] = {
                "value": round(raw, 3), "provenance": "stale:team_changed",
                "note": f"describes {prior_team}, player now on {adp_team}",
            }
            continue
        factors[fid] = {"value": round(raw, 3), "provenance": "measured", "note": None}

    return {
        "sleeper_id": sid,
        "name": p.get("name"),
        "position": pos,
        "team": adp_team or None,
        "prior_team": prior_team,
        "team_changed": team_changed,
        "adp": p.get("adp"),
        "adp_round_pick": p.get("adp_formatted"),
        "games_played": hit["games"] if hit else 0,
        "matched": bool(hit),
        "recovered_from_season": recovered_from,
        "factors": factors,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2025,
                    help="season to measure factors from (default: last completed)")
    ap.add_argument("--adp-season", type=int, default=None,
                    help="ADP season defining the draftable universe (default: season+1)")
    ap.add_argument("--limit", type=int, default=300,
                    help="how deep into ADP to go")
    ap.add_argument("--league", default=DEFAULT_LEAGUE_ID,
                    help="league whose scoring format selects the ADP variant")
    ap.add_argument("--lookback", type=int, default=2,
                    help="seasons to search back when the target year is empty "
                         "(recovers injured veterans; 0 disables)")
    ap.add_argument("--out", default="artifacts/player_factors.json")
    args = ap.parse_args()

    adp_season = args.adp_season or (args.season + 1)

    print(f"Measuring factors from {args.season}; universe = {adp_season} ADP ...")
    fmt = values.league_format(args.league)
    fetched = fetch_adp(fmt, adp_season)

    # ADP covers K and DEF; DraftLab models only QB/RB/WR/TE. Leaving them in
    # would stack ~30 guaranteed non-matches into the unmatched report and bury
    # the real join failures it exists to surface.
    ranked = fetched["players"]
    skipped = [p for p in ranked if (p.get("position") or "").upper() not in FACTORS]
    universe = [p for p in ranked
                if (p.get("position") or "").upper() in FACTORS][:args.limit]
    if skipped:
        pos_counts = Counter((p.get("position") or "?").upper() for p in skipped)
        print(f"  skipping unmodelled positions: {dict(pos_counts)}")
    if not universe:
        print("  ERROR: no ADP returned; cannot define a draftable universe.",
              file=sys.stderr)
        print(f"  attempts: {fetched.get('attempts')}", file=sys.stderr)
        return 1
    print(f"  universe: {len(universe)} players")

    measured = index_measurements(args.season)
    print(f"  measured: {len(measured['by_key_pos'])} name/position keys "
          f"from {args.season}")

    all_pos = all_positions_index(args.season)
    index = sleeper_id_index()
    players = []
    for p in universe:
        sid = lookup_sleeper_id(index, p.get("name"), p.get("position"), p.get("team"))
        row = build_player(p, measured, sid)
        # Only pay for the recovery scan on players the main path missed.
        if not row["matched"] and args.lookback:
            for back in range(0, args.lookback + 1):
                yr = args.season - back
                fb = recover(yr, p.get("name") or "")
                if fb:
                    row = build_player(dict(p, _target_season=args.season),
                                       measured, sid, fallback=fb)
                    break
        players.append(row)

    recovered = [p for p in players if p.get("recovered_from_season")]

    # Report the joins that failed. A silent 60% match rate would look like a
    # working pipeline; this project has been bitten by exactly that before.
    unmatched = [p for p in players if not p["matched"]]
    no_sid = [p for p in players if not p["sleeper_id"]]
    changed = [p for p in players if p["team_changed"]]

    prov = Counter()
    for p in players:
        for f in p["factors"].values():
            prov[f["provenance"]] += 1

    print(f"\njoins:")
    print(f"  matched to {args.season} stats   {len(players)-len(unmatched)}/{len(players)}")
    print(f"  resolved to a Sleeper ID      {len(players)-len(no_sid)}/{len(players)}")
    print(f"  changed teams since {args.season}    {len(changed)}")
    if recovered:
        print(f"  recovered from an earlier season {len(recovered)}")
        for r in recovered:
            why = ("filed under another position" if r["recovered_from_season"] == args.season
                   else "no season played that year")
            print(f"    {r['position']:3} {r['name']:22} <- {r['recovered_from_season']}  ({why})")
    print("\nfactor provenance:")
    for k, n in prov.most_common():
        print(f"  {k:28} {n}")

    if unmatched:
        reasons = {}
        for p in unmatched:
            reasons[p["name"]] = classify_miss(measured, all_pos, p["name"], p["position"])
        bugs = {n: r for n, r in reasons.items() if r.startswith(("JOIN BUG", "DROPPED"))}
        variants = {n: r for n, r in reasons.items() if r.startswith("possible")}

        print(f"\nunmatched: {len(unmatched)}")
        for p in unmatched:
            r = reasons[p["name"]]
            mark = "  <-- FIX" if r.startswith(("JOIN BUG", "DROPPED", "possible")) else ""
            print(f"  {p['position']:3} {p['name']:24} {r}{mark}")
        if bugs or variants:
            print(f"\n  {len(bugs)} join bug(s), {len(variants)} possible name variant(s).")
            print("  These are recoverable data, not missing players.")
        else:
            print("\n  All misses are players with no season in the source —")
            print("  rookies and anyone who sat out. Nothing to fix in the join.")

    by_pos = Counter(p["position"] for p in players)
    doc = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "basis": {
            "player_factors": f"measured:{args.season}",
            "universe": f"FantasyFootballCalculator ADP {adp_season}",
            "format": fmt,
            "off_ppg_rank_source": _OFF_PPG_SOURCE["used"],
        },
        "counts": {
            "players": len(players),
            "by_position": dict(by_pos),
            "matched": len(players) - len(unmatched),
            "unmatched": len(unmatched),
            "missing_sleeper_id": len(no_sid),
            "team_changed": len(changed),
            "recovered": len(recovered),
            "provenance": dict(prov),
        },
        "note": (
            "Factor values are format-invariant; only benchmark cohorts vary by "
            "scoring format. Check each factor's provenance before use: "
            "'measured' is current, 'stale:team_changed' describes the player's "
            "previous offence, 'missing:*' has no value at all."
        ),
        "players": players,
    }

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

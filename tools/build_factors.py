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

ROOKIES HAVE NO MEASUREMENTS
----------------------------
A 2026 rookie has no 2025 nflverse row, so every factor is null with
provenance `missing:no_prior_season`. That is a real gap, reported rather than
papered over with a projection this script has no business inventing. If the
gap turns out to be large enough to matter, the fix is a projections basis —
which is why provenance is per-field, so a later pass can fill selectively
without rewriting the schema.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
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
from sleeper_core import values                     # noqa: E402
from sleeper_core.adp import (                      # noqa: E402
    fetch_adp, name_keys, sleeper_id_index, lookup_sleeper_id,
)
from sleeper_core.config import DEFAULT_LEAGUE_ID   # noqa: E402
from sleeper_core.stats import to_nflverse_team     # noqa: E402

# Factors describing the offence rather than the player. These go stale the
# moment a player changes teams.
TEAM_CONTEXT = {"off_ppg_rank", "team_pass_attempts", "team_pass_att_rank",
                "team_target_rank", "rec_td_rank"}


def index_measurements(season: int) -> dict:
    """(name_key, position) -> per-game factor values for one season."""
    rows = load_player_seasons([season])
    out: dict[tuple[str, str], dict] = {}
    for ps in rows:
        vals = per_game(ps)
        for key in name_keys(ps["name"]):
            out[(key, ps["position"])] = {"values": vals, "team": ps["team"],
                                          "games": ps["games"], "name": ps["name"]}
    return out


def match(measured: dict, name: str, position: str) -> dict | None:
    """Name+position join, position first — the same rule the ADP crosswalk uses."""
    for key in name_keys(name):
        hit = measured.get((key, (position or "").upper()))
        if hit:
            return hit
    return None


def build_player(p: dict, measured: dict, sid: str | None) -> dict:
    pos = (p.get("position") or "").upper()
    adp_team = to_nflverse_team((p.get("team") or "").upper())
    hit = match(measured, p.get("name") or "", pos)

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
        raw = hit["values"].get(fid)
        if raw is None:
            factors[fid] = {"value": None, "provenance": "missing:not_recorded",
                            "note": "player matched but factor absent from source"}
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
    print(f"  measured: {len(measured)} name/position keys from {args.season}")

    index = sleeper_id_index()
    players = []
    for p in universe:
        sid = lookup_sleeper_id(index, p.get("name"), p.get("position"), p.get("team"))
        players.append(build_player(p, measured, sid))

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
    print("\nfactor provenance:")
    for k, n in prov.most_common():
        print(f"  {k:28} {n}")

    if unmatched:
        print(f"\nunmatched (first 15 of {len(unmatched)}) — rookies, or a broken join:")
        for p in unmatched[:15]:
            print(f"  {p['position']:3} {p['name']}")

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

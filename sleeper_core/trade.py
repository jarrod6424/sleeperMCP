"""
Pick-aware trade analysis.

Player pricing is FantasyCalc, same join as the original analyze_trade in
server.py (exact sleeperId, then exact name, then unique partial name).
Picks are parsed and valued by sleeper_core.picks. Anything that fails to
parse or price lands in unpriced_assets rather than crashing or inventing
a number.

Backward-compatible field names: format, source, give, get, give_total,
get_total, difference, percent_vs_larger_side, verdict, unmatched, note.
"""

from __future__ import annotations

from typing import Any

from . import advice, picks
from .config import FC_SOURCE, SPORT
from .http import get_json
from .league import compute_standings, resolve_league_id, resolve_my_roster, resolve_roster, user_map
from .players import load_players, player_name
from .values import fc_row, fc_values, league_format

PICK_LIMITATION = (
    "Pick values are heuristic schedule estimates, not FantasyCalc market quotes. "
    "See sleeper_core/picks.py for the curve (static Superflex/1QB table, with "
    "FantasyCalc rank-band means when the board is dense enough)."
)

ELITE_OVERALL_RANK = 36


def _index_fc(values: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_name: dict[str, dict] = {}
    by_sid: dict[str, dict] = {}
    for v in values:
        p = v.get("player") or {}
        nm = (p.get("name") or "").strip().lower()
        if nm:
            by_name[nm] = v
        sid = p.get("sleeperId")
        if sid:
            by_sid[str(sid)] = v
    return by_name, by_sid


def resolve_player_value(item: str, by_name: dict[str, dict], by_sid: dict[str, dict]):
    """Match one player token. Same rules as the original server.py helper.

    Returns the FantasyCalc row, or a (None, reason, matches) tuple.
    """
    raw = str(item).strip()
    key = raw.lower()
    v = by_sid.get(raw) or by_name.get(key)
    if v:
        return v
    partial = [vv for nm, vv in by_name.items() if key in nm]
    if len(partial) == 1:
        return partial[0]
    matches = [((vv.get("player") or {}).get("name") or nm) for nm, vv in by_name.items() if key in nm]
    if matches:
        return None, "ambiguous", matches
    return None, "not found", []


def _verdict(give_total: float, get_total: float) -> tuple[str, float, float]:
    diff = get_total - give_total
    larger = max(give_total, get_total, 1)
    ratio = abs(diff) / larger
    if ratio < 0.05:
        verdict = "roughly even"
    elif diff > 0:
        verdict = "you come out ahead"
    else:
        verdict = "you give up more value"
    return verdict, diff, ratio


def _standings_rank_map(lid: str) -> dict[int, dict]:
    try:
        ranked = compute_standings(lid)
    except Exception:
        return {}
    out: dict[int, dict] = {}
    for row in ranked:
        rid = row.get("roster_id")
        if rid is not None:
            out[int(rid)] = row
    return out


def _resolve_origin_roster(
    origin: dict[str, Any],
    lid: str,
    umap: dict[str, dict] | None,
    standings: dict[int, dict],
) -> tuple[int | None, str | None, int | None]:
    """Return (roster_id, team_name, standings_rank) for a pick origin."""
    if origin.get("roster_id") is not None:
        rid = int(origin["roster_id"])
        row = standings.get(rid) or {}
        return rid, row.get("owner"), row.get("rank")
    name = (origin.get("from_team") or "").strip().lower()
    if not name or not umap:
        return None, origin.get("from_team"), None
    rosters = get_json(f"/league/{lid}/rosters", cache=True) or []
    for uid, info in umap.items():
        team = (info.get("team_name") or "").strip().lower()
        mgr = (info.get("display_name") or "").strip().lower()
        if name == team or name == mgr or name in team or name in mgr:
            roster = next((r for r in rosters if r.get("owner_id") == uid), None)
            if roster:
                rid = roster.get("roster_id")
                row = standings.get(int(rid)) if rid is not None else {}
                return rid, info.get("team_name"), (row or {}).get("rank")
    return None, origin.get("from_team"), None


def _override_for(token: str, parsed: dict, overrides: dict[str, Any] | None):
    if not overrides:
        return None
    keys = [
        token,
        str(token).strip(),
        f"{parsed.get('season')} {parsed.get('round')}",
        f"{parsed.get('season')}-R{parsed.get('round')}",
    ]
    for k in keys:
        if k in overrides:
            return overrides[k]
    # Case-insensitive token match.
    lower = {str(k).strip().lower(): v for k, v in overrides.items()}
    return lower.get(str(token).strip().lower())


def _price_side_items(
    items: list[str],
    *,
    by_name: dict[str, dict],
    by_sid: dict[str, dict],
    model: str,
    slot_estimate: str,
    num_qbs: int,
    num_teams: int,
    fc_rows: list[dict],
    overrides: dict[str, Any] | None,
    lid: str,
    umap: dict[str, dict] | None,
    standings: dict[int, dict],
) -> dict[str, Any]:
    player_rows: list[dict] = []
    pick_rows: list[dict] = []
    missing: list[dict] = []
    unpriced: list[dict] = []
    player_total = 0
    pick_total = 0

    for it in items or []:
        parsed = picks.parse_pick_token(str(it))
        if parsed.get("ok") or picks.looks_like_pick(str(it)):
            if not parsed.get("ok"):
                unpriced.append(
                    {
                        "query": it,
                        "kind": "pick",
                        "reason": parsed.get("reason") or "unparseable_pick",
                    }
                )
                continue
            slot = slot_estimate if slot_estimate in picks.SLOT_MULT else "mid"
            if slot_estimate == "auto":
                _rid, _team, rank = _resolve_origin_roster(
                    parsed.get("origin") or {}, lid, umap, standings
                )
                slot = picks.slot_from_rank(rank, num_teams)
            override = _override_for(str(it), parsed, overrides) if model == "manual" else None
            if model == "manual" and override is None:
                # Manual only overrides listed tokens; the rest still use schedule.
                pass
            row = picks.price_parsed_pick(
                parsed,
                slot=slot,
                num_qbs=num_qbs,
                num_teams=num_teams,
                fc_rows=fc_rows if model != "manual" or override is None else None,
                override=override,
            )
            if row.get("value") is None:
                unpriced.append(
                    {
                        "query": it,
                        "kind": "pick",
                        "reason": row.get("source") or "unpriced",
                        "normalized": row.get("normalized"),
                    }
                )
                continue
            pick_rows.append(row)
            pick_total += row["value"]
            continue

        result = resolve_player_value(str(it), by_name, by_sid)
        if isinstance(result, tuple):
            _, reason, matches = result
            missing.append({"query": it, "reason": reason, "matches": matches})
            unpriced.append({"query": it, "kind": "player", "reason": reason, "matches": matches})
        elif result:
            player_rows.append(fc_row(result))
            player_total += result.get("value") or 0
        else:
            missing.append({"query": it, "reason": "not found"})
            unpriced.append({"query": it, "kind": "player", "reason": "not found"})

    return {
        "players": player_rows,
        "picks": pick_rows,
        "missing": missing,
        "unpriced": unpriced,
        "player_total": player_total,
        "pick_total": pick_total,
        "total": player_total + pick_total,
    }


def _positional_counts(player_ids: list[str], players_map: dict) -> dict[str, int]:
    counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    for pid in player_ids:
        pos = (player_name(str(pid), players_map).get("position") or "").upper()
        if pos in counts:
            counts[pos] += 1
    return counts


def _roster_player_ids(roster: dict) -> list[str]:
    return [str(p) for p in (roster.get("players") or [])]


def _find_owner_of(player_sid: str, rosters: list[dict]) -> dict | None:
    for r in rosters:
        owned = {str(p) for p in (r.get("players") or [])}
        if str(player_sid) in owned:
            return r
    return None


def _roster_fit(
    *,
    lid: str,
    subject_query: str | None,
    get_player_rows: list[dict],
    give_player_rows: list[dict],
    fmt: dict,
) -> dict[str, Any]:
    notes: list[str] = []
    resolved = (
        resolve_roster(lid, subject_query)
        if subject_query
        else resolve_my_roster(lid)
    )
    if not resolved:
        return {
            "summary": "Roster context unavailable.",
            "notes": ["Could not resolve the subject roster for fit notes."],
            "subject_team": None,
            "counterparty_team": None,
        }

    roster = resolved["roster"]
    owner = resolved["owner"]
    players_map = load_players()
    before = _positional_counts(_roster_player_ids(roster), players_map)

    give_pos = [(r.get("position") or "").upper() for r in give_player_rows]
    get_pos = [(r.get("position") or "").upper() for r in get_player_rows]
    after = dict(before)
    for pos in give_pos:
        if pos in after:
            after[pos] = max(0, after[pos] - 1)
    for pos in get_pos:
        if pos in after:
            after[pos] += 1

    deltas = []
    for pos in ("QB", "RB", "WR", "TE"):
        delta = after[pos] - before[pos]
        if delta:
            direction = "adds" if delta > 0 else "loses"
            deltas.append(f"{direction} {abs(delta)} {pos}")
            notes.append(
                f"{pos}: {before[pos]} → {after[pos]} ({'+' if delta > 0 else ''}{delta})"
            )

    is_sf = int(fmt.get("numQbs") or 1) >= 2
    if is_sf and after.get("QB", 0) <= 1:
        notes.append("Superflex: QB depth drops to a single quarterback — high risk.")
    if is_sf and after.get("QB", 0) >= 3 and before.get("QB", 0) < 3:
        notes.append("Superflex: improves QB depth.")

    # Lightweight contender vs rebuilder: standings rank only.
    rank = None
    try:
        for row in compute_standings(lid):
            if row.get("roster_id") == roster.get("roster_id"):
                rank = row.get("rank")
                break
    except Exception:
        pass
    num_teams = int(fmt.get("numTeams") or 12)
    if rank:
        if rank <= max(1, num_teams // 4):
            notes.append(f"Subject sits {rank}/{num_teams} — treat as a contender on record.")
        elif rank > num_teams - max(1, num_teams // 4):
            notes.append(f"Subject sits {rank}/{num_teams} — treat as a rebuilder on record.")

    give_elite = [r.get("name") for r in give_player_rows if (r.get("overall_rank") or 999) <= ELITE_OVERALL_RANK]
    if give_elite:
        notes.append("Giving elite-ranked player(s): " + ", ".join(str(n) for n in give_elite if n))

    counterparty = None
    try:
        rosters = get_json(f"/league/{lid}/rosters", cache=True) or []
        umap = user_map(lid)
        owners_found = []
        for row in get_player_rows:
            sid = row.get("sleeper_id")
            if not sid:
                continue
            other = _find_owner_of(str(sid), rosters)
            if other and other.get("roster_id") != roster.get("roster_id"):
                info = umap.get(other.get("owner_id"), {})
                owners_found.append(info.get("team_name") or info.get("display_name"))
        uniq = [n for n in dict.fromkeys(owners_found) if n]
        if len(uniq) == 1:
            counterparty = uniq[0]
            notes.append(f"Received players currently rostered by {counterparty}.")
    except Exception:
        pass

    if deltas:
        adds = [d[5:] for d in deltas if d.startswith("adds")]
        loses = [d[6:] for d in deltas if d.startswith("loses")]
        parts = []
        if adds:
            parts.append("adds " + ", ".join(adds))
        if loses:
            parts.append("thins " + ", ".join(loses))
        summary = ("; ".join(parts).capitalize() + ".") if parts else "No positional count change from players."
    else:
        summary = "No positional count change from the players in this trade."

    return {
        "summary": summary,
        "notes": notes or ["No additional roster-fit flags."],
        "subject_team": owner.get("team_name") or "unknown",
        "counterparty_team": counterparty or "unknown",
        "positional_delta": {pos: after[pos] - before[pos] for pos in ("QB", "RB", "WR", "TE")},
    }


def analyze_trade(
    give: list[str],
    get: list[str],
    league_id: str | None = None,
    *,
    pick_value_model: str = "schedule",
    include_roster_fit: bool = True,
    slot_estimate: str = "auto",
    pick_overrides: dict[str, Any] | None = None,
    team_name_or_manager: str | None = None,
) -> dict[str, Any]:
    """Compare two sides of a trade, players and/or pick tokens."""
    lid = resolve_league_id(league_id)
    fmt = league_format(lid)
    values = fc_values(fmt)
    if not values:
        return {"error": "no trade values returned", "format": fmt, "source": FC_SOURCE}

    by_name, by_sid = _index_fc(values)
    model = (pick_value_model or "schedule").strip().lower()
    if model not in {"schedule", "manual"}:
        model = "schedule"
    slot_est = (slot_estimate or "auto").strip().lower()
    num_qbs = int(fmt.get("numQbs") or 1)
    num_teams = int(fmt.get("numTeams") or 12)

    umap = None
    standings: dict[int, dict] = {}
    try:
        umap = user_map(lid)
        standings = _standings_rank_map(lid)
    except Exception:
        umap = None

    give_side = _price_side_items(
        give or [],
        by_name=by_name,
        by_sid=by_sid,
        model=model,
        slot_estimate=slot_est,
        num_qbs=num_qbs,
        num_teams=num_teams,
        fc_rows=values,
        overrides=pick_overrides,
        lid=lid,
        umap=umap,
        standings=standings,
    )
    get_side = _price_side_items(
        get or [],
        by_name=by_name,
        by_sid=by_sid,
        model=model,
        slot_estimate=slot_est,
        num_qbs=num_qbs,
        num_teams=num_teams,
        fc_rows=values,
        overrides=pick_overrides,
        lid=lid,
        umap=umap,
        standings=standings,
    )

    verdict, diff, ratio = _verdict(give_side["total"], get_side["total"])
    unpriced = []
    for u in give_side["unpriced"]:
        unpriced.append({**u, "side": "give"})
    for u in get_side["unpriced"]:
        unpriced.append({**u, "side": "get"})

    roster_fit = None
    if include_roster_fit:
        roster_fit = _roster_fit(
            lid=lid,
            subject_query=team_name_or_manager,
            get_player_rows=get_side["players"],
            give_player_rows=give_side["players"],
            fmt=fmt,
        )

    reasons = [
        f"Give total {give_side['total']} vs get total {get_side['total']} "
        f"({round(ratio * 100, 1)}% vs larger side).",
    ]
    if give_side["picks"] or get_side["picks"]:
        reasons.append(
            f"Picks priced with the {model} model "
            f"(give pick value {give_side['pick_total']}, get pick value {get_side['pick_total']})."
        )
    else:
        reasons.append("No draft picks on either side; totals are FantasyCalc player values only.")
    if unpriced:
        reasons.append(f"{len(unpriced)} asset(s) could not be priced — see unpriced_assets.")
    if roster_fit and roster_fit.get("summary"):
        reasons.append("Roster fit: " + roster_fit["summary"])

    state = get_json(f"/state/{SPORT}", cache=True) or {}
    season = str(state.get("season") or state.get("league_season") or "")
    week = state.get("week") or state.get("display_week")

    subject = advice.subject_block()
    try:
        resolved = (
            resolve_roster(lid, team_name_or_manager)
            if team_name_or_manager
            else resolve_my_roster(lid)
        )
        if resolved:
            subject = advice.subject_block(
                team_name=(resolved["owner"] or {}).get("team_name"),
                manager=(resolved["owner"] or {}).get("display_name"),
                roster_id=resolved["roster"].get("roster_id"),
            )
    except Exception:
        pass

    limitations = [
        PICK_LIMITATION,
        "Player values are FantasyCalc estimates for this league's format and move daily.",
    ]
    if model == "manual":
        limitations.append("manual model: listed pick_overrides replace schedule values for those tokens only.")

    return {
        "league_id": lid,
        "platform": "sleeper",
        "format": fmt,
        "as_of": advice.as_of_block(season=season, week=week),
        "subject": subject,
        "source": FC_SOURCE,
        "give": give_side["players"],
        "give_total": give_side["total"],
        "get": get_side["players"],
        "get_total": get_side["total"],
        "difference": diff,
        "percent_vs_larger_side": round(ratio * 100, 1),
        "verdict": verdict,
        "unmatched": {"give": give_side["missing"], "get": get_side["missing"]},
        "players_give": give_side["players"],
        "players_get": get_side["players"],
        "picks": {
            "model": model,
            "give": give_side["picks"],
            "get": get_side["picks"],
        },
        "roster_fit": roster_fit,
        "unpriced_assets": unpriced,
        "recommendations": [],
        "reasons": reasons,
        "data_sources": ["fantasycalc", "sleeper"],
        "limitations": limitations,
        "unofficial": True,
        "note": (
            "Player values are FantasyCalc estimates. Draft picks use the in-repo "
            "schedule heuristic (not FantasyCalc quotes)."
        ),
    }

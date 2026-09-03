"""
Team grade: contender / rebuilder classification plus next-move suggestions.

This is an MCP advice tool, not a DraftLab artifact. It reports a classification
from FantasyCalc roster totals, starter quality vs the league, and pick capital.
It does not produce a CeilingScore or replace value_my_roster.
"""

from __future__ import annotations

from typing import Any

from . import advice, picks as pick_mod
from .config import FC_SOURCE, SPORT
from .http import get_json
from .league import (
    compute_standings,
    resolve_league_id,
    resolve_my_roster,
    resolve_roster,
    user_map,
)
from .players import load_players, player_name
from .values import fc_values, league_format

SKILL = ("QB", "RB", "WR", "TE")
CLASSIFICATIONS = (
    "championship_contender",
    "playoff_hopeful",
    "mid_pack",
    "rebuilder",
    "tank",
)
FUTURE_YEARS = 3
DRAFT_ROUNDS = 4


def letter_from_score(score: float) -> str:
    bands = (
        (93, "A+"),
        (87, "A"),
        (83, "A-"),
        (77, "B+"),
        (73, "B"),
        (67, "B-"),
        (63, "C+"),
        (57, "C"),
        (53, "C-"),
        (45, "D"),
    )
    for thresh, grade in bands:
        if score >= thresh:
            return grade
    return "F"


def letter_from_rank(rank: int, n: int) -> str:
    if n <= 1:
        return "B"
    pct = 100.0 * (1.0 - (rank - 1) / max(n - 1, 1))
    return letter_from_score(pct)


def owned_picks(
    roster_id: int,
    traded: list[dict],
    seasons: list[str],
    rounds: int = DRAFT_ROUNDS,
) -> list[dict[str, Any]]:
    """Original picks minus traded-away, plus traded-in.

    Sleeper traded_picks: roster_id is the original owner, owner_id is current.
    Unmoved picks are not listed, so they stay with the original roster.
    """
    rid = int(roster_id)
    mine: set[tuple[str, int, int]] = {
        (str(season), rnd, rid)
        for season in seasons
        for rnd in range(1, rounds + 1)
    }
    for p in traded or []:
        try:
            season = str(p.get("season"))
            rnd = int(p.get("round") or 0)
            original = int(p.get("roster_id"))
            current = int(p.get("owner_id"))
        except (TypeError, ValueError):
            continue
        if rnd < 1:
            continue
        if original == rid and current != rid:
            mine.discard((season, rnd, rid))
        if current == rid:
            mine.add((season, rnd, original))
    out = [
        {"season": s, "round": r, "original_roster_id": orig}
        for s, r, orig in sorted(mine)
    ]
    return out


def pick_capital(owned: list[dict], num_qbs: int) -> int:
    total = 0
    for p in owned:
        val = pick_mod.static_pick_value(int(p["round"]), "mid", num_qbs) or 0
        total += val
    return total


def _fc_index(values: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for v in values or []:
        sid = (v.get("player") or {}).get("sleeperId")
        if sid:
            out[str(sid)] = v
    return out


def _roster_snapshot(
    roster: dict,
    owner: dict,
    players_map: dict,
    fc_by_sid: dict[str, dict],
    owned_pick_rows: list[dict],
    num_qbs: int,
) -> dict[str, Any]:
    pids = [str(p) for p in (roster.get("players") or [])]
    starters = [str(p) for p in (roster.get("starters") or []) if p]
    pos_value = {pos: 0.0 for pos in SKILL}
    pos_names: dict[str, list[str]] = {pos: [] for pos in SKILL}
    ages = []
    total = 0.0
    starter_value = 0.0
    for pid in pids:
        rec = player_name(pid, players_map)
        info = players_map.get(pid) or {}
        fc = fc_by_sid.get(pid) or {}
        val = float(fc.get("value") or 0)
        total += val
        pos = (rec.get("position") or "").upper()
        if pos in pos_value:
            pos_value[pos] += val
            if rec.get("name"):
                pos_names[pos].append(rec["name"])
        if pid in starters:
            starter_value += val
        age = info.get("age") or (fc.get("player") or {}).get("maybeAge")
        try:
            if age:
                ages.append(float(age))
        except (TypeError, ValueError):
            pass
    youth = 0.0
    if ages:
        avg_age = sum(ages) / len(ages)
        # Younger than 25 → 100; older than 30 → ~20.
        youth = max(0.0, min(100.0, (30.0 - avg_age) / 5.0 * 100.0 / 2 + 50))
    else:
        avg_age = None
        youth = 50.0
    capital = pick_capital(owned_pick_rows, num_qbs)
    return {
        "roster_id": roster.get("roster_id"),
        "team_name": owner.get("team_name"),
        "manager": owner.get("display_name"),
        "total_value": total,
        "starter_value": starter_value,
        "pos_value": pos_value,
        "pos_names": pos_names,
        "avg_age": round(avg_age, 1) if avg_age is not None else None,
        "youth": youth,
        "pick_capital": capital,
        "picks": owned_pick_rows,
        "player_ids": pids,
        "starters": starters,
    }


def _rank(values: list[float], mine: float, reverse: bool = True) -> int:
    ordered = sorted(values, reverse=reverse)
    for i, v in enumerate(ordered, start=1):
        if v == mine:
            return i
    return len(ordered) or 1


def _pct_from_rank(rank: int, n: int) -> float:
    if n <= 1:
        return 50.0
    return 100.0 * (1.0 - (rank - 1) / (n - 1))


def classify(
    *,
    value_rank: int,
    starter_rank: int,
    pick_rank: int,
    n: int,
    horizon: str,
    pick_capital_value: int,
    median_picks: float,
) -> str:
    """Map ranks onto the five FRD classifications. Every team gets one."""
    top = max(1, n // 6) or 1  # 2 in a 12-team
    if horizon == "win_now":
        primary, secondary = starter_rank, value_rank
    else:
        primary, secondary = value_rank, starter_rank

    if primary <= top and secondary <= n // 3 + 1:
        return "championship_contender"
    if primary <= n // 3:
        return "playoff_hopeful"
    if primary <= n // 2 + 1:
        return "mid_pack"
    # Bottom half: tank if the pick pile is clearly above median, else rebuilder.
    if primary >= n - top + 1 and pick_capital_value >= median_picks:
        return "tank"
    if pick_rank <= n // 3 and value_rank >= n // 2:
        return "tank"
    return "rebuilder"


def _pick_blurb(owned: list[dict], roster_id: int) -> str:
    firsts = [p for p in owned if p["round"] == 1]
    missing_own_first = not any(
        p["round"] == 1 and p["original_roster_id"] == roster_id for p in owned
    )
    bits = []
    if firsts:
        bits.append(
            "own "
            + ", ".join(
                f"{p['season']} {'mid ' if p['original_roster_id'] == roster_id else 'acquired '}1st"
                for p in firsts
            )
        )
    else:
        bits.append("no 1sts currently")
    if missing_own_first:
        bits.append("missing original 1st" if firsts else "including own 1st")
    return "; ".join(bits)


def _next_moves(snap: dict, classification: str, pos_grades: dict, horizon: str) -> list[dict]:
    weakest = sorted(
        ((pos, pos_grades[pos]) for pos in SKILL),
        key=lambda kv: kv[1]["score"],
    )
    strongest = list(reversed(weakest))
    moves: list[dict] = []

    if weakest and strongest and weakest[0][1]["score"] < 60 and strongest[0][1]["score"] >= 70:
        wpos, winfo = weakest[0]
        spos, sinfo = strongest[0]
        names = ", ".join((sinfo.get("players") or [])[:2]) or spos
        moves.append(
            {
                "priority": len(moves) + 1,
                "type": "trade",
                "advice": f"Package {spos} depth ({names}) toward a {wpos} upgrade",
                "reasons": [
                    f"{wpos} is the weakest unit ({winfo['grade']})",
                    f"{spos} is a relative surplus ({sinfo['grade']})",
                ],
            }
        )

    if classification in {"tank", "rebuilder"}:
        moves.append(
            {
                "priority": len(moves) + 1,
                "type": "trade",
                "advice": (
                    "Sell aging win-now pieces for picks / young shares"
                    if horizon == "dynasty"
                    else "Hold competitive starters; avoid emptying the roster"
                ),
                "reasons": [
                    f"Classification is {classification}",
                    "Dynasty horizon values pick capital and youth over a push"
                    if horizon == "dynasty"
                    else "Win-now horizon still needs 2-year starter quality",
                ],
            }
        )
    elif classification in {"championship_contender", "playoff_hopeful"}:
        wpos = weakest[0][0] if weakest else "WR"
        moves.append(
            {
                "priority": len(moves) + 1,
                "type": "trade",
                "advice": f"Buy a {wpos} starter; spending a mid-round pick is on the table",
                "reasons": [
                    f"Classification is {classification}",
                    f"{wpos} is the unit most likely to block a weekly lineup",
                ],
            }
        )

    wpos = weakest[0][0] if weakest else "RB"
    moves.append(
        {
            "priority": len(moves) + 1,
            "type": "fa",
            "advice": f"Check waiver_advice for {wpos} — do not spend FAAB blindly",
            "reasons": [
                f"{wpos} is the weakest positional grade",
                "waiver_advice ranks the actual wire against this roster",
            ],
        }
    )

    # Hold young capital when rebuilding; hold studs when contending.
    if classification in {"tank", "rebuilder"}:
        hold = "Hold young assets and acquired 1sts; do not panic-cut for a week-1 starter"
    else:
        hold = "Hold the elite core; do not move a top-36 piece without a clear starter back"
    moves.append(
        {
            "priority": len(moves) + 1,
            "type": "hold",
            "advice": hold,
            "reasons": [
                f"Overall value {int(snap['total_value'])}",
                f"Pick capital {snap['pick_capital']}",
            ],
        }
    )

    # Cap at 3, keep at least 1, unique types preferred.
    trimmed = []
    seen = set()
    for m in moves:
        if m["type"] in seen and len(trimmed) >= 2:
            continue
        seen.add(m["type"])
        m["priority"] = len(trimmed) + 1
        trimmed.append(m)
        if len(trimmed) == 3:
            break
    if not trimmed:
        trimmed = moves[:3]
    for i, m in enumerate(trimmed, start=1):
        m["priority"] = i
    return trimmed[:3]


def grade_team(
    league_id: str | None = None,
    *,
    team_name_or_manager: str | None = None,
    horizon: str = "dynasty",
) -> dict[str, Any]:
    lid = resolve_league_id(league_id)
    league = get_json(f"/league/{lid}", cache=True) or {}
    state = get_json(f"/state/{SPORT}", cache=True) or {}
    season = str(league.get("season") or state.get("season") or state.get("league_season") or "")
    week = state.get("week") or state.get("display_week")
    fmt = league_format(lid)
    horizon_n = (horizon or "dynasty").strip().lower()
    if horizon_n not in {"dynasty", "win_now"}:
        horizon_n = "dynasty"

    resolved = (
        resolve_roster(lid, team_name_or_manager)
        if team_name_or_manager
        else resolve_my_roster(lid)
    )
    if not resolved:
        return {"error": "could not resolve team", "query": team_name_or_manager, "league_id": lid}

    values = fc_values(fmt)
    if not values:
        return {"error": "no trade values returned", "format": fmt, "source": FC_SOURCE}
    fc_by_sid = _fc_index(values)
    players_map = load_players()
    umap = user_map(lid)
    rosters = get_json(f"/league/{lid}/rosters", cache=True) or []
    traded = get_json(f"/league/{lid}/traded_picks") or []
    try:
        current_year = int(season) if season else int(state.get("league_season") or 2026)
    except (TypeError, ValueError):
        current_year = 2026
    future = [str(current_year + i) for i in range(0, FUTURE_YEARS)]
    # If we're pre-draft, include this season's pick; if the draft is done,
    # owned_picks still lists it and the manager can ignore a spent pick.
    num_qbs = int(fmt.get("numQbs") or 1)

    snaps = []
    for roster in rosters:
        owner = umap.get(roster.get("owner_id"), {})
        rid = roster.get("roster_id")
        if rid is None:
            continue
        owned = owned_picks(int(rid), traded, future, DRAFT_ROUNDS)
        snaps.append(
            _roster_snapshot(roster, owner, players_map, fc_by_sid, owned, num_qbs)
        )

    if not snaps:
        return {"error": "no rosters", "league_id": lid}

    totals = [s["total_value"] for s in snaps]
    starters = [s["starter_value"] for s in snaps]
    capitals = [s["pick_capital"] for s in snaps]
    youths = [s["youth"] for s in snaps]
    n = len(snaps)
    median_picks = sorted(capitals)[n // 2]

    target_id = resolved["roster"].get("roster_id")
    target = next((s for s in snaps if s["roster_id"] == target_id), None)
    if not target:
        return {"error": "resolved roster missing from league snapshot", "league_id": lid}

    # Rank every team so positional grades are league-relative.
    for snap in snaps:
        snap["value_rank"] = _rank(totals, snap["total_value"])
        snap["starter_rank"] = _rank(starters, snap["starter_value"])
        snap["pick_rank"] = _rank(capitals, snap["pick_capital"])
        snap["youth_rank"] = _rank(youths, snap["youth"])
        if horizon_n == "win_now":
            snap["composite"] = (
                0.20 * _pct_from_rank(snap["value_rank"], n)
                + 0.55 * _pct_from_rank(snap["starter_rank"], n)
                + 0.15 * _pct_from_rank(snap["starter_rank"], n)
                + 0.10 * _pct_from_rank(snap["pick_rank"], n)
            )
        else:
            snap["composite"] = (
                0.40 * _pct_from_rank(snap["value_rank"], n)
                + 0.20 * _pct_from_rank(snap["starter_rank"], n)
                + 0.25 * _pct_from_rank(snap["pick_rank"], n)
                + 0.15 * _pct_from_rank(snap["youth_rank"], n)
            )
        snap["classification"] = classify(
            value_rank=snap["value_rank"],
            starter_rank=snap["starter_rank"],
            pick_rank=snap["pick_rank"],
            n=n,
            horizon=horizon_n,
            pick_capital_value=snap["pick_capital"],
            median_picks=median_picks,
        )

    # Refresh target after ranking.
    target = next(s for s in snaps if s["roster_id"] == target_id)

    pos_grades = {}
    for pos in SKILL:
        league_vals = [s["pos_value"][pos] for s in snaps]
        rank = _rank(league_vals, target["pos_value"][pos])
        names = target["pos_names"][pos][:4]
        detail_names = ", ".join(names) if names else "no players"
        pos_grades[pos] = {
            "grade": letter_from_rank(rank, n),
            "rank": rank,
            "value": round(target["pos_value"][pos]),
            "detail": f"{detail_names} (#{rank}/{n} in {pos} value)",
            "players": target["pos_names"][pos],
            "score": _pct_from_rank(rank, n),
        }
    pick_rank = target["pick_rank"]
    pos_grades["picks"] = {
        "grade": letter_from_rank(pick_rank, n),
        "rank": pick_rank,
        "value": target["pick_capital"],
        "detail": _pick_blurb(target["picks"], int(target["roster_id"])),
        "score": _pct_from_rank(pick_rank, n),
    }

    next_moves = _next_moves(target, target["classification"], pos_grades, horizon_n)
    standings = compute_standings(lid)
    rec_rank = None
    for row in standings:
        if row.get("roster_id") == target_id:
            rec_rank = row.get("rank")
            break

    reasons = [
        f"League value rank {target['value_rank']}/{n} (total {int(target['total_value'])}).",
        f"Starter-value rank {target['starter_rank']}/{n}.",
        f"Pick-capital rank {target['pick_rank']}/{n} ({target['pick_capital']} schedule points).",
        f"Horizon={horizon_n}; classification={target['classification']}.",
    ]
    if rec_rank:
        reasons.append(f"Current standings rank {rec_rank}/{n}.")
    if target["avg_age"] is not None:
        reasons.append(f"Roster average age {target['avg_age']}.")

    verdict = (
        f"{target['team_name'] or 'This team'} grades {letter_from_score(target['composite'])} "
        f"({target['classification'].replace('_', ' ')}). "
        + (next_moves[0]["advice"] if next_moves else "")
    )

    return advice.advice_envelope(
        league_id=lid,
        platform="sleeper",
        fmt=fmt,
        season=season,
        week=week,
        subject=advice.subject_block(
            team_name=target["team_name"],
            manager=target["manager"],
            roster_id=target["roster_id"],
        ),
        verdict=verdict,
        reasons=reasons,
        data_sources=["fantasycalc", "sleeper"],
        limitations=[
            "Classification is a heuristic from FantasyCalc totals, starter value, "
            "youth, and in-repo pick-schedule capital — not a projection.",
            "Pick capital uses the same static schedule as analyze_trade, not FantasyCalc pick quotes.",
            "next_moves are suggestions; use waiver_advice / analyze_trade for the actual claim or trade math.",
        ],
        recommendations=next_moves,
        extra={
            "classification": target["classification"],
            "grade": letter_from_score(target["composite"]),
            "overall_value": round(target["total_value"]),
            "league_value_rank": target["value_rank"],
            "teams_in_league": n,
            "horizon": horizon_n,
            "positional_grades": {
                k: {"grade": v["grade"], "detail": v["detail"]}
                for k, v in pos_grades.items()
            },
            "next_moves": next_moves,
            "source": FC_SOURCE,
        },
    )

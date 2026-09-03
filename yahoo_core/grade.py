"""Yahoo best-effort grade_team. Draft-pick capital is unsupported."""

from __future__ import annotations

from typing import Any

from sleeper_core import advice, grade as sleeper_grade
from sleeper_core import values as sleeper_values
from sleeper_core.config import FC_SOURCE, SPORT
from sleeper_core.http import get_json as sleeper_get_json

from .league import (
    _config_error,
    compute_league,
    compute_my_team,
    compute_rosters,
    resolve_league_key,
    scout_team,
)
from .values import league_format


def _team_value(entry: dict[str, Any], fc_by_sid: dict[str, dict]) -> dict[str, Any]:
    pos_value = {pos: 0.0 for pos in sleeper_grade.SKILL}
    pos_names: dict[str, list[str]] = {pos: [] for pos in sleeper_grade.SKILL}
    total = 0.0
    starter_value = 0.0
    for row in list(entry.get("starters") or []):
        sid = row.get("sleeper_id")
        val = float((fc_by_sid.get(str(sid)) or {}).get("value") or 0) if sid else 0.0
        total += val
        starter_value += val
        pos = (row.get("position") or "").upper()
        if pos in pos_value:
            pos_value[pos] += val
            if row.get("name"):
                pos_names[pos].append(row["name"])
    for row in list(entry.get("bench") or []):
        sid = row.get("sleeper_id")
        val = float((fc_by_sid.get(str(sid)) or {}).get("value") or 0) if sid else 0.0
        total += val
        pos = (row.get("position") or "").upper()
        if pos in pos_value:
            pos_value[pos] += val
            if row.get("name"):
                pos_names[pos].append(row["name"])
    return {
        "roster_id": entry.get("roster_id") or entry.get("team_key"),
        "team_name": entry.get("owner"),
        "manager": entry.get("manager"),
        "total_value": total,
        "starter_value": starter_value,
        "pos_value": pos_value,
        "pos_names": pos_names,
        "avg_age": None,
        "youth": 50.0,
        "pick_capital": 0,
        "picks": [],
    }


def grade_team(
    league_key: str | None = None,
    *,
    team_name_or_manager: str | None = None,
    horizon: str = "dynasty",
) -> dict[str, Any]:
    lid = resolve_league_key(league_key)
    if not lid:
        return _config_error("YAHOO_LEAGUE_KEY is not configured")

    league = compute_league(lid)
    if isinstance(league, dict) and league.get("error"):
        return league

    if team_name_or_manager:
        report = scout_team(team_name_or_manager, lid)
    else:
        report = compute_my_team(lid)
    if isinstance(report, dict) and report.get("error"):
        return report

    fmt = league_format(lid)
    if fmt.get("error"):
        return fmt
    horizon_n = (horizon or "dynasty").strip().lower()
    if horizon_n not in {"dynasty", "win_now"}:
        horizon_n = "dynasty"

    fc_raw = sleeper_values.fc_values(fmt) or []
    fc_by_sid: dict[str, dict] = {}
    for v in fc_raw:
        sid = (v.get("player") or {}).get("sleeperId")
        if sid:
            fc_by_sid[str(sid)] = v

    rosters = compute_rosters(lid, include_players=True)
    if isinstance(rosters, dict) and rosters.get("error"):
        return rosters

    snaps = [_team_value(entry, fc_by_sid) for entry in rosters]
    n = len(snaps) or 1
    totals = [s["total_value"] for s in snaps]
    starters = [s["starter_value"] for s in snaps]
    target_key = report.get("roster_id") or report.get("team_key") or report.get("owner")
    target = next(
        (
            s
            for s in snaps
            if s["roster_id"] == target_key or s["team_name"] == report.get("owner")
        ),
        None,
    )
    if not target:
        target = _team_value(report, fc_by_sid)
        snaps.append(target)
        n = len(snaps)
        totals = [s["total_value"] for s in snaps]
        starters = [s["starter_value"] for s in snaps]

    for snap in snaps:
        snap["value_rank"] = sleeper_grade._rank(totals, snap["total_value"])
        snap["starter_rank"] = sleeper_grade._rank(starters, snap["starter_value"])
        snap["pick_rank"] = n
        snap["youth_rank"] = n // 2 or 1
        snap["classification"] = sleeper_grade.classify(
            value_rank=snap["value_rank"],
            starter_rank=snap["starter_rank"],
            pick_rank=snap["pick_rank"],
            n=n,
            horizon=horizon_n,
            pick_capital_value=0,
            median_picks=0,
        )
        if horizon_n == "win_now":
            snap["composite"] = (
                0.30 * sleeper_grade._pct_from_rank(snap["value_rank"], n)
                + 0.70 * sleeper_grade._pct_from_rank(snap["starter_rank"], n)
            )
        else:
            snap["composite"] = (
                0.55 * sleeper_grade._pct_from_rank(snap["value_rank"], n)
                + 0.45 * sleeper_grade._pct_from_rank(snap["starter_rank"], n)
            )

    target = next(
        (
            s
            for s in snaps
            if s["roster_id"] == target_key or s["team_name"] == report.get("owner")
        ),
        snaps[0],
    )

    pos_grades = {}
    for pos in sleeper_grade.SKILL:
        league_vals = [s["pos_value"][pos] for s in snaps]
        rank = sleeper_grade._rank(league_vals, target["pos_value"][pos])
        names = ", ".join(target["pos_names"][pos][:4]) or "no players"
        pos_grades[pos] = {
            "grade": sleeper_grade.letter_from_rank(rank, n),
            "detail": f"{names} (#{rank}/{n} in {pos} value)",
            "score": sleeper_grade._pct_from_rank(rank, n),
            "players": target["pos_names"][pos],
        }
    pos_grades["picks"] = {
        "grade": "N/A",
        "detail": "Yahoo has no Sleeper-style traded-picks feed; pick capital omitted.",
        "score": 50.0,
        "players": [],
    }

    next_moves = sleeper_grade._next_moves(
        {**target, "pick_capital": 0},
        target["classification"],
        pos_grades,
        horizon_n,
    )

    state = sleeper_get_json(f"/state/{SPORT}", cache=True) or {}
    season = str(state.get("season") or league.get("season") or "")
    week = state.get("week")

    verdict = (
        f"{target['team_name'] or 'This team'} grades "
        f"{sleeper_grade.letter_from_score(target['composite'])} "
        f"({target['classification'].replace('_', ' ')})."
    )
    return advice.advice_envelope(
        league_id=lid,
        platform="yahoo",
        fmt=fmt,
        season=season,
        week=week,
        subject=advice.subject_block(
            team_name=target["team_name"],
            manager=target["manager"],
            roster_id=target["roster_id"],
        ),
        verdict=verdict,
        reasons=[
            f"League value rank {target['value_rank']}/{n} (total {int(target['total_value'])}).",
            "Yahoo pick capital is omitted (unsupported).",
        ],
        data_sources=["fantasycalc", "yahoo"],
        limitations=[
            "Yahoo pick ownership cannot be priced; classification ignores draft capital.",
            "Yahoo dynasty is not auto-detected; pass horizon explicitly if needed.",
            "Players without a sleeper_id crosswalk contribute 0 value.",
        ],
        recommendations=next_moves,
        extra={
            "classification": target["classification"],
            "grade": sleeper_grade.letter_from_score(target["composite"]),
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

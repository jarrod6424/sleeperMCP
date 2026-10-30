"""Yahoo best-effort grade_team. Draft-pick capital is unsupported."""

from __future__ import annotations

from typing import Any

from sleeper_core import advice, grade as sleeper_grade
from sleeper_core import projections as sleeper_proj
from sleeper_core import sos as sleeper_sos
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
    sleeper_roster_slots,
)
from .parse import to_int
from .values import league_format

# Redraft classifications, keyed to the playoff line rather than to dynasty
# ideas like rebuilding or tanking, which a one-season league has no use for.
REDRAFT_CLASSIFICATIONS = ("contender", "bubble", "long_shot")

# Yahoo statuses meaning "cannot be in a lineup" (injured reserve, not active,
# PUP, suspended). Weekly designations like Q and O are not excluded.
UNAVAILABLE_STATUSES = {"IR", "NA", "PUP-P", "PUP-R", "SUSP"}


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
    horizon: str | None = None,
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
    if horizon is None:
        # Grade a redraft league on this season, not rest-of-career value.
        horizon_n = "dynasty" if fmt.get("isDynasty") else "win_now"
        horizon_source = "league_format"
    else:
        horizon_n = horizon.strip().lower()
        horizon_source = "explicit"
        if horizon_n not in {"dynasty", "win_now"}:
            return _config_error(
                "unknown horizon", horizon=horizon, valid_horizons=["dynasty", "win_now"]
            )

    fc_raw = sleeper_values.fc_values(fmt) or []
    fc_by_sid: dict[str, dict] = {}
    for v in fc_raw:
        sid = (v.get("player") or {}).get("sleeperId")
        if sid:
            fc_by_sid[str(sid)] = v

    rosters = compute_rosters(lid, include_players=True)
    if isinstance(rosters, dict) and rosters.get("error"):
        return rosters

    if horizon_n == "win_now" and not fmt.get("isDynasty"):
        return _redraft_grade(
            lid, league, report, fmt, fc_by_sid, rosters, horizon_source=horizon_source
        )

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
            # "tank" means a bottom team whose pick pile beats the league
            # median. Yahoo pick capital is unknown, so it must never clear
            # that bar; 0 >= 0 used to make every bottom-two team a "tank".
            pick_capital_value=0,
            median_picks=float("inf"),
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
            "horizon_source": horizon_source,
            "positional_grades": {
                k: {"grade": v["grade"], "detail": v["detail"]}
                for k, v in pos_grades.items()
            },
            "next_moves": next_moves,
            "source": FC_SOURCE,
        },
    )


def classify_redraft(rank: int, n: int, playoff_teams: int) -> str:
    """contender / bubble / long_shot by blended rank around the playoff line.

    With 6 of 12 making it: 1-4 contender, 5-8 bubble (the two either side of
    the line), 9-12 long shot.
    """
    if rank <= max(1, playoff_teams - 2):
        return "contender"
    if rank <= min(n, playoff_teams + 2):
        return "bubble"
    return "long_shot"


def _player_row(row: dict, fc_by_sid: dict, allowed: dict, opponents: dict) -> dict[str, Any]:
    sid = row.get("sleeper_id")
    position = (row.get("position") or "").split(",")[0].strip().upper()
    value = float((fc_by_sid.get(str(sid)) or {}).get("value") or 0) if sid else 0.0
    schedule = sleeper_sos.schedule_factor(row.get("team"), position, allowed, opponents)
    # Unknown schedules keep their plain value; the null factor says so.
    multiplier = schedule["multiplier"] if schedule["multiplier"] is not None else 1.0
    return {
        "player_id": str(sid or row.get("player_key") or row.get("name")),
        "name": row.get("name"),
        "position": position,
        "nfl_team": row.get("team"),
        "value": value,
        "adj_value": round(value * multiplier, 1),
        "schedule": schedule,
        # Can't be started now; still counts toward roster value, since an IR
        # player returns later and FantasyCalc already prices the injury.
        "unavailable": row.get("selected_position") == "IR"
        or (row.get("status") or "").upper() in UNAVAILABLE_STATUSES,
    }


def _redraft_snapshot(entry: dict, fc_by_sid: dict, allowed: dict, opponents: dict,
                      slots: list[str]) -> dict[str, Any]:
    pool = [
        _player_row(row, fc_by_sid, allowed, opponents)
        for row in list(entry.get("starters") or []) + list(entry.get("bench") or [])
    ]
    # Best legal lineup by schedule-adjusted value, not whoever is slotted in
    # this week: a stud left on the bench still makes the team strong.
    lineup = sleeper_proj.optimal_lineup(
        slots, [p for p in pool if not p["unavailable"]], score_key="adj_value"
    )
    pos_value = {pos: 0.0 for pos in sleeper_grade.SKILL}
    pos_names: dict[str, list[str]] = {pos: [] for pos in sleeper_grade.SKILL}
    for p in sorted(pool, key=lambda r: r["adj_value"], reverse=True):
        if p["position"] in pos_value:
            pos_value[p["position"]] += p["adj_value"]
            pos_names[p["position"]].append(p["name"])
    pos_schedule = {}
    for pos in sleeper_grade.SKILL:
        factors = [p["schedule"]["factor"] for p in lineup
                   if p["position"] == pos and p["schedule"]["factor"] is not None]
        pos_schedule[pos] = round(sum(factors) / len(factors), 4) if factors else None
    return {
        "roster_id": entry.get("roster_id") or entry.get("team_key"),
        "team_name": entry.get("owner"),
        "manager": entry.get("manager"),
        "wins": entry.get("wins") or 0,
        "losses": entry.get("losses") or 0,
        "ties": entry.get("ties") or 0,
        "points_for": entry.get("points_for") or 0.0,
        "raw_value": sum(p["value"] for p in pool),
        "total_value": sum(p["adj_value"] for p in pool),
        "starter_value": sum(p["adj_value"] for p in lineup),
        "lineup": lineup,
        "pos_value": pos_value,
        "pos_names": pos_names,
        "pos_schedule": pos_schedule,
    }


def _schedule_note(factor: float | None) -> str:
    if factor is None:
        return "schedule n/a"
    pct = round((factor - 1.0) * 100)
    if pct == 0:
        return "schedule average"
    return f"schedule {abs(pct)}% {'softer' if pct > 0 else 'tougher'} than average"


def _redraft_moves(target: dict, classification: str, pos_grades: dict) -> list[dict]:
    ranked = sorted(sleeper_grade.SKILL, key=lambda pos: pos_grades[pos]["score"])
    weakest, strongest = ranked[0], ranked[-1]
    moves: list[dict] = []
    if pos_grades[weakest]["score"] < 60 and pos_grades[strongest]["score"] >= 70:
        names = ", ".join(pos_grades[strongest]["players"][:2]) or strongest
        moves.append({
            "type": "trade",
            "advice": f"Package {strongest} depth ({names}) toward a {weakest} upgrade",
            "reasons": [f"{weakest} is the weakest unit ({pos_grades[weakest]['grade']})",
                        f"{strongest} is a relative surplus ({pos_grades[strongest]['grade']})"],
        })
    pitch = {
        "contender": f"Buy a {weakest} starter for the playoff run",
        "bubble": f"Upgrade {weakest} now; the playoff line is within reach",
        "long_shot": f"Take upside swings at {weakest}; only wins move a long shot",
    }[classification]
    moves.append({"type": "trade", "advice": pitch,
                  "reasons": [f"Classification is {classification}",
                              f"{weakest} is the weakest positional grade"]})
    toughest = min(
        (pos for pos in sleeper_grade.SKILL if target["pos_schedule"][pos] is not None),
        key=lambda pos: target["pos_schedule"][pos],
        default=None,
    )
    if toughest and target["pos_schedule"][toughest] < 0.97:
        moves.append({"type": "fa",
                      "advice": f"Plan {toughest} streaming or depth around a tough schedule",
                      "reasons": [f"{toughest} starters: {_schedule_note(target['pos_schedule'][toughest])}"]})
    moves.append({"type": "fa", "advice": f"Check waiver_advice for {weakest} before bidding",
                  "reasons": ["waiver_advice ranks the actual wire against this roster"]})
    for i, move in enumerate(moves, start=1):
        move["priority"] = i
    return moves


def _redraft_grade(
    lid: str,
    league: dict,
    report: dict,
    fmt: dict,
    fc_by_sid: dict,
    rosters: list[dict],
    *,
    horizon_source: str,
) -> dict[str, Any]:
    """Redraft grade: rest-of-season roster strength blended with results.

    roster score       30% total value rank + 70% best-lineup value rank, both
                       scaled by each player's remaining positional schedule
    performance score  50% points-for rank + 50% wins rank (points break ties)
    blend              performance weight = games played / regular-season weeks
    """
    settings = league.get("settings") or {}
    season = str(league.get("season") or "")
    current_week = to_int(league.get("current_week"), 1)
    start_week = to_int(league.get("start_week"), 1) or 1
    playoff_start = to_int(settings.get("playoff_start_week"), 15) or 15
    end_week = to_int(league.get("end_week"), playoff_start + 2) or playoff_start + 2
    playoff_teams = to_int(settings.get("num_playoff_teams"), 6) or 6
    reception_points = league.get("reception_points")
    if reception_points is None:
        reception_points = fmt.get("ppr", 1.0)

    allowed = sleeper_sos.points_allowed(season, float(reception_points))
    opponents = sleeper_sos.remaining_opponents(season, current_week, end_week)
    slots = [s for s in sleeper_roster_slots(league.get("roster_positions"))
             if s not in sleeper_proj.SKIP_SLOTS]

    snaps = [_redraft_snapshot(e, fc_by_sid, allowed, opponents, slots) for e in rosters]
    n = len(snaps)
    regular_weeks = max(1, playoff_start - start_week)
    for snap in snaps:
        played = snap["wins"] + snap["losses"] + snap["ties"]
        snap["value_rank"] = sleeper_grade._rank([s["total_value"] for s in snaps], snap["total_value"])
        snap["starter_rank"] = sleeper_grade._rank([s["starter_value"] for s in snaps], snap["starter_value"])
        snap["pf_rank"] = sleeper_grade._rank([s["points_for"] for s in snaps], snap["points_for"])
        record = [(s["wins"] + 0.5 * s["ties"], s["points_for"]) for s in snaps]
        snap["wins_rank"] = sleeper_grade._rank(record, (snap["wins"] + 0.5 * snap["ties"], snap["points_for"]))
        snap["roster_score"] = (0.30 * sleeper_grade._pct_from_rank(snap["value_rank"], n)
                                + 0.70 * sleeper_grade._pct_from_rank(snap["starter_rank"], n))
        snap["performance_score"] = (0.5 * sleeper_grade._pct_from_rank(snap["pf_rank"], n)
                                     + 0.5 * sleeper_grade._pct_from_rank(snap["wins_rank"], n))
        snap["performance_weight"] = min(1.0, played / regular_weeks)
        snap["composite"] = ((1 - snap["performance_weight"]) * snap["roster_score"]
                             + snap["performance_weight"] * snap["performance_score"])
    composites = [s["composite"] for s in snaps]
    for snap in snaps:
        snap["overall_rank"] = sleeper_grade._rank(composites, snap["composite"])
        snap["classification"] = classify_redraft(snap["overall_rank"], n, playoff_teams)

    target_key = report.get("roster_id") or report.get("team_key")
    target = next((s for s in snaps if s["roster_id"] == target_key
                   or s["team_name"] == report.get("owner")), snaps[0])

    pos_grades = {}
    for pos in sleeper_grade.SKILL:
        rank = sleeper_grade._rank([s["pos_value"][pos] for s in snaps], target["pos_value"][pos])
        names = ", ".join(target["pos_names"][pos][:4]) or "no players"
        pos_grades[pos] = {
            "grade": sleeper_grade.letter_from_rank(rank, n),
            "detail": f"{names} (#{rank}/{n} in {pos} value; "
                      f"{_schedule_note(target['pos_schedule'][pos])})",
            "score": sleeper_grade._pct_from_rank(rank, n),
            "players": target["pos_names"][pos],
            "schedule_factor": target["pos_schedule"][pos],
        }
    next_moves = _redraft_moves(target, target["classification"], pos_grades)

    grade = sleeper_grade.letter_from_score(target["composite"])
    w = target["performance_weight"]
    return advice.advice_envelope(
        league_id=lid,
        platform="yahoo",
        fmt=fmt,
        season=season,
        week=current_week,
        subject=advice.subject_block(
            team_name=target["team_name"], manager=target["manager"], roster_id=target["roster_id"],
        ),
        verdict=f"{target['team_name'] or 'This team'} grades {grade} "
                f"({target['classification'].replace('_', ' ')}, #{target['overall_rank']}/{n}).",
        reasons=[
            f"Roster: best-lineup value #{target['starter_rank']}/{n}, total value "
            f"#{target['value_rank']}/{n}, schedule-adjusted for weeks {current_week}-{end_week}.",
            f"Results: points for #{target['pf_rank']}/{n}, record #{target['wins_rank']}/{n} "
            f"({target['wins']}-{target['losses']}{'-' + str(target['ties']) if target['ties'] else ''}).",
            f"Blend: {round(100 * (1 - w))}% roster, {round(100 * w)}% results "
            f"({round(w * regular_weeks)} of {regular_weeks} regular-season weeks played).",
        ],
        data_sources=["fantasycalc", "yahoo", "nflverse"],
        limitations=[
            "Player values are FantasyCalc redraft market values scaled by schedule, not projections.",
            f"Schedule strength is fantasy points allowed by position through week "
            f"{allowed['through_week']}, shrunk toward league average by "
            f"{int(sleeper_sos.SHRINK_GAMES)} games; league bonus scoring is not modelled.",
            "K and DEF carry no FantasyCalc value and no schedule factor.",
            "Players without a sleeper_id crosswalk contribute 0 value.",
        ],
        recommendations=next_moves,
        extra={
            "classification": target["classification"],
            "grade": grade,
            "model": "redraft",
            "overall_rank": target["overall_rank"],
            "overall_value": round(target["raw_value"]),
            "schedule_adjusted_value": round(target["total_value"]),
            "league_value_rank": target["value_rank"],
            "teams_in_league": n,
            "horizon": "win_now",
            "horizon_source": horizon_source,
            "redraft": {
                "roster_score": round(target["roster_score"], 1),
                "performance_score": round(target["performance_score"], 1),
                "performance_weight": round(w, 3),
                "composite": round(target["composite"], 1),
                "best_lineup_rank": target["starter_rank"],
                "points_for_rank": target["pf_rank"],
                "record_rank": target["wins_rank"],
                "schedule_weeks": [current_week, end_week],
                "schedule_through_week": allowed["through_week"],
                "playoff_teams": playoff_teams,
            },
            "best_lineup": [
                {"slot": p["slot"], "name": p["name"], "position": p["position"],
                 "value": round(p["value"]), "adj_value": round(p["adj_value"]),
                 "schedule_factor": p["schedule"]["factor"], "byes_left": p["schedule"]["byes"]}
                for p in target["lineup"]
            ],
            "positional_grades": {
                k: {"grade": v["grade"], "detail": v["detail"], "schedule_factor": v["schedule_factor"]}
                for k, v in pos_grades.items()
            },
            "next_moves": next_moves,
            "source": FC_SOURCE,
        },
    )

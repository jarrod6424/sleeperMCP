"""
Yahoo start/sit advice using Sleeper projections + the ID crosswalk.

Yahoo provides the roster and slots; Sleeper provides weekly projections.
Players without a sleeper_id join are listed under unmatched and excluded
from the optimization pool.
"""

from __future__ import annotations

from typing import Any

from sleeper_core import advice as sleeper_advice
from sleeper_core import projections as sleeper_proj
from sleeper_core import start_sit as sleeper_start_sit
from sleeper_core.http import get_json as sleeper_get_json

from .config import YAHOO_SCORING_FORMAT
from .league import (
    _config_error,
    compute_league,
    resolve_league_key,
    scout_team,
    compute_my_team,
)

_SCORING_MAP = {
    "ppr": ("pts_ppr", "PPR"),
    "half": ("pts_half_ppr", "Half-PPR"),
    "half_ppr": ("pts_half_ppr", "Half-PPR"),
    "half-ppr": ("pts_half_ppr", "Half-PPR"),
    "std": ("pts_std", "Standard"),
    "standard": ("pts_std", "Standard"),
}


def scoring_field(override: str | None = None) -> tuple[str, str]:
    key = (override or YAHOO_SCORING_FORMAT or "ppr").strip().lower()
    return _SCORING_MAP.get(key, ("pts_ppr", "PPR"))


def _primary_position(position: str | None) -> str | None:
    if not position:
        return None
    return position.split(",")[0].strip().upper() or None


def start_sit_advice(
    *,
    week: int | None = None,
    team_name_or_manager: str | None = None,
    league_key: str | None = None,
    scoring_format: str | None = None,
    strategy: str = "balanced",
) -> dict[str, Any]:
    lid = resolve_league_key(league_key)
    if not lid:
        return _config_error("YAHOO_LEAGUE_KEY is not configured")

    if team_name_or_manager:
        report = scout_team(team_name_or_manager, lid)
    else:
        report = compute_my_team(lid)
    if isinstance(report, dict) and report.get("error"):
        return report

    league = compute_league(lid)
    if isinstance(league, dict) and league.get("error"):
        return league

    state = sleeper_get_json("/state/nfl", cache=True) or {}
    week = week or to_int_safe(league.get("current_week")) or state.get("week") or 1
    season = str(state.get("season") or state.get("league_season") or league.get("season") or "")
    if not season:
        return {
            "error": "could not determine current season",
            "platform": "yahoo",
            "league_key": lid,
        }

    field, scoring_label = scoring_field(scoring_format)
    if scoring_format is None and league.get("scoring_format_label"):
        label = str(league["scoring_format_label"]).lower()
        if "half" in label:
            field, scoring_label = "pts_half_ppr", "Half-PPR"
        elif label == "ppr":
            field, scoring_label = "pts_ppr", "PPR"
        elif label == "standard":
            field, scoring_label = "pts_std", "Standard"
        scoring_format_note = "Detected from Yahoo settings.stat_modifiers (receptions)."
    else:
        scoring_format_note = (
            "Yahoo scoring modifiers are not fully parsed yet; "
            "set YAHOO_SCORING_FORMAT=ppr|half_ppr|std to match your league."
            if league.get("reception_points") is None
            else "Using explicit scoring_format override."
        )

    proj = sleeper_proj.projections_for(season, int(week))
    if not proj:
        return sleeper_start_sit.projection_failure(
            league_id=lid,
            platform="yahoo",
            team_name=report.get("owner"),
            week=week,
            season=season,
            extra={"scoring_format_note": scoring_format_note},
        )

    starters_raw = report.get("starters") or []
    bench_raw = report.get("bench") or []
    rostered = list(starters_raw) + list(bench_raw)

    unmatched: list[dict[str, Any]] = []
    pool: list[dict[str, Any]] = []
    for row in rostered:
        sleeper_id = row.get("sleeper_id")
        position = _primary_position(row.get("position"))
        if not sleeper_id or not position:
            unmatched.append(
                {
                    "name": row.get("name"),
                    "yahoo_player_id": row.get("player_id"),
                    "position": row.get("position"),
                    "reason": "no sleeper_id" if not sleeper_id else "no position",
                    "crosswalk_matched_by": row.get("crosswalk_matched_by"),
                }
            )
            continue
        pool.append(
            {
                "player_id": str(sleeper_id),
                "yahoo_player_id": row.get("player_id"),
                "name": row.get("name"),
                "position": position,
                "selected_position": row.get("selected_position"),
                "team": row.get("team") or row.get("editorial_team_abbr"),
                "injury_status": row.get("status"),
                "proj": sleeper_proj.proj_points(str(sleeper_id), proj, field),
            }
        )

    pool_by_id = {p["player_id"]: p for p in pool}
    slots = [
        s
        for s in (league.get("roster_positions") or [])
        if s not in sleeper_proj.SKIP_SLOTS
    ]
    if not slots:
        return {
            "error": "no active roster slots found in Yahoo league settings",
            "platform": "yahoo",
            "league_key": lid,
        }

    current_starter_ids = []
    for row in starters_raw:
        sid = row.get("sleeper_id")
        if sid and str(sid) in pool_by_id:
            current_starter_ids.append(str(sid))

    from .values import league_format

    league_fmt = league_format(lid)
    if isinstance(league_fmt, dict) and league_fmt.get("error"):
        league_fmt = {"isDynasty": False, "numQbs": 1, "ppr": 1, "numTeams": 12}

    result = sleeper_start_sit.build_from_pool(
        league_id=lid,
        platform="yahoo",
        fmt=league_fmt,
        season=season,
        week=int(week),
        subject=sleeper_advice.subject_block(
            team_name=report.get("owner"),
            manager=report.get("manager"),
            roster_id=report.get("roster_id") or report.get("team_key"),
        ),
        scoring_label=scoring_label,
        slots=slots,
        pool=pool,
        current_starter_ids=current_starter_ids,
        strategy=strategy,
        source="Yahoo roster + api.sleeper.com projections via sleeper_id crosswalk (UNDOCUMENTED)",
        extra={
            "scoring_format_note": scoring_format_note,
            "unmatched_players": unmatched,
        },
        note=(
            "Projections come from an undocumented Sleeper endpoint and are "
            "estimates. Lineup is a greedy heuristic, not a guaranteed optimum. "
            "Players without a Sleeper ID join are listed in unmatched_players."
        ),
    )
    return result


def to_int_safe(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None

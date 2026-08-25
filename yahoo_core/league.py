"""
Yahoo league-scoped reads normalized toward the Sleeper tool shapes.

Phase 1 covers redraft essentials: league metadata, rosters, standings, and
my team (record + rank + this week's matchup when available).
"""

from __future__ import annotations

import os
from typing import Any

from .config import DEFAULT_LEAGUE_KEY, DEFAULT_TEAM_NAME
from .http import YahooConfigError, get_json
from .parse import (
    indexed_items,
    league_metadata,
    league_subresource,
    merge_dicts,
    player_display_name,
    roster_players,
    selected_slot,
    team_entries,
    to_float,
    to_int,
)


def resolve_league_key(league_key: str | None) -> str:
    default = os.environ.get("YAHOO_LEAGUE_KEY", DEFAULT_LEAGUE_KEY)
    return (league_key or default or "").strip()


def resolve_team_name(team_name: str | None) -> str:
    return (team_name or os.environ.get("YAHOO_TEAM_NAME", DEFAULT_TEAM_NAME) or "").strip()


def _config_error(message: str, **extra: Any) -> dict[str, Any]:
    return {"error": message, "platform": "yahoo", **extra}


def _fetch_league(league_key: str, out: str) -> dict[str, Any]:
    return get_json(f"league/{league_key};out={out}")


def _format_player(player: dict[str, Any]) -> dict[str, Any]:
    slot = selected_slot(player)
    team = player.get("editorial_team_abbr") or player.get("editorial_team_full_name")
    status = player.get("status") or player.get("injury_note")
    return {
        "player_id": str(player.get("player_id") or player.get("player_key") or ""),
        "name": player_display_name(player),
        "position": player.get("display_position") or player.get("primary_position"),
        "team": team,
        "selected_position": slot,
        "status": status or None,
    }


def _split_roster(players: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    starters: list[dict[str, Any]] = []
    bench: list[dict[str, Any]] = []
    for raw in players:
        formatted = _format_player(raw)
        if formatted["selected_position"] in {"BN", "IR"}:
            bench.append(formatted)
        else:
            starters.append(formatted)
    return starters, bench


def _team_record(team: dict[str, Any]) -> dict[str, int | float]:
    standings = team.get("team_standings") or {}
    if isinstance(standings, list):
        standings = standings[0] if standings else {}
    outcomes = standings.get("outcome_totals") or {}
    points = standings.get("points_for") or "0"
    points_against = standings.get("points_against") or "0"
    return {
        "wins": to_int(outcomes.get("wins")),
        "losses": to_int(outcomes.get("losses")),
        "ties": to_int(outcomes.get("ties")),
        "points_for": round(to_float(points), 2),
        "points_against": round(to_float(points_against), 2),
    }


def _manager_name(team: dict[str, Any]) -> str | None:
    managers = team.get("managers")
    for item in indexed_items(managers):
        manager = item.get("manager") if isinstance(item, dict) else item
        if isinstance(manager, dict):
            return manager.get("nickname") or manager.get("guid")
        if isinstance(manager, list):
            for block in manager:
                if isinstance(block, dict):
                    return block.get("nickname") or block.get("guid")
    return None


def _format_team_entry(team: dict[str, Any], include_players: bool) -> dict[str, Any]:
    record = _team_record(team)
    entry: dict[str, Any] = {
        "platform": "yahoo",
        "roster_id": str(team.get("team_id") or ""),
        "team_key": team.get("team_key"),
        "owner": team.get("name") or "unknown",
        "manager": _manager_name(team),
        **record,
    }
    if include_players:
        players = roster_players(team.get("roster"))
        starters, bench = _split_roster(players)
        entry["starters"] = starters
        entry["bench"] = bench
    return entry


def compute_league(league_key: str | None = None) -> dict[str, Any]:
    lid = resolve_league_key(league_key)
    if not lid:
        return _config_error(
            "YAHOO_LEAGUE_KEY is not configured",
            hint="Set YAHOO_LEAGUE_KEY to your league key, e.g. 461.l.12345",
        )
    try:
        payload = _fetch_league(lid, "metadata,settings")
    except YahooConfigError as exc:
        return _config_error(str(exc))

    meta = league_metadata(payload)
    if not meta:
        return _config_error("league not found", league_key=lid)

    settings_node = league_subresource(payload, "settings")
    settings_blocks = settings_node if isinstance(settings_node, list) else [settings_node]
    settings = merge_dicts(*(b for b in settings_blocks if isinstance(b, dict)))

    roster_positions = []
    roster_positions_node = settings.get("roster_positions")
    for item in indexed_items(roster_positions_node):
        pos = item.get("roster_position") if isinstance(item, dict) else item
        if isinstance(pos, dict):
            count = to_int(pos.get("count"), 1)
            abbr = pos.get("position") or pos.get("abbreviation")
            if abbr:
                roster_positions.extend([abbr] * count)

    return {
        "platform": "yahoo",
        "league_key": meta.get("league_key") or lid,
        "league_id": meta.get("league_id"),
        "name": meta.get("name"),
        "season": meta.get("season"),
        "num_teams": to_int(meta.get("num_teams")),
        "current_week": to_int(meta.get("current_week")),
        "is_finished": meta.get("is_finished"),
        "url": meta.get("url"),
        "scoring_type": settings.get("scoring_type"),
        "draft_type": settings.get("draft_type"),
        "roster_positions": roster_positions,
        "settings": {
            "waiver_type": settings.get("waiver_type"),
            "trade_end_date": settings.get("trade_end_date"),
            "playoff_start_week": settings.get("playoff_start_week"),
            "num_playoff_teams": settings.get("num_playoff_teams"),
        },
    }


def compute_rosters(league_key: str | None = None, include_players: bool = True) -> list[dict] | dict:
    lid = resolve_league_key(league_key)
    if not lid:
        return _config_error("YAHOO_LEAGUE_KEY is not configured")
    try:
        payload = _fetch_league(lid, "teams,standings,rosters")
    except YahooConfigError as exc:
        return _config_error(str(exc))

    teams = team_entries(payload)
    if not teams:
        return _config_error("no teams returned", league_key=lid)

    return [_format_team_entry(team, include_players) for team in teams]


def compute_standings(league_key: str | None = None) -> list[dict] | dict:
    rosters = compute_rosters(league_key, include_players=False)
    if isinstance(rosters, dict) and "error" in rosters:
        return rosters
    ranked = sorted(
        rosters,
        key=lambda row: (row["wins"], row["points_for"]),
        reverse=True,
    )
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
    return ranked


def resolve_my_team(
    league_key: str | None = None,
    team_name: str | None = None,
) -> dict[str, Any] | None:
    lid = resolve_league_key(league_key)
    if not lid:
        return None
    target = resolve_team_name(team_name).strip().lower()
    if not target:
        return None

    rosters = compute_rosters(lid, include_players=False)
    if isinstance(rosters, dict):
        return None

    for row in rosters:
        owner = (row.get("owner") or "").strip().lower()
        manager = (row.get("manager") or "").strip().lower()
        if target in {owner, manager}:
            matched_by = "team_name" if target == owner else "manager"
            return {"team_key": row.get("team_key"), "roster_id": row.get("roster_id"), "matched_by": matched_by}
    return None


def _matchup_nodes(scoreboard_node: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for item in indexed_items(scoreboard_node):
        matchup = item.get("matchup") if isinstance(item, dict) else item
        if isinstance(matchup, dict):
            nodes.append(matchup)
        else:
            nodes.extend(m for m in indexed_items(matchup) if isinstance(m, dict))
    return nodes


def _matchup_for(league_key: str, week: int, team_key: str) -> dict[str, Any] | None:
    try:
        payload = get_json(f"league/{league_key}/scoreboard;week={week}")
    except YahooConfigError:
        return None

    for matchup in _matchup_nodes(league_subresource(payload, "scoreboard")):
        teams_node = matchup.get("teams") if isinstance(matchup, dict) else None
        teams: list[dict[str, Any]] = []
        for team_item in indexed_items(teams_node):
            team_list = team_item.get("team") if isinstance(team_item, dict) else team_item
            blocks = team_list if isinstance(team_list, list) else [team_list]
            team_meta = merge_dicts(*(b for b in blocks if isinstance(b, dict)))
            if team_meta:
                teams.append(team_meta)
        if not any(t.get("team_key") == team_key for t in teams):
            continue
        me = next(t for t in teams if t.get("team_key") == team_key)
        opp = next((t for t in teams if t.get("team_key") != team_key), None)
        points = me.get("team_points") or {}
        opp_points = (opp or {}).get("team_points") or {}
        return {
            "week": week,
            "points": round(to_float(points.get("total")), 2) if points else None,
            "opponent": (opp or {}).get("name") or "BYE",
            "opponent_points": round(to_float(opp_points.get("total")), 2) if opp_points else None,
            "opponent_team_key": (opp or {}).get("team_key"),
        }
    return None


def compute_my_team(
    league_key: str | None = None,
    team_name: str | None = None,
) -> dict[str, Any]:
    lid = resolve_league_key(league_key)
    if not lid:
        return _config_error(
            "YAHOO_LEAGUE_KEY is not configured",
            tried_team_name=resolve_team_name(team_name),
        )

    resolved = resolve_my_team(lid, team_name=team_name)
    if not resolved:
        return _config_error(
            "could not find that team in this league",
            league_key=lid,
            tried_team_name=resolve_team_name(team_name),
        )

    rosters = compute_rosters(lid, include_players=True)
    if isinstance(rosters, dict):
        return rosters

    entry = next((r for r in rosters if r.get("team_key") == resolved["team_key"]), None)
    if not entry:
        return _config_error("team roster not found", league_key=lid, **resolved)

    entry = dict(entry)
    entry["matched_by"] = resolved["matched_by"]

    standings = compute_standings(lid)
    if isinstance(standings, dict):
        return standings
    for row in standings:
        if row.get("team_key") == resolved["team_key"]:
            entry["rank"] = row["rank"]
            entry["teams_in_league"] = len(standings)
            break

    league = compute_league(lid)
    week = to_int(league.get("current_week"), 1) if isinstance(league, dict) else 1
    team_key = str(resolved["team_key"])
    entry["this_week"] = _matchup_for(lid, week, team_key)
    entry["next_week"] = _matchup_for(lid, week + 1, team_key)
    return entry

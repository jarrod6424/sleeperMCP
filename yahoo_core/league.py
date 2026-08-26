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
    user_game_leagues,
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


def _format_player(player: dict[str, Any], indexes: dict | None = None) -> dict[str, Any]:
    slot = selected_slot(player)
    team = player.get("editorial_team_abbr") or player.get("editorial_team_full_name")
    status = player.get("status") or player.get("injury_note")
    row = {
        "player_id": str(player.get("player_id") or player.get("player_key") or ""),
        "player_key": player.get("player_key"),
        "name": player_display_name(player),
        "position": player.get("display_position") or player.get("primary_position"),
        "team": team,
        "selected_position": slot,
        "status": status or None,
    }
    try:
        from sleeper_core.crosswalk import yahoo_to_sleeper

        resolved = yahoo_to_sleeper(
            row.get("player_key") or row.get("player_id"),
            name=row.get("name"),
            position=row.get("position"),
            indexes=indexes,
        )
        row["sleeper_id"] = resolved.get("sleeper_id")
        row["crosswalk_matched_by"] = resolved.get("matched_by")
    except Exception:
        row["sleeper_id"] = None
        row["crosswalk_matched_by"] = None
    return row


def _split_roster(
    players: list[dict[str, Any]],
    indexes: dict | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    starters: list[dict[str, Any]] = []
    bench: list[dict[str, Any]] = []
    for raw in players:
        formatted = _format_player(raw, indexes=indexes)
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
        from sleeper_core.crosswalk import build_indexes

        indexes = build_indexes()
        players = roster_players(team.get("roster"))
        starters, bench = _split_roster(players, indexes=indexes)
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
            return {
                "team_key": row.get("team_key"),
                "roster_id": row.get("roster_id"),
                "matched_by": matched_by,
            }
    return None


def resolve_roster(
    league_key: str | None,
    query: str,
) -> dict[str, Any] | None:
    """Resolve any team by team name or manager. Exact match first, then partial."""
    lid = resolve_league_key(league_key)
    q = (query or "").strip().lower()
    if not lid or not q:
        return None

    rosters = compute_rosters(lid, include_players=False)
    if isinstance(rosters, dict):
        return None

    def fields(row: dict[str, Any]) -> list[str]:
        return [
            (row.get("owner") or "").strip().lower(),
            (row.get("manager") or "").strip().lower(),
        ]

    for row in rosters:
        if q in [f for f in fields(row) if f]:
            return {
                "team_key": row.get("team_key"),
                "roster_id": row.get("roster_id"),
                "matched_by": "exact",
                "owner": row.get("owner"),
                "manager": row.get("manager"),
            }

    for row in rosters:
        if any(q in f for f in fields(row) if f):
            return {
                "team_key": row.get("team_key"),
                "roster_id": row.get("roster_id"),
                "matched_by": "partial",
                "owner": row.get("owner"),
                "manager": row.get("manager"),
            }
    return None


def available_team_names(league_key: str | None = None) -> list[str]:
    rosters = compute_rosters(league_key, include_players=False)
    if isinstance(rosters, dict):
        return []
    return [str(r.get("owner") or r.get("manager") or "") for r in rosters if r.get("owner") or r.get("manager")]


def _team_report(lid: str, team_key: str, matched_by: str) -> dict[str, Any]:
    rosters = compute_rosters(lid, include_players=True)
    if isinstance(rosters, dict):
        return rosters

    entry = next((r for r in rosters if r.get("team_key") == team_key), None)
    if not entry:
        return _config_error("team roster not found", league_key=lid, team_key=team_key)

    entry = dict(entry)
    entry["matched_by"] = matched_by

    standings = compute_standings(lid)
    if isinstance(standings, dict):
        return standings
    for row in standings:
        if row.get("team_key") == team_key:
            entry["rank"] = row["rank"]
            entry["teams_in_league"] = len(standings)
            break

    league = compute_league(lid)
    week = to_int(league.get("current_week"), 1) if isinstance(league, dict) else 1
    entry["this_week"] = _matchup_for(lid, week, str(team_key))
    entry["next_week"] = _matchup_for(lid, week + 1, str(team_key))
    return entry


def _matchup_nodes(scoreboard_node: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for item in indexed_items(scoreboard_node):
        matchup = item.get("matchup") if isinstance(item, dict) else item
        if isinstance(matchup, dict):
            nodes.append(matchup)
        else:
            nodes.extend(m for m in indexed_items(matchup) if isinstance(m, dict))
    return nodes


def _teams_from_matchup(matchup: dict[str, Any]) -> list[dict[str, Any]]:
    teams_node = matchup.get("teams") if isinstance(matchup, dict) else None
    teams: list[dict[str, Any]] = []
    for team_item in indexed_items(teams_node):
        team_list = team_item.get("team") if isinstance(team_item, dict) else team_item
        blocks = team_list if isinstance(team_list, list) else [team_list]
        team_meta = merge_dicts(*(b for b in blocks if isinstance(b, dict)))
        if team_meta:
            teams.append(team_meta)
    return teams


def _matchup_for(league_key: str, week: int, team_key: str) -> dict[str, Any] | None:
    try:
        payload = get_json(f"league/{league_key}/scoreboard;week={week}")
    except YahooConfigError:
        return None

    for matchup in _matchup_nodes(league_subresource(payload, "scoreboard")):
        teams = _teams_from_matchup(matchup)
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


def compute_matchups(
    league_key: str | None = None,
    week: int | None = None,
) -> dict[str, Any]:
    """Full weekly scoreboard, shaped like Sleeper's get_matchups."""
    lid = resolve_league_key(league_key)
    if not lid:
        return _config_error("YAHOO_LEAGUE_KEY is not configured")

    if week is None:
        league = compute_league(lid)
        if isinstance(league, dict) and league.get("error"):
            return league
        week = to_int(league.get("current_week"), 1)

    try:
        payload = get_json(f"league/{lid}/scoreboard;week={int(week)}")
    except YahooConfigError as exc:
        return _config_error(str(exc))

    matchups: list[dict[str, Any]] = []
    for index, matchup in enumerate(
        _matchup_nodes(league_subresource(payload, "scoreboard")), start=1
    ):
        teams_raw = _teams_from_matchup(matchup)
        teams = []
        for team in teams_raw:
            points = team.get("team_points") or {}
            team_key = str(team.get("team_key") or "")
            teams.append(
                {
                    "roster_id": team_key.split(".t.")[-1] if ".t." in team_key else team_key,
                    "team_key": team_key,
                    "team": team.get("name") or "unknown",
                    "points": round(to_float(points.get("total")), 2) if points else None,
                }
            )
        matchups.append(
            {
                "matchup_id": to_int(matchup.get("week"), week) * 100 + index
                if matchup.get("week") is not None
                else index,
                "week": to_int(matchup.get("week"), week),
                "teams": teams,
            }
        )
    return {
        "platform": "yahoo",
        "league_id": lid,
        "league_key": lid,
        "week": int(week),
        "matchups": matchups,
    }


def compute_managers(league_key: str | None = None) -> list[dict] | dict:
    """Managers / team owners in a Yahoo league."""
    rosters = compute_rosters(league_key, include_players=False)
    if isinstance(rosters, dict):
        return rosters
    managers: list[dict[str, Any]] = []
    for row in rosters:
        managers.append(
            {
                "platform": "yahoo",
                "user_id": row.get("team_key"),
                "username": row.get("manager"),
                "display_name": row.get("manager"),
                "team_name": row.get("owner"),
                "roster_id": row.get("roster_id"),
                "team_key": row.get("team_key"),
                "is_commissioner": None,
            }
        )
    return managers


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
    return _team_report(lid, str(resolved["team_key"]), resolved["matched_by"])


def scout_team(
    team_name_or_manager: str,
    league_key: str | None = None,
) -> dict[str, Any]:
    lid = resolve_league_key(league_key)
    if not lid:
        return _config_error(
            "YAHOO_LEAGUE_KEY is not configured",
            query=team_name_or_manager,
        )

    resolved = resolve_roster(lid, team_name_or_manager)
    if not resolved:
        return {
            "error": "no team matched",
            "platform": "yahoo",
            "query": team_name_or_manager,
            "available_teams": available_team_names(lid),
            "league_key": lid,
        }
    return _team_report(lid, str(resolved["team_key"]), resolved["matched_by"])


def _transaction_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    node = league_subresource(payload, "transactions")
    entries: list[dict[str, Any]] = []
    for item in indexed_items(node):
        blocks = item.get("transaction") if isinstance(item, dict) else item
        if isinstance(blocks, dict) and "transaction_key" in blocks:
            entries.append(blocks)
            continue
        meta = merge_dicts(
            *(b for b in (blocks if isinstance(blocks, list) else [blocks]) if isinstance(b, dict) and "transaction_key" in b)
        )
        players_node = None
        for block in (blocks if isinstance(blocks, list) else [blocks]):
            if isinstance(block, dict) and "players" in block:
                players_node = block["players"]
                break
        if meta:
            if players_node is not None:
                meta = {**meta, "players": players_node}
            entries.append(meta)
    return entries


def _transaction_players(players_node: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in indexed_items(players_node):
        player_blocks = item.get("player") if isinstance(item, dict) else item
        blocks = player_blocks if isinstance(player_blocks, list) else [player_blocks]
        player: dict[str, Any] = {}
        txn_data = None
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if "player_id" in block or "name" in block or "player_key" in block:
                player.update(block)
            if "transaction_data" in block:
                txn_data = block["transaction_data"]
        if txn_data is not None:
            player["transaction_data"] = txn_data
        if player:
            out.append(player)
    return out


def _format_transaction(raw: dict[str, Any]) -> dict[str, Any]:
    players = _transaction_players(raw.get("players"))
    adds: list[dict[str, Any]] = []
    drops: list[dict[str, Any]] = []
    teams: list[str] = []
    for player in players:
        data = player.get("transaction_data") or {}
        if isinstance(data, list):
            data = data[0] if data else {}
        action = (data.get("type") or "").lower()
        rec = {
            "player_id": str(player.get("player_id") or player.get("player_key") or ""),
            "name": player_display_name(player),
            "position": player.get("display_position"),
            "team": player.get("editorial_team_abbr"),
        }
        if action == "add":
            rec["to_team"] = data.get("destination_team_name")
            adds.append(rec)
            if data.get("destination_team_name"):
                teams.append(data["destination_team_name"])
        elif action == "drop":
            rec["from_team"] = data.get("source_team_name")
            drops.append(rec)
            if data.get("source_team_name"):
                teams.append(data["source_team_name"])
        else:
            # trades and other moves — attach both sides when present
            if data.get("destination_team_name"):
                rec["to_team"] = data.get("destination_team_name")
                teams.append(data["destination_team_name"])
                adds.append(rec)
            if data.get("source_team_name"):
                drop_rec = dict(rec)
                drop_rec["from_team"] = data.get("source_team_name")
                drops.append(drop_rec)
                teams.append(data["source_team_name"])

    # Trade metadata often sits on the transaction itself.
    for key in ("trader_team_name", "tradee_team_name"):
        if raw.get(key):
            teams.append(str(raw[key]))

    created = to_int(raw.get("timestamp"), 0)
    return {
        "platform": "yahoo",
        "type": raw.get("type"),
        "status": raw.get("status"),
        "week": None,
        "created": created,
        "transaction_key": raw.get("transaction_key"),
        "teams": sorted(set(t for t in teams if t)),
        "adds": adds,
        "drops": drops,
        "faab_bid": to_int(raw.get("faab_bid"), 0) or None,
    }


def compute_transactions(
    league_key: str | None = None,
    *,
    count: int = 40,
) -> list[dict] | dict:
    """Recent league transactions (adds/drops/trades), newest first.

    Yahoo does not bucket transactions by NFL week the way Sleeper does, so
    this returns the most recent `count` moves league-wide.
    """
    lid = resolve_league_key(league_key)
    if not lid:
        return _config_error("YAHOO_LEAGUE_KEY is not configured")
    try:
        # types filter keeps commissioner's notes out of the activity feed.
        path = f"league/{lid}/transactions;types=add,drop,trade"
        if count and count > 0:
            path = f"{path};count={int(count)}"
        payload = get_json(path)
    except YahooConfigError as exc:
        return _config_error(str(exc))

    formatted = [_format_transaction(raw) for raw in _transaction_entries(payload)]
    formatted.sort(key=lambda row: row.get("created") or 0, reverse=True)
    return formatted


def recent_moves(
    league_key: str | None = None,
    weeks: int = 3,
) -> list[dict] | dict:
    """Approximate Sleeper recent_moves: last N weeks of activity by count."""
    count = max(10, int(weeks) * 12)
    return compute_transactions(league_key, count=count)


def _draft_result_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    node = league_subresource(payload, "draft_results")
    if node is None:
        node = league_subresource(payload, "draftresults")
    entries: list[dict[str, Any]] = []
    for item in indexed_items(node):
        result = item.get("draft_result") if isinstance(item, dict) else item
        if isinstance(result, list):
            result = merge_dicts(*(b for b in result if isinstance(b, dict)))
        if isinstance(result, dict) and (result.get("pick") is not None or result.get("player_key")):
            entries.append(result)
    return entries


def _players_collection(payload: dict[str, Any]) -> list[dict[str, Any]]:
    node = league_subresource(payload, "players")
    # Some responses nest players under fantasy_content.league[1].players
    # while others put a bare players collection on fantasy_content.
    if node is None:
        from .parse import fantasy_root

        node = fantasy_root(payload).get("players")
    out: list[dict[str, Any]] = []
    for item in indexed_items(node):
        player_blocks = item.get("player") if isinstance(item, dict) else item
        blocks = player_blocks if isinstance(player_blocks, list) else [player_blocks]
        player = merge_dicts(*(b for b in blocks if isinstance(b, dict)))
        if player:
            out.append(player)
    return out


def _fetch_players_by_keys(league_key: str, keys: list[str]) -> dict[str, dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    unique = [k for k in dict.fromkeys(keys) if k]
    for i in range(0, len(unique), 25):
        chunk = unique[i : i + 25]
        path = f"league/{league_key}/players;player_keys={','.join(chunk)}"
        try:
            payload = get_json(path)
        except YahooConfigError:
            continue
        for player in _players_collection(payload):
            key = str(player.get("player_key") or "")
            if key:
                by_key[key] = player
    return by_key


def list_drafts(league_key: str | None = None) -> list[dict] | dict:
    """Yahoo has one draft per league — return a synthetic draft entry."""
    league = compute_league(league_key)
    if isinstance(league, dict) and league.get("error"):
        return league
    lid = league.get("league_key") or resolve_league_key(league_key)
    return [
        {
            "platform": "yahoo",
            "draft_id": lid,
            "league_id": lid,
            "league_key": lid,
            "name": league.get("name"),
            "season": league.get("season"),
            "status": league.get("draft_type"),
            "draft_type": league.get("draft_type"),
            "note": "Yahoo draft picks are keyed by league_key; pass this draft_id to get_draft_picks",
        }
    ]


def compute_draft_picks(league_key: str | None = None) -> list[dict] | dict:
    """Full draft board for a Yahoo league, ordered by pick number."""
    lid = resolve_league_key(league_key)
    if not lid:
        return _config_error("YAHOO_LEAGUE_KEY is not configured")
    try:
        payload = get_json(f"league/{lid}/draftresults")
    except YahooConfigError as exc:
        return _config_error(str(exc))

    results = _draft_result_entries(payload)
    team_names: dict[str, str] = {}
    rosters = compute_rosters(lid, include_players=False)
    if isinstance(rosters, list):
        for row in rosters:
            if row.get("team_key"):
                team_names[str(row["team_key"])] = str(row.get("owner") or row["team_key"])

    player_keys = [str(r.get("player_key")) for r in results if r.get("player_key")]
    players = _fetch_players_by_keys(lid, player_keys)

    picks: list[dict[str, Any]] = []
    for raw in results:
        player_key = str(raw.get("player_key") or "")
        player = players.get(player_key, {})
        team_key = str(raw.get("team_key") or "")
        picks.append(
            {
                "platform": "yahoo",
                "pick_no": to_int(raw.get("pick")),
                "round": to_int(raw.get("round")),
                "draft_slot": None,
                "roster_id": team_key.split(".t.")[-1] if ".t." in team_key else team_key,
                "team_key": team_key,
                "team": team_names.get(team_key),
                "player": player_display_name(player) if player else player_key,
                "player_id": str(player.get("player_id") or player_key),
                "player_key": player_key,
                "position": player.get("display_position") or player.get("primary_position"),
                "nfl_team": player.get("editorial_team_abbr"),
                "cost": raw.get("cost"),
                "is_keeper": None,
            }
        )
    picks.sort(key=lambda row: row.get("pick_no") or 0)
    try:
        from sleeper_core.crosswalk import enrich_with_sleeper_ids

        picks = enrich_with_sleeper_ids(
            picks, yahoo_id_key="player_id", name_key="player", position_key="position"
        )
    except Exception:
        pass
    return picks


def compute_available_players(
    league_key: str | None = None,
    position: str | None = None,
    limit: int = 25,
) -> list[dict] | dict:
    """Available / free-agent players in a Yahoo league, ranked by overall rank.

    Yahoo caps each page at 25; this paginates with `start` until `limit`.
    """
    lid = resolve_league_key(league_key)
    if not lid:
        return _config_error("YAHOO_LEAGUE_KEY is not configured")

    pos = position.upper().strip() if position else None
    if pos == "DEF":
        pos = "DEF"
    wanted = max(1, min(int(limit), 200))
    out: list[dict[str, Any]] = []
    start = 0
    while len(out) < wanted:
        page = min(25, wanted - len(out))
        parts = [
            f"league/{lid}/players",
            "status=A",
            "sort=OR",
            f"count={page}",
            f"start={start}",
        ]
        if pos:
            parts.insert(1, f"position={pos}")
        # Yahoo uses semicolon-delimited filters after the resource.
        path = parts[0] + ";" + ";".join(parts[1:])
        try:
            payload = get_json(path)
        except YahooConfigError as exc:
            return _config_error(str(exc))

        page_players = _players_collection(payload)
        if not page_players:
            break
        for player in page_players:
            row = {
                "platform": "yahoo",
                "player_id": str(player.get("player_id") or player.get("player_key") or ""),
                "player_key": player.get("player_key"),
                "name": player_display_name(player),
                "position": player.get("display_position") or player.get("primary_position"),
                "team": player.get("editorial_team_abbr"),
                "status": player.get("status"),
                "search_rank": None,
            }
            out.append(row)
            if len(out) >= wanted:
                break
        if len(page_players) < page:
            break
        start += len(page_players)

    try:
        from sleeper_core.crosswalk import enrich_with_sleeper_ids

        out = enrich_with_sleeper_ids(out)
    except Exception:
        pass
    for index, row in enumerate(out, start=1):
        row["search_rank"] = index
    return out


def list_user_leagues(season: str | None = None) -> dict[str, Any]:
    """Leagues the authenticated Yahoo user belongs to (NFL by default).

    Uses users;use_login=1/games/leagues. Optional season filters to leagues
    whose game season matches (string year, e.g. "2025").
    """
    try:
        payload = get_json("users;use_login=1/games/leagues")
    except YahooConfigError as exc:
        return _config_error(str(exc))

    default_key = resolve_league_key(None)
    year = (season or "").strip() or None
    leagues: list[dict[str, Any]] = []
    for raw in user_game_leagues(payload):
        game_code = (raw.get("game_code") or "").lower()
        if game_code and game_code != "nfl":
            continue
        league_season = str(raw.get("season") or raw.get("game_season") or "")
        if year and league_season and league_season != year:
            continue
        league_key = raw.get("league_key")
        leagues.append(
            {
                "platform": "yahoo",
                "league_id": league_key,
                "league_key": league_key,
                "name": raw.get("name"),
                "season": league_season or None,
                "status": raw.get("draft_status"),
                "num_teams": to_int(raw.get("num_teams")) or None,
                "is_default": bool(default_key and league_key == default_key),
                "scoring_type": raw.get("scoring_type"),
                "league_type": raw.get("league_type"),
            }
        )
    return {
        "platform": "yahoo",
        "season": year,
        "leagues": leagues,
    }

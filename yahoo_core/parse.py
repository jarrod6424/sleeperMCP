"""
Helpers for Yahoo's nested JSON: numbered dicts, list-of-dict resources, counts.

Yahoo's fantasy API returns JSON that mirrors its XML tree. Collections are
objects with keys "0", "1", … plus "count". Resources are often a list where
index 0 is metadata and later entries hold sub-resources.
"""

from __future__ import annotations

from typing import Any


def fantasy_root(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the fantasy_content object or {}."""
    return payload.get("fantasy_content") or {}


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def indexed_items(node: Any) -> list[Any]:
    """Expand a Yahoo numbered collection into a list of values."""
    if not isinstance(node, dict):
        return as_list(node)
    out: list[Any] = []
    count = node.get("count")
    if count is not None:
        for i in range(int(count)):
            key = str(i)
            if key in node:
                out.append(node[key])
        return out
    for key, value in node.items():
        if key == "count":
            continue
        if key.isdigit():
            out.append(value)
    out.sort(key=lambda item: 0)
    return out


def merge_dicts(*dicts: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for d in dicts:
        if isinstance(d, dict):
            merged.update(d)
    return merged


def league_blocks(payload: dict[str, Any]) -> list[Any]:
    return as_list(fantasy_root(payload).get("league"))


def league_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    blocks = league_blocks(payload)
    if not blocks:
        return {}
    first = blocks[0]
    return first if isinstance(first, dict) else {}


def league_subresource(payload: dict[str, Any], name: str) -> Any:
    for block in league_blocks(payload):
        if isinstance(block, dict) and name in block:
            return block[name]
    return None


def team_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten team list entries from a teams sub-resource."""
    teams_node = league_subresource(payload, "teams")
    entries: list[dict[str, Any]] = []
    for item in indexed_items(teams_node):
        team_list = item.get("team") if isinstance(item, dict) else None
        if team_list is None and isinstance(item, dict) and "team_key" in item:
            entries.append(item)
            continue
        blocks = as_list(team_list)
        meta = merge_dicts(*(b for b in blocks if isinstance(b, dict) and "team_key" in b))
        extras: dict[str, Any] = {}
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for key in ("roster", "team_standings", "managers"):
                if key in block:
                    extras[key] = block[key]
        if meta or extras:
            entries.append({**meta, **extras})
    return entries


def _resource_blocks(node: Any, resource_name: str) -> list[Any]:
    """Normalize a Yahoo resource that may be a dict or a list of blocks."""
    if node is None:
        return []
    if isinstance(node, dict) and resource_name in node:
        return as_list(node[resource_name])
    if isinstance(node, dict) and any(
        k in node for k in ("league_key", "game_key", "guid", "team_key")
    ):
        return [node]
    return as_list(node)


def user_game_leagues(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten leagues from users;use_login=1/games/leagues responses."""
    users_node = fantasy_root(payload).get("users")
    leagues: list[dict[str, Any]] = []
    for user_item in indexed_items(users_node):
        user_blocks = _resource_blocks(user_item, "user")
        games_node = None
        for block in user_blocks:
            if isinstance(block, dict) and "games" in block:
                games_node = block["games"]
                break
        for game_item in indexed_items(games_node):
            game_blocks = _resource_blocks(game_item, "game")
            game_meta = merge_dicts(
                *(
                    b
                    for b in game_blocks
                    if isinstance(b, dict) and ("game_key" in b or "code" in b)
                )
            )
            leagues_node = None
            for block in game_blocks:
                if isinstance(block, dict) and "leagues" in block:
                    leagues_node = block["leagues"]
                    break
            for league_item in indexed_items(leagues_node):
                league_blocks = _resource_blocks(league_item, "league")
                league_meta = merge_dicts(
                    *(
                        b
                        for b in league_blocks
                        if isinstance(b, dict) and "league_key" in b
                    )
                )
                if not league_meta:
                    continue
                leagues.append(
                    {
                        **league_meta,
                        "game_key": game_meta.get("game_key"),
                        "game_code": game_meta.get("code"),
                        "game_season": game_meta.get("season"),
                    }
                )
    return leagues


def roster_players(roster_node: Any) -> list[dict[str, Any]]:
    if not isinstance(roster_node, dict):
        return []
    players_node = roster_node.get("players") or roster_node.get("player")
    out: list[dict[str, Any]] = []
    for item in indexed_items(players_node):
        player_blocks = as_list(item.get("player") if isinstance(item, dict) else item)
        player: dict[str, Any] = {}
        selected_position = None
        for block in player_blocks:
            if not isinstance(block, dict):
                continue
            if "player_id" in block or "name" in block:
                player.update(block)
            if "selected_position" in block:
                selected_position = block["selected_position"]
        if selected_position is not None:
            player["selected_position"] = selected_position
        if player:
            out.append(player)
    return out


def player_display_name(player: dict[str, Any]) -> str:
    if player.get("name"):
        if isinstance(player["name"], dict):
            return player["name"].get("full") or player["name"].get("ascii_first", "")
        return str(player["name"])
    first = player.get("editorial_player_key", "")
    return str(first or player.get("player_id", "unknown"))


def selected_slot(player: dict[str, Any]) -> str:
    pos = player.get("selected_position")
    if isinstance(pos, dict):
        return str(pos.get("position") or "BN")
    return str(pos or "BN")


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

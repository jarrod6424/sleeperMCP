"""
Sleeper ↔ Yahoo player ID crosswalk.

Sleeper's player map carries `yahoo_id` for many (not all) players. Yahoo
player_keys look like `461.p.32692` — the numeric suffix is that yahoo_id.

When yahoo_id is missing (common for some recent rookies), fall back to a
normalized name match, optionally tightened by position.
"""

from __future__ import annotations

import re
from typing import Any

from .players import load_players

_YAHOO_KEY_RE = re.compile(r"(?:^|\.)p\.(\d+)$", re.IGNORECASE)


def normalize_yahoo_id(value: str | int | None) -> str | None:
    """Accept a raw yahoo_id, player_id, or player_key → digit string."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return text
    match = _YAHOO_KEY_RE.search(text)
    if match:
        return match.group(1)
    # Bare "32692" already handled; reject non-numeric keys.
    return None


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _display_name(info: dict[str, Any]) -> str:
    full = info.get("full_name")
    if full:
        return str(full)
    first = info.get("first_name") or ""
    last = info.get("last_name") or ""
    return (first + " " + last).strip()


def build_indexes(players: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build yahoo_id → sleeper_id and name → [sleeper_id] indexes."""
    players = players if players is not None else load_players()
    by_yahoo: dict[str, str] = {}
    by_name: dict[str, list[str]] = {}
    for sleeper_id, info in players.items():
        if not isinstance(info, dict):
            continue
        yahoo = normalize_yahoo_id(info.get("yahoo_id"))
        if yahoo and yahoo not in by_yahoo:
            by_yahoo[yahoo] = str(sleeper_id)

        key = info.get("search_full_name") or normalize_name(_display_name(info))
        if key:
            by_name.setdefault(str(key), []).append(str(sleeper_id))
    return {"by_yahoo": by_yahoo, "by_name": by_name, "players": players}


def _record(sleeper_id: str, info: dict[str, Any], matched_by: str) -> dict[str, Any]:
    yahoo = normalize_yahoo_id(info.get("yahoo_id"))
    return {
        "sleeper_id": sleeper_id,
        "yahoo_id": yahoo,
        "yahoo_player_key_suffix": yahoo,
        "name": _display_name(info),
        "position": info.get("position"),
        "team": info.get("team"),
        "matched_by": matched_by,
    }


def sleeper_to_yahoo(
    sleeper_id: str,
    players: dict[str, Any] | None = None,
) -> dict[str, Any]:
    players = players if players is not None else load_players()
    info = players.get(str(sleeper_id))
    if not info:
        return {
            "error": "sleeper player not found",
            "sleeper_id": sleeper_id,
            "yahoo_id": None,
            "matched_by": None,
        }
    yahoo = normalize_yahoo_id(info.get("yahoo_id"))
    return {
        "sleeper_id": str(sleeper_id),
        "yahoo_id": yahoo,
        "name": _display_name(info),
        "position": info.get("position"),
        "team": info.get("team"),
        "matched_by": "yahoo_id" if yahoo else "missing:no_yahoo_id",
    }


def yahoo_to_sleeper(
    yahoo_id: str | int | None = None,
    *,
    name: str | None = None,
    position: str | None = None,
    indexes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a Yahoo player to a Sleeper id.

    Preference order: yahoo_id exact → name+position → name only.
    """
    indexes = indexes or build_indexes()
    players: dict[str, Any] = indexes["players"]
    by_yahoo: dict[str, str] = indexes["by_yahoo"]
    by_name: dict[str, list[str]] = indexes["by_name"]

    yid = normalize_yahoo_id(yahoo_id)
    if yid and yid in by_yahoo:
        sid = by_yahoo[yid]
        return _record(sid, players[sid], "yahoo_id")

    key = normalize_name(name)
    if key and key in by_name:
        candidates = by_name[key]
        if position:
            pos = position.upper().strip()
            # Yahoo display_position can be "WR,RB" — take primary token.
            pos_primary = pos.split(",")[0].strip()
            narrowed = [
                sid
                for sid in candidates
                if (players.get(sid) or {}).get("position") == pos_primary
            ]
            if len(narrowed) == 1:
                sid = narrowed[0]
                return _record(sid, players[sid], "name+position")
            if len(narrowed) > 1:
                return {
                    "error": "ambiguous name+position match",
                    "yahoo_id": yid,
                    "name": name,
                    "position": position,
                    "candidates": [
                        _record(sid, players[sid], "name+position") for sid in narrowed[:5]
                    ],
                    "matched_by": None,
                }
        if len(candidates) == 1:
            sid = candidates[0]
            return _record(sid, players[sid], "name")
        return {
            "error": "ambiguous name match",
            "yahoo_id": yid,
            "name": name,
            "candidates": [_record(sid, players[sid], "name") for sid in candidates[:5]],
            "matched_by": None,
        }

    return {
        "error": "no crosswalk match",
        "yahoo_id": yid,
        "name": name,
        "position": position,
        "sleeper_id": None,
        "matched_by": None,
    }


def enrich_with_sleeper_ids(
    rows: list[dict[str, Any]],
    *,
    yahoo_id_key: str = "player_id",
    name_key: str = "name",
    position_key: str = "position",
) -> list[dict[str, Any]]:
    """Attach sleeper_id / crosswalk_matched_by onto Yahoo-shaped player rows."""
    indexes = build_indexes()
    out: list[dict[str, Any]] = []
    for row in rows:
        enriched = dict(row)
        resolved = yahoo_to_sleeper(
            row.get("player_key") or row.get(yahoo_id_key),
            name=row.get(name_key),
            position=row.get(position_key),
            indexes=indexes,
        )
        enriched["sleeper_id"] = resolved.get("sleeper_id")
        enriched["crosswalk_matched_by"] = resolved.get("matched_by")
        if resolved.get("error") and not resolved.get("sleeper_id"):
            enriched["crosswalk_error"] = resolved.get("error")
        out.append(enriched)
    return out


def crosswalk_stats(players: dict[str, Any] | None = None) -> dict[str, Any]:
    """Coverage summary — useful for sanity-checking the join."""
    players = players if players is not None else load_players()
    total = 0
    with_yahoo = 0
    active_skill = 0
    active_with_yahoo = 0
    skill = {"QB", "RB", "WR", "TE", "K", "DEF"}
    for info in players.values():
        if not isinstance(info, dict):
            continue
        total += 1
        has = normalize_yahoo_id(info.get("yahoo_id")) is not None
        if has:
            with_yahoo += 1
        if info.get("position") in skill and info.get("active"):
            active_skill += 1
            if has:
                active_with_yahoo += 1
    return {
        "players_total": total,
        "with_yahoo_id": with_yahoo,
        "active_skill_positions": active_skill,
        "active_skill_with_yahoo_id": active_with_yahoo,
        "active_skill_missing_yahoo_id": active_skill - active_with_yahoo,
    }

"""
The player map and the enrichment helpers that read from it.

Sleeper returns numeric player IDs everywhere — rosters, matchups, draft picks,
transactions — and nothing else. Turning "9493" into "Puka Nacua, WR, LAR"
requires the full player map, which is a single ~5 MB JSON document covering
every player in the league. Sleeper asks that it be fetched at most once a day.

Hence three cache layers, checked in order:

  1. in-process dict   free, but empty on a cold start
  2. on-disk JSON      survives restarts, may not survive an ephemeral host
  3. the API           ~5 MB, slow, rate-limit sensitive

Warm the map at startup rather than on first use. Otherwise the first mobile
question of the day, and the first pick of a draft, both pay the 5 MB tax.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .config import CACHE_DIR, PLAYER_CACHE_TTL, SPORT
from .http import get_json

# Module-level singleton, shared by every importer of this package.
_players_cache: dict[str, Any] | None = None
_players_cache_ts: float = 0.0


def load_players() -> dict[str, Any]:
    """Return the full player map: memory, then disk, then the API."""
    global _players_cache, _players_cache_ts
    now = time.time()
    if _players_cache is not None and (now - _players_cache_ts) < PLAYER_CACHE_TTL:
        return _players_cache

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"players_{SPORT}.json"
    if cache_file.exists() and (now - cache_file.stat().st_mtime) < PLAYER_CACHE_TTL:
        try:
            _players_cache = json.loads(cache_file.read_text())
            _players_cache_ts = cache_file.stat().st_mtime
            return _players_cache
        except json.JSONDecodeError:
            pass

    players = get_json(f"/players/{SPORT}") or {}
    try:
        cache_file.write_text(json.dumps(players))
    except OSError:
        pass
    _players_cache = players
    _players_cache_ts = now
    return players


def warm() -> int:
    """Load the player map ahead of first use. Returns how many players landed.

    Call this at server startup and at the start of a draft session. Safe to
    call repeatedly — it is just load_players() with a friendlier name.
    """
    return len(load_players())


def reset_cache() -> None:
    """Drop the in-process player map. Disk cache is left alone."""
    global _players_cache, _players_cache_ts
    _players_cache = None
    _players_cache_ts = 0.0


def player_name(pid: str, players: dict[str, Any]) -> dict[str, Any]:
    """Resolve one player_id into a readable record.

    Takes the map as an argument rather than calling load_players() itself:
    callers resolving a whole roster should load once and pass it in, not
    re-check the cache on every one of eighteen players.
    """
    info = players.get(pid)
    if info:
        first = info.get("first_name") or ""
        last = info.get("last_name") or ""
        name = (first + " " + last).strip() or info.get("full_name") or pid
        return {
            "player_id": pid,
            "name": name,
            "position": info.get("position"),
            "team": info.get("team"),
            "injury_status": info.get("injury_status") or None,
        }
    # Team defenses are keyed by the team abbreviation (e.g. "DET"), not by a
    # numeric ID, so they never appear in the player map.
    if pid.isalpha() and pid.isupper():
        return {"player_id": pid, "name": f"{pid} D/ST", "position": "DEF", "team": pid}
    return {"player_id": pid, "name": pid, "position": None, "team": None}


def enrich_players(ids: list[str] | None, players: dict[str, Any]) -> list[dict]:
    """Resolve a list of player_ids. Tolerates None for an empty roster slot."""
    return [player_name(pid, players) for pid in (ids or [])]

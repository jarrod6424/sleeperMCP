"""
Sleeper Fantasy Football MCP server (read-only).

Wraps the public Sleeper HTTP API (https://docs.sleeper.com). That API is
read-only and requires no authentication, so this server can only read data.
There are no write paths anywhere in this file.

The default league is taken from the SLEEPER_LEAGUE_ID environment variable and
falls back to the league in the URL you provided. Every league tool accepts an
optional league_id if you ever want to point at a different one.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import time
from pathlib import Path
from typing import Any

# Corporate networks that do TLS inspection present a private root CA that
# Python's bundled certifi does not trust, which breaks every outbound call.
# truststore routes verification through the OS trust store instead.
#
# This is opt-in because a cloud host has no inspecting proxy and certifi works
# there normally — and because an unconditional import crashes any environment
# where truststore is not installed. Set USE_OS_TRUSTSTORE=1 locally.
if os.environ.get("USE_OS_TRUSTSTORE"):
    import truststore

    truststore.inject_into_ssl()

import httpx
from fastmcp import FastMCP

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

BASE_URL = "https://api.sleeper.app/v1"
DEFAULT_LEAGUE_ID = os.environ.get("SLEEPER_LEAGUE_ID", "1312218810614300672")
SPORT = os.environ.get("SLEEPER_SPORT", "nfl")

# Identity used to resolve "my team" without passing a roster_id.
DEFAULT_USERNAME = os.environ.get("SLEEPER_USERNAME", "JarrodLee")
DEFAULT_TEAM_NAME = os.environ.get("SLEEPER_TEAM_NAME", "Pine Bluff Escapees")

# The full player map is ~5MB. Sleeper asks that you fetch it at most once a
# day, so it is cached on disk and refreshed only when stale.
CACHE_DIR = Path(
    os.environ.get("SLEEPER_CACHE_DIR", Path.home() / ".cache" / "sleeper-mcp")
)
PLAYER_CACHE_TTL = 18 * 60 * 60  # seconds

mcp = FastMCP("sleeper-readonly")

_client = httpx.Client(
    base_url=BASE_URL,
    timeout=30.0,
    headers={"User-Agent": "sleeper-mcp-readonly/1.0"},
)

# Separate client for Sleeper's UNDOCUMENTED endpoints (stats and projections),
# which live on a different host. These are not part of the supported API and
# can change or disappear without notice. Kept isolated so that if they break,
# only the projection tools are affected and every documented tool keeps working.
ALT_BASE_URL = "https://api.sleeper.com"
PROJ_CACHE_TTL = 6 * 60 * 60  # seconds

_alt_client = httpx.Client(
    base_url=ALT_BASE_URL,
    timeout=30.0,
    headers={"User-Agent": "sleeper-mcp-readonly/1.0"},
)

# Client for FantasyCalc's public trade-value API (third party). Semi-official:
# documented by FantasyCalc in a guest post, but with no formal API docs or
# stated rate limits. Isolated like the projections client so a failure here
# only affects the trade-value tools.
FC_BASE_URL = "https://api.fantasycalc.com"
FC_CACHE_TTL = 6 * 60 * 60  # seconds

_fc_client = httpx.Client(
    base_url=FC_BASE_URL,
    timeout=30.0,
    headers={"User-Agent": "sleeper-mcp-readonly/1.0"},
)

# nflverse open data — hosted on GitHub releases, MIT licensed.
# Files are large (2–7 MB per season) so cached on disk and refreshed every 6 h.
NFLVERSE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"
NFLVERSE_CACHE_TTL = 6 * 60 * 60

_nfl_client = httpx.Client(
    follow_redirects=True,
    timeout=60.0,
    headers={"User-Agent": "sleeper-mcp-readonly/1.0"},
)

# Short-lived in-memory caches to avoid hammering the API within one session.
_mem_cache: dict[str, tuple[float, Any]] = {}
_MEM_TTL = 3600.0  # seconds

# In-memory singleton for the player map (5 MB JSON, expensive to re-parse).
_players_cache: dict[str, Any] | None = None
_players_cache_ts: float = 0.0

# In-memory cache for parsed nflverse CSV files (2-7 MB each, re-parsed per call otherwise).
_csv_mem_cache: dict[str, list[dict]] = {}
_csv_mem_cache_ts: dict[str, float] = {}


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------


def _get(path: str, *, cache: bool = False) -> Any:
    """GET a Sleeper API path and return parsed JSON. Read-only."""
    if cache:
        hit = _mem_cache.get(path)
        if hit and (time.time() - hit[0]) < _MEM_TTL:
            return hit[1]
    resp = _client.get(path)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    if cache:
        _mem_cache[path] = (time.time(), data)
    return data


def _league(league_id: str | None) -> str:
    return league_id or DEFAULT_LEAGUE_ID


# --------------------------------------------------------------------------
# Player map (cached) and enrichment helpers
# --------------------------------------------------------------------------


def _load_players() -> dict[str, Any]:
    """Return the full player map. Checks an in-memory singleton first, then
    the on-disk cache, then fetches from the API."""
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

    players = _get(f"/players/{SPORT}") or {}
    try:
        cache_file.write_text(json.dumps(players))
    except OSError:
        pass
    _players_cache = players
    _players_cache_ts = now
    return players


def _player_name(pid: str, players: dict[str, Any]) -> dict[str, Any]:
    """Resolve one player_id into a readable record."""
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
    # Team defenses are keyed by the team abbreviation (e.g. "DET").
    if pid.isalpha() and pid.isupper():
        return {"player_id": pid, "name": f"{pid} D/ST", "position": "DEF", "team": pid}
    return {"player_id": pid, "name": pid, "position": None, "team": None}


def _enrich_players(ids: list[str] | None, players: dict[str, Any]) -> list[dict]:
    return [_player_name(pid, players) for pid in (ids or [])]


def _user_map(league_id: str) -> dict[str, dict]:
    """Map user_id -> {display_name, team_name}."""
    users = _get(f"/league/{league_id}/users", cache=True) or []
    out: dict[str, dict] = {}
    for u in users:
        meta = u.get("metadata") or {}
        out[u.get("user_id")] = {
            "display_name": u.get("display_name"),
            "team_name": meta.get("team_name") or u.get("display_name"),
            "is_commissioner": bool(u.get("is_owner")),
        }
    return out


# --------------------------------------------------------------------------
# Tools: league overview
# --------------------------------------------------------------------------


@mcp.tool()
def get_nfl_state() -> dict:
    """Current NFL state: active week, season, season type. Use this to learn
    the current week before asking for matchups or transactions."""
    return _get(f"/state/{SPORT}", cache=True) or {}


_LEAGUE_KEEP_SETTINGS = {
    "num_teams", "playoff_teams", "playoff_week_start", "trade_deadline",
    "waiver_type", "waiver_budget", "max_keepers", "type", "best_ball",
    "playoff_type", "playoff_round_type", "reserve_slots", "taxi_slots",
    "taxi_years", "taxi_allow_vets", "leg",
}

_LEAGUE_DROP_FIELDS = {
    "last_message_id", "last_message_text_map", "last_message_time",
    "last_message_attachment", "last_author_id", "last_author_display_name",
    "last_author_avatar", "last_author_is_bot", "last_pinned_message_id",
    "last_read_id", "shard", "company_id", "group_id", "bracket_id",
    "bracket_overrides_id", "loser_bracket_id", "loser_bracket_overrides_id",
    "avatar",
}


@mcp.tool()
def get_league(league_id: str | None = None) -> dict:
    """League settings and metadata: name, status, scoring, roster slots,
    number of teams, season. Returns a trimmed view — use get_league_full for
    all raw fields."""
    data = _get(f"/league/{_league(league_id)}", cache=True)
    if not data:
        return {"error": "league not found", "league_id": _league(league_id)}
    trimmed = {k: v for k, v in data.items() if k not in _LEAGUE_DROP_FIELDS}
    if "settings" in trimmed:
        trimmed["settings"] = {
            k: v for k, v in trimmed["settings"].items() if k in _LEAGUE_KEEP_SETTINGS
        }
    return trimmed


@mcp.tool()
def get_league_full(league_id: str | None = None) -> dict:
    """Full unfiltered league data including all internal Sleeper fields. Use
    get_league for everyday questions; only call this when you need obscure
    settings not returned by get_league."""
    data = _get(f"/league/{_league(league_id)}", cache=True)
    if not data:
        return {"error": "league not found", "league_id": _league(league_id)}
    return data


@mcp.tool()
def get_managers(league_id: str | None = None) -> list[dict]:
    """List the managers (users) in the league with display names, team names,
    and who the commissioner is."""
    users = _get(f"/league/{_league(league_id)}/users") or []
    result = []
    for u in users:
        meta = u.get("metadata") or {}
        result.append(
            {
                "user_id": u.get("user_id"),
                "username": u.get("username"),
                "display_name": u.get("display_name"),
                "team_name": meta.get("team_name") or u.get("display_name"),
                "is_commissioner": bool(u.get("is_owner")),
            }
        )
    return result


# --------------------------------------------------------------------------
# Tools: rosters and standings
# --------------------------------------------------------------------------


def _format_roster_entry(
    r: dict, owner: dict, players: dict, include_players: bool
) -> dict:
    """Turn one raw roster + its owner into a readable entry."""
    settings = r.get("settings") or {}
    fpts = settings.get("fpts", 0) + (settings.get("fpts_decimal", 0) / 100)
    fpts_against = settings.get("fpts_against", 0) + (
        settings.get("fpts_against_decimal", 0) / 100
    )
    entry = {
        "roster_id": r.get("roster_id"),
        "owner": owner.get("team_name") or "unknown",
        "manager": owner.get("display_name"),
        "wins": settings.get("wins", 0),
        "losses": settings.get("losses", 0),
        "ties": settings.get("ties", 0),
        "points_for": round(fpts, 2),
        "points_against": round(fpts_against, 2),
        "waiver_budget_used": settings.get("waiver_budget_used"),
    }
    if include_players:
        starters = r.get("starters") or []
        entry["starters"] = _enrich_players(starters, players)
        bench_ids = [p for p in (r.get("players") or []) if p not in set(starters)]
        entry["bench"] = _enrich_players(bench_ids, players)
    return entry


def _compute_rosters(lid: str, include_players: bool) -> list[dict]:
    rosters = _get(f"/league/{lid}/rosters", cache=True) or []
    umap = _user_map(lid)
    players = _load_players() if include_players else {}
    return [
        _format_roster_entry(r, umap.get(r.get("owner_id"), {}), players, include_players)
        for r in rosters
    ]


def _compute_standings(lid: str) -> list[dict]:
    ranked = sorted(
        _compute_rosters(lid, include_players=False),
        key=lambda x: (x["wins"], x["points_for"]),
        reverse=True,
    )
    for i, row in enumerate(ranked, start=1):
        row["rank"] = i
    return ranked


@mcp.tool()
def get_rosters(league_id: str | None = None, include_players: bool = True) -> list[dict]:
    """All rosters in the league, joined with manager names and win/loss
    records. Set include_players=False for a lighter response that omits the
    player lists."""
    return _compute_rosters(_league(league_id), include_players)


@mcp.tool()
def get_standings(league_id: str | None = None) -> list[dict]:
    """Current standings, sorted by wins then total points for. A derived view
    built from the rosters endpoint."""
    return _compute_standings(_league(league_id))


# --------------------------------------------------------------------------
# Tools: "my team"
# --------------------------------------------------------------------------


def _resolve_my_roster(lid: str) -> dict | None:
    """Find the configured user's roster in the league. Matches by username
    (resolved to a user_id) first, then falls back to team name, then to
    display name. Returns {roster, owner, matched_by} or None."""
    rosters = _get(f"/league/{lid}/rosters", cache=True) or []
    umap = _user_map(lid)  # user_id -> {display_name, team_name, ...}

    def roster_for(uid: str) -> dict | None:
        return next((r for r in rosters if r.get("owner_id") == uid), None)

    # 1. Username -> user_id via the user endpoint, then match owner.
    if DEFAULT_USERNAME:
        user = _get(f"/user/{DEFAULT_USERNAME}", cache=True)
        uid = user.get("user_id") if user else None
        if uid:
            r = roster_for(uid)
            if r:
                return {"roster": r, "owner": umap.get(uid, {}), "matched_by": "username"}

    # 2. Team name match within the league.
    if DEFAULT_TEAM_NAME:
        target = DEFAULT_TEAM_NAME.strip().lower()
        for uid, info in umap.items():
            if (info.get("team_name") or "").strip().lower() == target:
                r = roster_for(uid)
                if r:
                    return {"roster": r, "owner": info, "matched_by": "team_name"}

    # 3. Display name match within the league.
    if DEFAULT_USERNAME:
        target = DEFAULT_USERNAME.strip().lower()
        for uid, info in umap.items():
            if (info.get("display_name") or "").strip().lower() == target:
                r = roster_for(uid)
                if r:
                    return {"roster": r, "owner": info, "matched_by": "display_name"}

    return None


def _team_report(lid: str, roster: dict, owner: dict, matched_by: str, players: dict) -> dict:
    """Build a full scouting report for one roster: players, record, standings
    rank, current matchup, and next week's matchup."""
    entry = _format_roster_entry(roster, owner, players, include_players=True)
    entry["matched_by"] = matched_by
    rid = roster.get("roster_id")

    standings = _compute_standings(lid)
    for row in standings:
        if row["roster_id"] == rid:
            entry["rank"] = row["rank"]
            entry["teams_in_league"] = len(standings)
            break

    week = _current_week()
    entry["this_week"] = _matchup_for(lid, week, rid)
    entry["next_week"] = _matchup_for(lid, week + 1, rid)
    return entry


def _resolve_roster(lid: str, query: str) -> dict | None:
    """Resolve any team in the league by team name, manager display name, or
    username. Tries an exact match first, then a substring match. Returns
    {roster, owner, matched_by} or None."""
    q = (query or "").strip().lower()
    if not q:
        return None
    rosters = _get(f"/league/{lid}/rosters", cache=True) or []
    users = _get(f"/league/{lid}/users") or []

    def roster_for(uid: str) -> dict | None:
        return next((r for r in rosters if r.get("owner_id") == uid), None)

    def owner_of(u: dict) -> dict:
        meta = u.get("metadata") or {}
        return {
            "display_name": u.get("display_name"),
            "team_name": meta.get("team_name") or u.get("display_name"),
        }

    def fields(u: dict) -> list[str]:
        meta = u.get("metadata") or {}
        return [
            (meta.get("team_name") or "").strip().lower(),
            (u.get("display_name") or "").strip().lower(),
            (u.get("username") or "").strip().lower(),
        ]

    # Exact match on any field.
    for u in users:
        if q in [f for f in fields(u) if f]:
            r = roster_for(u.get("user_id"))
            if r:
                return {"roster": r, "owner": owner_of(u), "matched_by": "exact"}

    # Substring fallback.
    for u in users:
        if any(q in f for f in fields(u) if f):
            r = roster_for(u.get("user_id"))
            if r:
                return {"roster": r, "owner": owner_of(u), "matched_by": "partial"}

    return None


@mcp.tool()
def get_my_team(league_id: str | None = None) -> dict:
    """Your own team: roster, record, standings rank, this week's matchup, and
    next week's matchup. Resolves you by the configured Sleeper username (with
    team name and display name as fallbacks), so no roster_id is needed. Use
    this for any "my team", "my roster", "my record", or "who do I play"
    question."""
    lid = _league(league_id)
    resolved = _resolve_my_roster(lid)
    if not resolved:
        return {
            "error": "could not find your team in this league",
            "tried_username": DEFAULT_USERNAME,
            "tried_team_name": DEFAULT_TEAM_NAME,
            "league_id": lid,
        }
    players = _load_players()
    return _team_report(lid, resolved["roster"], resolved["owner"], resolved["matched_by"], players)


@mcp.tool()
def scout_team(team_name_or_manager: str, league_id: str | None = None) -> dict:
    """Scout any team in the league, chosen by team name or manager name.
    Returns their roster, record, standings rank, current matchup, and next
    week's matchup. Matching is case-insensitive and accepts partial names.
    If nothing matches, the available team names are returned so you can retry."""
    lid = _league(league_id)
    resolved = _resolve_roster(lid, team_name_or_manager)
    if not resolved:
        users = _get(f"/league/{lid}/users") or []
        available = [
            (u.get("metadata") or {}).get("team_name") or u.get("display_name")
            for u in users
        ]
        return {
            "error": "no team matched",
            "query": team_name_or_manager,
            "available_teams": available,
        }
    players = _load_players()
    return _team_report(lid, resolved["roster"], resolved["owner"], resolved["matched_by"], players)


@mcp.tool()
def get_my_roster_id(league_id: str | None = None) -> dict:
    """Resolve the configured user to their roster_id and team name in the
    league. Useful when another tool needs an explicit roster_id."""
    lid = _league(league_id)
    resolved = _resolve_my_roster(lid)
    if not resolved:
        return {"error": "could not resolve your team", "league_id": lid}
    return {
        "roster_id": resolved["roster"].get("roster_id"),
        "team_name": resolved["owner"].get("team_name"),
        "manager": resolved["owner"].get("display_name"),
        "matched_by": resolved["matched_by"],
        "league_id": lid,
    }


# --------------------------------------------------------------------------
# Tools: matchups
# --------------------------------------------------------------------------


def _current_week() -> int:
    state = _get(f"/state/{SPORT}", cache=True) or {}
    return state.get("week") or state.get("display_week") or 1


def _compute_matchups(lid: str, week: int) -> dict:
    raw = _get(f"/league/{lid}/matchups/{week}", cache=True) or []
    rosters = _get(f"/league/{lid}/rosters", cache=True) or []
    umap = _user_map(lid)
    roster_owner = {
        r.get("roster_id"): umap.get(r.get("owner_id"), {}).get("team_name", "unknown")
        for r in rosters
    }

    pairs: dict[Any, list] = {}
    for m in raw:
        team = {
            "roster_id": m.get("roster_id"),
            "team": roster_owner.get(m.get("roster_id"), "unknown"),
            "points": m.get("points"),
        }
        pairs.setdefault(m.get("matchup_id"), []).append(team)

    matchups = [
        {"matchup_id": mid, "teams": teams}
        for mid, teams in sorted(pairs.items(), key=lambda kv: (kv[0] is None, kv[0]))
    ]
    return {"league_id": lid, "week": week, "matchups": matchups}


def _matchup_for(lid: str, week: int, roster_id: Any) -> dict | None:
    """Return one roster's matchup for a week: own points, opponent, and the
    opponent's points. Returns None if the roster has no matchup that week
    (for example a week past the end of the schedule). For future weeks the
    pairing is set but points will be null until the games are played."""
    for mu in _compute_matchups(lid, week)["matchups"]:
        teams = mu["teams"]
        if any(t["roster_id"] == roster_id for t in teams):
            me = next(t for t in teams if t["roster_id"] == roster_id)
            opp = next((t for t in teams if t["roster_id"] != roster_id), None)
            return {
                "week": week,
                "points": me["points"],
                "opponent": opp["team"] if opp else "BYE",
                "opponent_points": opp["points"] if opp else None,
                "opponent_roster_id": opp["roster_id"] if opp else None,
            }
    return None


@mcp.tool()
def get_matchups(league_id: str | None = None, week: int | None = None) -> dict:
    """Matchups for a given week, paired up by opponent and labeled with team
    names and scores. If week is omitted, the current NFL week is used."""
    lid = _league(league_id)
    return _compute_matchups(lid, week if week is not None else _current_week())


# --------------------------------------------------------------------------
# Tools: transactions and picks
# --------------------------------------------------------------------------


def _compute_transactions(lid: str, week: int) -> list[dict]:
    """Enriched transaction list for one week (the Sleeper "round")."""
    txns = _get(f"/league/{lid}/transactions/{week}", cache=True) or []
    players = _load_players()
    rosters = _get(f"/league/{lid}/rosters", cache=True) or []
    umap = _user_map(lid)
    roster_owner = {
        r.get("roster_id"): umap.get(r.get("owner_id"), {}).get("team_name", "unknown")
        for r in rosters
    }

    def names(d: dict | None, team_key: str) -> list[dict]:
        out = []
        for pid, rid in (d or {}).items():
            rec = _player_name(pid, players)
            rec[team_key] = roster_owner.get(rid)
            out.append(rec)
        return out

    result = []
    for t in txns:
        result.append(
            {
                "type": t.get("type"),
                "status": t.get("status"),
                "week": t.get("leg"),
                "created": t.get("created"),
                "roster_ids": t.get("roster_ids"),
                "teams": [roster_owner.get(rid) for rid in (t.get("roster_ids") or [])],
                "adds": names(t.get("adds"), "to_team"),
                "drops": names(t.get("drops"), "from_team"),
                "draft_picks": t.get("draft_picks") or [],
                "waiver_bid": (t.get("settings") or {}).get("waiver_bid"),
            }
        )
    return result


@mcp.tool()
def get_transactions(league_id: str | None = None, week: int | None = None) -> list[dict]:
    """Trades, waivers, and free-agent moves for a week (the Sleeper "round").
    Adds and drops are resolved to player names. If week is omitted, the current
    week is used."""
    lid = _league(league_id)
    return _compute_transactions(lid, week if week is not None else _current_week())


@mcp.tool()
def recent_moves(weeks: int = 3, league_id: str | None = None) -> list[dict]:
    """Trades, waivers, and free-agent moves across the last N weeks combined
    (default 3), newest first, with player names resolved. Saves querying each
    week separately."""
    lid = _league(league_id)
    current = _current_week()
    moves: list[dict] = []
    for wk in range(current, max(0, current - weeks), -1):
        if wk < 1:
            break
        moves.extend(_compute_transactions(lid, wk))
    moves.sort(key=lambda m: (m.get("created") or 0), reverse=True)
    return moves


@mcp.tool()
def get_traded_picks(league_id: str | None = None) -> list[dict]:
    """All traded draft picks in the league, including future picks."""
    return _get(f"/league/{_league(league_id)}/traded_picks") or []


@mcp.tool()
def get_playoff_bracket(league_id: str | None = None, bracket: str = "winners") -> list[dict]:
    """Playoff bracket. bracket is "winners" or "losers"."""
    lid = _league(league_id)
    which = "losers_bracket" if bracket.lower().startswith("los") else "winners_bracket"
    return _get(f"/league/{lid}/{which}") or []


# --------------------------------------------------------------------------
# Tools: drafts
# --------------------------------------------------------------------------


@mcp.tool()
def get_drafts(league_id: str | None = None) -> list[dict]:
    """All drafts for the league (most leagues have one; dynasty leagues may
    have several), newest first."""
    return _get(f"/league/{_league(league_id)}/drafts") or []


@mcp.tool()
def get_draft(draft_id: str) -> dict:
    """Details for a specific draft, including draft order and slot mapping."""
    return _get(f"/draft/{draft_id}") or {"error": "draft not found", "draft_id": draft_id}


@mcp.tool()
def get_draft_picks(draft_id: str) -> list[dict]:
    """Every pick in a draft, in order, with player names resolved."""
    picks = _get(f"/draft/{draft_id}/picks") or []
    out = []
    for p in picks:
        meta = p.get("metadata") or {}
        name = (meta.get("first_name", "") + " " + meta.get("last_name", "")).strip()
        out.append(
            {
                "pick_no": p.get("pick_no"),
                "round": p.get("round"),
                "draft_slot": p.get("draft_slot"),
                "roster_id": p.get("roster_id"),
                "player": name or p.get("player_id"),
                "position": meta.get("position"),
                "team": meta.get("team"),
                "is_keeper": p.get("is_keeper"),
            }
        )
    return out


@mcp.tool()
def get_draft_traded_picks(draft_id: str) -> list[dict]:
    """Draft picks that were traded within a specific draft. Useful in keeper
    and dynasty formats where picks change hands. Use get_drafts to find the
    draft_id."""
    return _get(f"/draft/{draft_id}/traded_picks") or []


# --------------------------------------------------------------------------
# Tools: players and users
# --------------------------------------------------------------------------


@mcp.tool()
def get_available_players(
    position: str | None = None, limit: int = 25, league_id: str | None = None
) -> list[dict]:
    """Free agents in the league: active players not on any roster. Optionally
    filter by position (QB, RB, WR, TE, K, DEF). Results are sorted by Sleeper's
    search rank, a rough popularity proxy, since the documented API has no
    projections. Lower search rank means more widely rostered across Sleeper."""
    lid = _league(league_id)
    rosters = _get(f"/league/{lid}/rosters", cache=True) or []
    rostered = {pid for r in rosters for pid in (r.get("players") or [])}

    players = _load_players()
    pos = position.upper() if position else None
    standard = {"QB", "RB", "WR", "TE", "K", "DEF"}
    UNRANKED = 10**9

    candidates = []
    for pid, info in players.items():
        if pid in rostered:
            continue
        p = info.get("position")
        if pos:
            if p != pos:
                continue
        elif p not in standard:
            continue
        # Team defenses have no "active" flag; keep them. Otherwise require active.
        if p != "DEF" and not info.get("active"):
            continue
        rank = info.get("search_rank")
        rank = UNRANKED if rank is None else rank
        candidates.append((rank, pid, info))

    candidates.sort(key=lambda x: x[0])
    out = []
    for rank, pid, info in candidates[:limit]:
        rec = _player_name(pid, players)
        rec["search_rank"] = None if rank >= UNRANKED else rank
        rec["status"] = info.get("status")
        out.append(rec)
    return out


@mcp.tool()
def search_player(name: str, limit: int = 10) -> list[dict]:
    """Find players by (partial) name. Returns player_id, position, and team
    so you can look them up elsewhere."""
    players = _load_players()
    needle = name.lower().replace(" ", "")
    matches = []
    for pid, info in players.items():
        full = (info.get("search_full_name") or "").lower()
        if needle in full:
            matches.append(_player_name(pid, players))
            if len(matches) >= limit:
                break
    return matches


@mcp.tool()
def get_player(player_id: str) -> dict:
    """Full detail for a single player_id."""
    players = _load_players()
    info = players.get(player_id)
    if not info:
        return _player_name(player_id, players)
    return info


@mcp.tool()
def get_trending_players(
    kind: str = "add", lookback_hours: int = 24, limit: int = 25
) -> list[dict]:
    """Trending players by add or drop activity. kind is "add" or "drop"."""
    kind = "drop" if kind.lower().startswith("d") else "add"
    path = f"/players/{SPORT}/trending/{kind}?lookback_hours={lookback_hours}&limit={limit}"
    trends = _get(path) or []
    players = _load_players()
    out = []
    for t in trends:
        rec = _player_name(t.get("player_id"), players)
        rec["count"] = t.get("count")
        out.append(rec)
    return out


@mcp.tool()
def get_user(username_or_id: str) -> dict:
    """Look up a Sleeper user by username or user_id."""
    return _get(f"/user/{username_or_id}") or {
        "error": "user not found",
        "query": username_or_id,
    }


# --------------------------------------------------------------------------
# Tools: projections and start/sit  (UNOFFICIAL)
#
# Everything below uses Sleeper's undocumented projections endpoint on
# api.sleeper.com. It is not part of the supported API. Sleeper has pulled
# stats endpoints before at a data provider's request, so treat this as
# best-effort: it may stop working at any time without affecting the rest.
# --------------------------------------------------------------------------

# Slot eligibility for lineup optimization. Bench-like slots are skipped.
_SLOT_ELIGIBILITY: dict[str, set] = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "DEF": {"DEF"},
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}
_SKIP_SLOTS = {"BN", "IR", "TAXI", "NA"}


def _alt_get(path: str, params: list | None = None) -> Any:
    """GET against the undocumented api.sleeper.com host. Read-only."""
    resp = _alt_client.get(path, params=params)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _normalize_projections(raw: Any) -> dict[str, dict]:
    """Reduce a projections payload to {player_id: stats_dict}. Handles both a
    list of records and a dict keyed by player_id, since the undocumented shape
    is not guaranteed."""
    out: dict[str, dict] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            pid = item.get("player_id") or (item.get("player") or {}).get("player_id")
            if pid is not None:
                out[str(pid)] = item.get("stats") or {}
    elif isinstance(raw, dict):
        for pid, item in raw.items():
            if isinstance(item, dict):
                out[str(pid)] = item.get("stats") or item
    return out


def _projections_for(season: str, week: int) -> dict[str, dict]:
    """Normalized weekly projections, cached on disk for a few hours."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"proj_{SPORT}_{season}_wk{week}.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < PROJ_CACHE_TTL:
        try:
            return json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            pass

    params = [("season_type", "regular")]
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        params.append(("position[]", pos))
    raw = _alt_get(f"/projections/{SPORT}/{season}/{week}", params=params)
    norm = _normalize_projections(raw)
    try:
        cache_file.write_text(json.dumps(norm))
    except OSError:
        pass
    return norm


def _scoring_field(lid: str) -> tuple[str, str]:
    """Pick the projection point field that matches the league's scoring:
    PPR, half-PPR, or standard, based on points per reception."""
    league = _get(f"/league/{lid}", cache=True) or {}
    rec = (league.get("scoring_settings") or {}).get("rec", 0) or 0
    if rec >= 1:
        return "pts_ppr", "PPR"
    if rec >= 0.5:
        return "pts_half_ppr", "Half-PPR"
    return "pts_std", "Standard"


def _proj_points(pid: str, proj: dict, field: str) -> float:
    stats = proj.get(str(pid)) or {}
    val = stats.get(field)
    if val is None:
        val = stats.get("pts_ppr") or stats.get("pts_half_ppr") or stats.get("pts_std")
    return round(float(val), 2) if val is not None else 0.0


def _optimal_lineup(slots: list[str], pool: list[dict]) -> list[dict]:
    """Greedy best-ball lineup: fill the most restrictive slots first, each with
    the highest-projected eligible player still available. A heuristic, not a
    provably optimal assignment, but reliable for start/sit guidance."""
    ranked = sorted(pool, key=lambda p: p["proj"], reverse=True)
    used: set = set()
    assigned: list[dict] = []
    order = sorted(
        range(len(slots)),
        key=lambda i: len(_SLOT_ELIGIBILITY.get(slots[i], {slots[i]})),
    )
    for i in order:
        slot = slots[i]
        elig = _SLOT_ELIGIBILITY.get(slot, {slot})
        pick = next(
            (p for p in ranked if p["player_id"] not in used and p["position"] in elig),
            None,
        )
        if pick:
            used.add(pick["player_id"])
            assigned.append({"slot": slot, **pick})
    return assigned


@mcp.tool()
def get_projections(
    week: int | None = None,
    position: str | None = None,
    limit: int = 50,
    league_id: str | None = None,
) -> dict:
    """[UNOFFICIAL] Weekly fantasy point projections, ranked highest first in
    your league's scoring format. Optionally filter by position. Uses Sleeper's
    undocumented projections endpoint (api.sleeper.com), which is not part of
    the supported API and may change or break without notice."""
    lid = _league(league_id)
    state = _get(f"/state/{SPORT}", cache=True) or {}
    week = week or state.get("week") or 1
    season = str(state.get("season") or state.get("league_season") or "")
    if not season:
        return {"error": "could not determine current season from NFL state"}
    field, fmt = _scoring_field(lid)
    proj = _projections_for(season, week)
    if not proj:
        return {
            "error": "no projection data returned",
            "week": week,
            "season": season,
            "hint": "The undocumented projections endpoint may have changed or be unavailable.",
            "source": "api.sleeper.com projections (UNDOCUMENTED, unsupported)",
        }
    players = _load_players()

    pos = position.upper() if position else None
    rows = []
    for pid in proj:
        rec = _player_name(pid, players)
        if pos and rec["position"] != pos:
            continue
        rec["projected_points"] = _proj_points(pid, proj, field)
        rows.append(rec)
    rows.sort(key=lambda r: r["projected_points"], reverse=True)

    return {
        "week": week,
        "season": season,
        "scoring_format": fmt,
        "source": "api.sleeper.com projections (UNDOCUMENTED, unsupported)",
        "players": rows[:limit],
    }


@mcp.tool()
def start_sit_advice(
    week: int | None = None,
    team_name_or_manager: str | None = None,
    league_id: str | None = None,
) -> dict:
    """[UNOFFICIAL] Start/sit help for a team. Compares the current starters to
    the highest-projected legal lineup and suggests swaps, using your league's
    scoring format. Defaults to your own team; pass a team name or manager to
    check someone else. Relies on Sleeper's undocumented projections endpoint,
    which is unsupported and may break without notice."""
    lid = _league(league_id)
    resolved = (
        _resolve_roster(lid, team_name_or_manager)
        if team_name_or_manager
        else _resolve_my_roster(lid)
    )
    if not resolved:
        return {"error": "could not resolve team", "query": team_name_or_manager}

    roster = resolved["roster"]
    owner = resolved["owner"]
    state = _get(f"/state/{SPORT}", cache=True) or {}
    week = week or state.get("week") or 1
    season = str(state.get("season") or state.get("league_season") or "")
    if not season:
        return {"error": "could not determine current season from NFL state"}
    field, fmt = _scoring_field(lid)
    proj = _projections_for(season, week)
    if not proj:
        return {
            "error": "no projection data returned",
            "team": owner.get("team_name"),
            "week": week,
            "hint": "The undocumented projections endpoint may have changed or be unavailable.",
            "source": "api.sleeper.com projections (UNDOCUMENTED, unsupported)",
        }
    players = _load_players()
    league = _get(f"/league/{lid}", cache=True) or {}

    starters = roster.get("starters") or []
    all_ids = roster.get("players") or []

    def make(pid: str) -> dict:
        rec = _player_name(pid, players)
        return {
            "player_id": str(pid),
            "name": rec["name"],
            "position": rec["position"],
            "proj": _proj_points(pid, proj, field),
        }

    pool = [make(pid) for pid in all_ids]
    pool_by_id = {p["player_id"]: p for p in pool}
    slots = [s for s in (league.get("roster_positions") or []) if s not in _SKIP_SLOTS]

    optimal = _optimal_lineup(slots, pool)
    optimal_ids = {p["player_id"] for p in optimal}
    current_ids = {str(p) for p in starters}

    current_proj = round(sum(pool_by_id.get(str(pid), {}).get("proj", 0.0) for pid in starters), 2)
    optimal_proj = round(sum(p["proj"] for p in optimal), 2)

    sit = [pool_by_id[str(pid)] for pid in starters if str(pid) not in optimal_ids and str(pid) in pool_by_id]
    start = [p for p in optimal if p["player_id"] not in current_ids]
    sit.sort(key=lambda p: p["proj"], reverse=True)
    start.sort(key=lambda p: p["proj"], reverse=True)

    return {
        "team": owner.get("team_name"),
        "week": week,
        "scoring_format": fmt,
        "source": "api.sleeper.com projections (UNDOCUMENTED, unsupported)",
        "current_projected": current_proj,
        "optimal_projected": optimal_proj,
        "potential_point_gain": round(optimal_proj - current_proj, 2),
        "consider_starting": start,
        "consider_benching": sit,
        "optimal_lineup": [
            {"slot": p["slot"], "name": p["name"], "position": p["position"], "proj": p["proj"]}
            for p in optimal
        ],
        "note": (
            "Projections come from an undocumented Sleeper endpoint and are "
            "estimates. Lineup is a greedy heuristic, not a guaranteed optimum."
        ),
    }


# --------------------------------------------------------------------------
# Tools: trade values  (UNOFFICIAL, third party: FantasyCalc)
#
# FantasyCalc derives trade values from millions of real fantasy trades. Each
# value carries a sleeperId, so values join onto Sleeper rosters with no name
# matching. Isolated behind its own client; if FantasyCalc changes, only these
# tools are affected.
# --------------------------------------------------------------------------

FC_SOURCE = "fantasycalc.com trade values (unofficial, third party)"


def _fc_get(path: str, params: list | None = None) -> Any:
    """GET against the FantasyCalc API. Read-only."""
    resp = _fc_client.get(path, params=params)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _league_format(lid: str) -> dict:
    """Translate the Sleeper league's settings into FantasyCalc parameters:
    PPR level, 1QB vs superflex, team count, and dynasty vs redraft."""
    league = _get(f"/league/{lid}", cache=True) or {}
    rec = (league.get("scoring_settings") or {}).get("rec", 0) or 0
    ppr = 1 if rec >= 1 else (0.5 if rec >= 0.5 else 0)
    positions = league.get("roster_positions") or []
    num_qbs = 2 if "SUPER_FLEX" in positions else 1
    num_teams = league.get("total_rosters") or 12
    is_dynasty = (league.get("settings") or {}).get("type") == 2
    return {
        "ppr": ppr,
        "numQbs": num_qbs,
        "numTeams": num_teams,
        "isDynasty": is_dynasty,
    }


def _fc_values(fmt: dict) -> list[dict]:
    """FantasyCalc values for a league format, cached on disk for a few hours."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = (
        f"fc_dyn{int(fmt['isDynasty'])}_qb{fmt['numQbs']}"
        f"_tm{fmt['numTeams']}_ppr{fmt['ppr']}.json"
    )
    cache_file = CACHE_DIR / key
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < FC_CACHE_TTL:
        try:
            return json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            pass

    params = [
        ("isDynasty", str(fmt["isDynasty"]).lower()),
        ("numQbs", fmt["numQbs"]),
        ("numTeams", fmt["numTeams"]),
        ("ppr", fmt["ppr"]),
    ]
    raw = _fc_get("/values/current", params=params) or []
    try:
        cache_file.write_text(json.dumps(raw))
    except OSError:
        pass
    return raw


def _fc_row(v: dict) -> dict:
    p = v.get("player") or {}
    return {
        "name": p.get("name"),
        "position": p.get("position"),
        "team": p.get("maybeTeam"),
        "age": p.get("maybeAge"),
        "value": v.get("value"),
        "redraft_value": v.get("redraftValue"),
        "overall_rank": v.get("overallRank"),
        "position_rank": v.get("positionRank"),
        "tier": v.get("maybeTier"),
        "adp": v.get("maybeAdp"),
        "trend_30_day": v.get("trend30Day"),
        "trade_frequency": v.get("maybeTradeFrequency"),
        "sleeper_id": p.get("sleeperId"),
    }


@mcp.tool()
def get_trade_values(
    limit: int = 50, position: str | None = None, league_id: str | None = None
) -> dict:
    """[UNOFFICIAL] FantasyCalc trade values for your league's exact format
    (PPR, superflex, team count, dynasty/redraft all detected automatically),
    ranked highest first. Optionally filter by position. Source is the
    third-party FantasyCalc API, which may change without notice."""
    lid = _league(league_id)
    fmt = _league_format(lid)
    values = _fc_values(fmt)
    if not values:
        return {"error": "no trade values returned", "format": fmt, "source": FC_SOURCE}

    pos = position.upper() if position else None
    rows = [_fc_row(v) for v in values]
    if pos:
        rows = [r for r in rows if r["position"] == pos]
    rows.sort(key=lambda r: r["value"] or 0, reverse=True)
    return {"format": fmt, "source": FC_SOURCE, "players": rows[:limit]}


@mcp.tool()
def value_my_roster(
    team_name_or_manager: str | None = None, league_id: str | None = None
) -> dict:
    """[UNOFFICIAL] Total FantasyCalc trade value of a roster, with each player
    valued and ranked. Defaults to your team; pass a name to value another team.
    Joins on Sleeper player IDs, so matching is exact. Source is the third-party
    FantasyCalc API."""
    lid = _league(league_id)
    resolved = (
        _resolve_roster(lid, team_name_or_manager)
        if team_name_or_manager
        else _resolve_my_roster(lid)
    )
    if not resolved:
        return {"error": "could not resolve team", "query": team_name_or_manager}

    fmt = _league_format(lid)
    values = _fc_values(fmt)
    if not values:
        return {"error": "no trade values returned", "format": fmt, "source": FC_SOURCE}

    by_sid = {}
    for v in values:
        sid = (v.get("player") or {}).get("sleeperId")
        if sid:
            by_sid[str(sid)] = v

    players_map = _load_players()
    owned = resolved["roster"].get("players") or []
    rows = []
    total = 0
    for pid in owned:
        v = by_sid.get(str(pid))
        rec = _player_name(pid, players_map)
        val = (v or {}).get("value")
        if val:
            total += val
        rows.append(
            {
                "name": rec["name"],
                "position": rec["position"],
                "value": val,
                "position_rank": (v or {}).get("positionRank"),
            }
        )
    rows.sort(key=lambda r: r["value"] or 0, reverse=True)
    return {
        "team": resolved["owner"].get("team_name"),
        "format": fmt,
        "source": FC_SOURCE,
        "total_value": total,
        "players": rows,
    }


@mcp.tool()
def analyze_trade(
    give: list[str], get: list[str], league_id: str | None = None
) -> dict:
    """[UNOFFICIAL] Compare two sides of a trade by FantasyCalc value. "give"
    is what you send away, "get" is what you receive; each is a list of player
    names (or Sleeper player IDs). Returns the totals, the difference, and a
    verdict, using your league's format. Source is the third-party FantasyCalc
    API. Picks are not valued."""
    lid = _league(league_id)
    fmt = _league_format(lid)
    values = _fc_values(fmt)
    if not values:
        return {"error": "no trade values returned", "format": fmt, "source": FC_SOURCE}

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

    def resolve(item: str) -> dict | None:
        raw = str(item).strip()
        key = raw.lower()
        v = by_sid.get(raw) or by_name.get(key)
        if v:
            return v
        partial = [vv for nm, vv in by_name.items() if key in nm]
        if len(partial) == 1:
            return partial[0]
        return None, [((vv.get("player") or {}).get("name") or nm) for nm, vv in by_name.items() if key in nm]

    def side(items: list[str]) -> tuple[list[dict], int, list[dict]]:
        out, total, missing = [], 0, []
        for it in items:
            result = resolve(it)
            if isinstance(result, tuple):
                _, candidates = result
                missing.append({"query": it, "reason": "ambiguous", "matches": candidates})
            elif result:
                out.append(_fc_row(result))
                total += result.get("value") or 0
            else:
                missing.append({"query": it, "reason": "not found"})
        return out, total, missing

    give_rows, give_total, give_missing = side(give or [])
    get_rows, get_total, get_missing = side(get or [])
    diff = get_total - give_total

    larger = max(give_total, get_total, 1)
    ratio = abs(diff) / larger
    if ratio < 0.05:
        verdict = "roughly even"
    elif diff > 0:
        verdict = "you come out ahead"
    else:
        verdict = "you give up more value"

    return {
        "format": fmt,
        "source": FC_SOURCE,
        "give": give_rows,
        "give_total": give_total,
        "get": get_rows,
        "get_total": get_total,
        "difference": diff,
        "percent_vs_larger_side": round(ratio * 100, 1),
        "verdict": verdict,
        "unmatched": {"give": give_missing, "get": get_missing},
        "note": "Values are FantasyCalc estimates and exclude draft picks.",
    }


# --------------------------------------------------------------------------
# Tools: ADP  (FantasyCalc)
# --------------------------------------------------------------------------


@mcp.tool()
def get_adp(
    position: str | None = None,
    limit: int = 50,
    league_id: str | None = None,
) -> dict:
    """[UNOFFICIAL] Average Draft Position (ADP) from FantasyCalc, derived from
    real fantasy drafts and matched to your league's format (PPR, superflex,
    dynasty/redraft detected automatically). ADP is embedded in the trade-values
    response as 'adp' — null means insufficient recent draft data for that player
    (common in the offseason). Optionally filter by position. Source is the
    third-party FantasyCalc API."""
    lid = _league(league_id)
    fmt = _league_format(lid)
    values = _fc_values(fmt)
    if not values:
        return {"error": "no data returned", "format": fmt, "source": FC_SOURCE}

    pos = position.upper() if position else None
    rows = []
    for v in values:
        p = v.get("player") or {}
        adp = v.get("maybeAdp")
        if adp is None:
            continue
        position_val = p.get("position")
        if pos and position_val != pos:
            continue
        rows.append({
            "name": p.get("name"),
            "position": position_val,
            "team": p.get("maybeTeam"),
            "age": p.get("maybeAge"),
            "adp": adp,
            "value": v.get("value"),
            "overall_rank": v.get("overallRank"),
            "position_rank": v.get("positionRank"),
            "sleeper_id": p.get("sleeperId"),
        })

    rows.sort(key=lambda r: r["adp"])
    return {
        "format": fmt,
        "source": FC_SOURCE,
        "note": "ADP is null for players with insufficient recent draft data (common in offseason).",
        "players": rows[:limit],
    }


# --------------------------------------------------------------------------
# Tools: dynasty tiers  (FantasyCalc)
# --------------------------------------------------------------------------


@mcp.tool()
def get_dynasty_tiers(
    position: str | None = None,
    league_id: str | None = None,
) -> dict:
    """[UNOFFICIAL] FantasyCalc dynasty player values grouped into market tiers.
    Tiers come directly from FantasyCalc's own tier algorithm. Each tier
    represents a meaningful value drop from the previous group. Optionally filter
    by position. Also includes redraft value and 30-day trend for context. Source
    is the third-party FantasyCalc API."""
    lid = _league(league_id)
    fmt = _league_format(lid)
    # Always pull dynasty values for this tool — that's what tiers are for.
    dynasty_fmt = {**fmt, "isDynasty": True}
    values = _fc_values(dynasty_fmt)
    if not values:
        return {"error": "no trade values returned", "format": dynasty_fmt, "source": FC_SOURCE}

    pos = position.upper() if position else None
    rows = [_fc_row(v) for v in values if v.get("value")]
    if pos:
        rows = [r for r in rows if r["position"] == pos]
    rows.sort(key=lambda r: r["overall_rank"] or 9999)

    groups: dict[int, list[dict]] = {}
    for r in rows:
        tier = r.get("tier") or 99
        entry = {k: v for k, v in r.items() if k != "tier"}
        groups.setdefault(tier, []).append(entry)

    return {
        "format": dynasty_fmt,
        "source": FC_SOURCE,
        "tiers": [
            {"tier": t, "players": players}
            for t, players in sorted(groups.items())
        ],
    }


# --------------------------------------------------------------------------
# nflverse open data — stats, snaps, depth charts, injuries
# (MIT licensed, no auth required, updated daily during season)
# --------------------------------------------------------------------------

NFLVERSE_SOURCE = "nflverse open data (MIT licensed, github.com/nflverse)"

# Columns to keep from the weekly player stats file (115 total — we trim hard).
_STAT_KEEP = {
    "player_display_name", "position", "team", "week", "season", "opponent_team",
    "targets", "receptions", "receiving_yards", "receiving_tds",
    "receiving_air_yards", "receiving_yards_after_catch",
    "target_share", "air_yards_share", "wopr", "racr",
    "carries", "rushing_yards", "rushing_tds",
    "completions", "attempts", "passing_yards", "passing_tds",
    "passing_interceptions", "passing_air_yards", "passing_cpoe",
    "fantasy_points", "fantasy_points_ppr",
}

_NUMERIC = {
    "week", "targets", "receptions", "receiving_yards", "receiving_tds",
    "receiving_air_yards", "receiving_yards_after_catch",
    "target_share", "air_yards_share", "wopr", "racr",
    "carries", "rushing_yards", "rushing_tds",
    "completions", "attempts", "passing_yards", "passing_tds",
    "passing_interceptions", "passing_air_yards", "passing_cpoe",
    "fantasy_points", "fantasy_points_ppr",
    "offense_snaps", "offense_pct", "defense_snaps", "defense_pct",
    "st_snaps", "st_pct",
}


def _nflverse_csv(tag: str, filename: str) -> list[dict]:
    """Download and disk-cache a nflverse CSV file. Checks an in-memory cache
    first so repeat calls within a session skip disk I/O and CSV parsing."""
    now = time.time()
    if filename in _csv_mem_cache and (now - _csv_mem_cache_ts.get(filename, 0)) < NFLVERSE_CACHE_TTL:
        return _csv_mem_cache[filename]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / filename
    if cache_file.exists() and (now - cache_file.stat().st_mtime) < NFLVERSE_CACHE_TTL:
        raw = cache_file.read_bytes()
    else:
        try:
            resp = _nfl_client.get(f"{NFLVERSE_BASE}/{tag}/{filename}")
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            raw = resp.content
        except Exception:
            return []
        try:
            cache_file.write_bytes(raw)
        except OSError:
            pass

    try:
        text = gzip.decompress(raw).decode("utf-8") if filename.endswith(".gz") else raw.decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(text)))
    except Exception:
        return []

    _csv_mem_cache[filename] = rows
    _csv_mem_cache_ts[filename] = now
    return rows


def _current_season() -> str:
    state = _get(f"/state/{SPORT}", cache=True) or {}
    return str(state.get("season") or state.get("league_season") or "2024")


def _coerce(row: dict, keep: set[str]) -> dict:
    """Keep only desired columns and coerce numerics."""
    out = {}
    for k, v in row.items():
        if k not in keep:
            continue
        if v in ("", "NA", "NULL", "None"):
            continue
        if k in _NUMERIC:
            try:
                out[k] = float(v) if "." in str(v) else int(v)
                continue
            except (ValueError, TypeError):
                pass
        out[k] = v
    return out


def _match_player(rows: list[dict], name: str, name_col: str) -> list[dict]:
    needle = name.strip().lower()
    return [r for r in rows if needle in (r.get(name_col) or "").lower()]


@mcp.tool()
def get_player_stats(
    player_name: str,
    season: str | None = None,
    last_n_weeks: int = 8,
) -> dict:
    """Weekly fantasy stats for a player from nflverse open data: targets,
    receptions, yards, TDs, air yards, target share, WOPR, snap counts, and
    fantasy points. Defaults to the last N weeks of the current season.
    Great for usage trend analysis and dynasty evaluation.
    Source: nflverse (MIT licensed)."""
    season = season or _current_season()
    rows = _nflverse_csv("stats_player", f"stats_player_week_{season}.csv")
    if not rows:
        return {"error": "no data", "season": season,
                "hint": "Season data may not be available yet."}

    matched = _match_player(rows, player_name, "player_display_name")
    if not matched:
        matched = _match_player(rows, player_name, "player_name")
    if not matched:
        return {"error": "player not found", "query": player_name, "season": season}

    matched.sort(key=lambda r: int(r.get("week") or 0), reverse=True)
    recent = sorted(matched[:last_n_weeks], key=lambda r: int(r.get("week") or 0))

    sample = recent[-1]
    return {
        "player": sample.get("player_display_name") or player_name,
        "position": sample.get("position"),
        "team": sample.get("team"),
        "season": season,
        "weeks_shown": len(recent),
        "source": NFLVERSE_SOURCE,
        "stats": [_coerce(r, _STAT_KEEP) for r in recent],
    }


@mcp.tool()
def get_snap_counts(
    player_name: str,
    season: str | None = None,
    last_n_weeks: int = 8,
) -> dict:
    """Snap count and participation percentages for a player by week from
    nflverse. Shows offensive, defensive, and special teams snaps and
    percentages. Key signal for dynasty: role changes show up here first.
    Source: nflverse (MIT licensed)."""
    season = season or _current_season()
    rows = _nflverse_csv("snap_counts", f"snap_counts_{season}.csv")
    if not rows:
        return {"error": "no snap data", "season": season}

    matched = _match_player(rows, player_name, "player")
    if not matched:
        return {"error": "player not found", "query": player_name, "season": season}

    snap_keep = {
        "week", "team", "opponent",
        "offense_snaps", "offense_pct",
        "defense_snaps", "defense_pct",
        "st_snaps", "st_pct",
    }
    matched.sort(key=lambda r: int(r.get("week") or 0), reverse=True)
    recent = sorted(matched[:last_n_weeks], key=lambda r: int(r.get("week") or 0))
    sample = recent[-1]

    return {
        "player": sample.get("player") or player_name,
        "position": sample.get("position"),
        "team": sample.get("team"),
        "season": season,
        "source": NFLVERSE_SOURCE,
        "snaps": [_coerce(r, snap_keep) for r in recent],
    }


@mcp.tool()
def get_depth_chart(
    team: str,
    position: str | None = None,
    season: str | None = None,
) -> dict:
    """Current depth chart for an NFL team from nflverse. Use the team
    abbreviation (BUF, KC, SF, etc). Optionally filter by position.
    Shows depth order for each position group.
    Source: nflverse (MIT licensed)."""
    season = season or _current_season()
    rows = _nflverse_csv("depth_charts", f"depth_charts_{season}.csv")
    if not rows:
        return {"error": "no depth chart data", "season": season}

    team_up = team.strip().upper()
    pos_up = position.strip().upper() if position else None
    matched = [
        r for r in rows
        if r.get("club_code", "").upper() == team_up
        and (pos_up is None or r.get("position", "").upper() == pos_up)
    ]
    if not matched:
        return {"error": "no depth chart found", "team": team, "position": position}

    max_week = max(int(r.get("week") or 0) for r in matched)
    current = [r for r in matched if int(r.get("week") or 0) == max_week]

    by_pos: dict[str, list] = {}
    for r in current:
        entry = {
            "name": r.get("full_name"),
            "depth": int(r.get("depth_team") or 99),
            "formation": r.get("formation") or None,
        }
        by_pos.setdefault(r.get("position") or "UNK", []).append(entry)
    for pos_key in by_pos:
        by_pos[pos_key].sort(key=lambda e: e["depth"])

    return {
        "team": team_up,
        "season": season,
        "week": max_week,
        "source": NFLVERSE_SOURCE,
        "depth_chart": by_pos,
    }


@mcp.tool()
def get_injuries(
    team: str | None = None,
    season: str | None = None,
) -> dict:
    """NFL injury report from nflverse for the most recent week. Optionally
    filter by team abbreviation (BUF, KC, etc). Shows report status, practice
    participation, and injury type for all listed players.
    Source: nflverse (MIT licensed)."""
    season = season or _current_season()
    rows = _nflverse_csv("injuries", f"injuries_{season}.csv")
    if not rows:
        return {"error": "no injury data", "season": season}

    team_up = team.strip().upper() if team else None
    filtered = [
        r for r in rows
        if team_up is None or r.get("team", "").upper() == team_up
    ]
    if not filtered:
        return {"error": "no injury data found", "team": team}

    max_week = max(int(r.get("week") or 0) for r in filtered)
    filtered = [r for r in filtered if int(r.get("week") or 0) == max_week]

    inj_keep = {
        "full_name", "position", "team", "week",
        "report_primary_injury", "report_secondary_injury", "report_status",
        "practice_primary_injury", "practice_status",
    }
    out = [
        _coerce(r, inj_keep) for r in filtered
        if r.get("report_status") or r.get("practice_status")
    ]
    out.sort(key=lambda r: (r.get("team", ""), r.get("position", "")))

    return {
        "team": team_up or "all teams",
        "season": season,
        "week": max_week,
        "source": NFLVERSE_SOURCE,
        "injuries": out,
    }


# --------------------------------------------------------------------------
# Tools: team offense crowding + composite player scoring
# --------------------------------------------------------------------------

# Path to the user-maintained OC tier config (edit directly to update).
_OC_TIERS_FILE = Path(__file__).parent / "oc_tiers.json"


def _load_oc_tiers() -> dict[str, dict]:
    if _OC_TIERS_FILE.exists():
        try:
            data = json.loads(_OC_TIERS_FILE.read_text())
            return {k: v for k, v in data.items() if not k.startswith("_")}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _stats_season_with_label() -> tuple[str, str]:
    """Try the current season; fall back to prior year if no game data exists yet.
    Returns (season_string, human_readable_label)."""
    current = _current_season()
    rows = _nflverse_csv("stats_player", f"stats_player_week_{current}.csv")
    if rows:
        return current, f"{current} (current season)"
    prev = str(int(current) - 1)
    return prev, f"{prev} (historical — {current} season data not yet available)"


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _team_skill_rows(team: str, season: str) -> list[dict]:
    """All weekly stat rows for skill positions on a given team."""
    rows = _nflverse_csv("stats_player", f"stats_player_week_{season}.csv")
    skill = {"WR", "RB", "TE", "QB"}
    return [
        r for r in rows
        if r.get("team", "").upper() == team.upper()
        and r.get("position") in skill
    ]


def _crowding_analysis(team: str, season: str) -> dict:
    """Compute per-player usage consistency and team target concentration."""
    rows = _team_skill_rows(team, season)
    if not rows:
        return {}

    # Build per-player weekly target share and snap pct
    from collections import defaultdict
    player_weeks: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        name = r.get("player_display_name") or r.get("player_name") or ""
        pos = r.get("position") or ""
        tgt_share = _safe_float(r.get("target_share"))
        off_pct = _safe_float(r.get("offense_pct"))  # not in this file, that's snap_counts
        carries = _safe_float(r.get("carries"))
        targets = _safe_float(r.get("targets"))
        if targets + carries == 0:
            continue
        player_weeks[name].append({
            "pos": pos,
            "target_share": tgt_share,
            "targets": targets,
            "carries": carries,
            "week": r.get("week"),
        })

    import statistics
    players_out = []
    for name, weeks in player_weeks.items():
        if not weeks:
            continue
        pos = weeks[0]["pos"]
        shares = [w["target_share"] for w in weeks if w["target_share"] > 0]
        avg_share = round(statistics.mean(shares), 3) if shares else 0.0
        share_std = round(statistics.stdev(shares), 3) if len(shares) > 1 else 0.0
        avg_targets = round(statistics.mean(w["targets"] for w in weeks), 1)
        games = len(weeks)
        # Consistency score: high avg share + low variance = high score (0-100)
        consistency = max(0.0, min(100.0, round(avg_share * 200 - share_std * 300, 1)))
        players_out.append({
            "name": name,
            "position": pos,
            "games": games,
            "avg_target_share": avg_share,
            "target_share_std": share_std,
            "avg_targets_per_game": avg_targets,
            "usage_consistency_score": consistency,
        })

    # Sort by avg target share descending, assign usage rank
    players_out.sort(key=lambda p: p["avg_target_share"], reverse=True)
    for i, p in enumerate(players_out, start=1):
        p["usage_rank"] = i

    # Team-level HHI: measure of target concentration (lower = more spread)
    all_shares = [p["avg_target_share"] for p in players_out if p["avg_target_share"] > 0]
    hhi = round(sum(s ** 2 for s in all_shares), 3) if all_shares else 0.0
    if hhi > 0.35:
        concentration = "high — targets concentrated in 1-2 players"
    elif hhi > 0.20:
        concentration = "moderate — clear hierarchy but multiple contributors"
    else:
        concentration = "distributed — targets spread across many players"

    return {
        "players": players_out,
        "team_hhi": hhi,
        "concentration": concentration,
    }


@mcp.tool()
def get_team_offense_crowding(
    team: str,
    season: str | None = None,
) -> dict:
    """Analyze how a team distributes offensive touches across skill positions.
    Shows each player's average target share, week-to-week consistency, and
    usage rank. Also includes team HHI (target concentration) and the OC's
    tier from your oc_tiers.json config. Low target share variance = player
    has a consistent role. High HHI = targets concentrated in 1-2 players
    (crowded for others). Use this to assess whether a player has a reliable,
    clearly defined role vs. competing for opportunities.
    Source: nflverse (MIT licensed) + oc_tiers.json (user-maintained)."""
    season_used, season_label = _stats_season_with_label() if not season else (season, season)
    if season:
        season_used, season_label = season, season

    analysis = _crowding_analysis(team.upper(), season_used)
    if not analysis:
        return {"error": "no data found", "team": team, "season": season_used}

    oc_tiers = _load_oc_tiers()
    oc_info = oc_tiers.get(team.upper(), {})

    return {
        "team": team.upper(),
        "season": season_label,
        "oc": {
            "name": oc_info.get("oc", "unknown — update oc_tiers.json"),
            "tier": oc_info.get("tier", 3),
            "notes": oc_info.get("notes", ""),
        },
        "team_concentration": {
            "hhi": analysis["team_hhi"],
            "interpretation": analysis["concentration"],
        },
        "source": f"{NFLVERSE_SOURCE} + oc_tiers.json",
        "skill_players": analysis["players"],
    }


@mcp.tool()
def score_player(
    player_name: str,
    league_id: str | None = None,
) -> dict:
    """Composite dynasty player score using your personal weighting model:
      30% FantasyCalc trade value
      10% team fit (fills your roster's weakest positions)
      20% floor consistency (% of games above 10 PPG + avg PPG)
      15% availability (games played %)
      15% usage within their offense (target share / snap rate)
      10% offensive context (OC tier + usage crowding)
    Returns a 0-100 score with a full breakdown of each component.
    Clearly labels whether stats are current-season or historical.
    Sources: FantasyCalc + nflverse + oc_tiers.json + Sleeper."""
    lid = _league(league_id)
    season_used, season_label = _stats_season_with_label()

    result: dict[str, Any] = {
        "player": player_name,
        "data_season": season_label,
        "components": {},
        "warnings": [],
    }

    # ── 1. FantasyCalc trade value (30%) ─────────────────────────────────────
    fmt = _league_format(lid)
    fc_vals = _fc_values(fmt)
    fc_match = None
    player_team = None
    player_pos = None
    if fc_vals:
        needle = player_name.strip().lower()
        # Exact name match first, then partial
        for v in fc_vals:
            nm = ((v.get("player") or {}).get("name") or "").strip().lower()
            if nm == needle:
                fc_match = v
                break
        if not fc_match:
            candidates = [v for v in fc_vals if needle in ((v.get("player") or {}).get("name") or "").lower()]
            if len(candidates) == 1:
                fc_match = candidates[0]
            elif len(candidates) > 1:
                result["warnings"].append(f"Ambiguous FC name — matched: {[((v.get('player') or {}).get('name')) for v in candidates[:5]]}")

    fc_score = 0.0
    fc_detail: dict = {}
    if fc_match:
        max_val = fc_vals[0].get("value") or 1
        val = fc_match.get("value") or 0
        fc_score = round(min(100.0, val / max_val * 100), 1)
        p = fc_match.get("player") or {}
        player_team = p.get("maybeTeam")
        player_pos = p.get("position")
        fc_detail = {
            "value": val,
            "overall_rank": fc_match.get("overallRank"),
            "position_rank": fc_match.get("positionRank"),
            "trend_30_day": fc_match.get("trend30Day"),
        }
    else:
        result["warnings"].append("Player not found in FantasyCalc — trade value component scored 0")
    result["components"]["trade_value"] = {
        "weight": "30%", "score": fc_score, "detail": fc_detail,
    }

    # ── 2. Team fit — fills your weakest positions (20%) ─────────────────────
    fit_score = 50.0  # neutral default
    fit_detail: dict = {}
    try:
        my_roster = _resolve_my_roster(lid)
        if my_roster and fc_vals:
            owned_ids = {str(pid) for pid in (my_roster["roster"].get("players") or [])}
            by_sid = {str((v.get("player") or {}).get("sleeperId")): v for v in fc_vals}
            pos_value: dict[str, float] = {"QB": 0.0, "RB": 0.0, "WR": 0.0, "TE": 0.0}
            pos_count: dict[str, int] = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
            for pid in owned_ids:
                v = by_sid.get(pid)
                if not v:
                    continue
                pos = (v.get("player") or {}).get("position") or ""
                if pos in pos_value:
                    pos_value[pos] += v.get("value") or 0
                    pos_count[pos] += 1
            # Rank positions by avg value (lower avg = weaker = higher fit score for that pos)
            avg_val = {p: (pos_value[p] / pos_count[p] if pos_count[p] else 0) for p in pos_value}
            ranked = sorted(avg_val.items(), key=lambda x: x[1])
            rank_map = {pos: i for i, (pos, _) in enumerate(ranked)}  # 0 = weakest
            target_pos = player_pos or ""
            if target_pos in rank_map:
                rank = rank_map[target_pos]  # 0=weakest, 3=strongest
                fit_score = round([100.0, 75.0, 50.0, 25.0][rank], 1)
            fit_detail = {
                "your_position_values": {p: round(avg_val[p]) for p in avg_val},
                "weakest_position": ranked[0][0] if ranked else "unknown",
                "player_position": target_pos,
                "fit_rank": f"{rank_map.get(target_pos, '?')+1} of 4 (1=best fit)",
            }
    except Exception as e:
        result["warnings"].append(f"Team fit skipped: {e}")
    result["components"]["team_fit"] = {
        "weight": "20%", "score": fit_score, "detail": fit_detail,
    }

    # ── 3 & 4. Stats-based components ────────────────────────────────────────
    stat_rows: list[dict] = []
    if player_team or player_name:
        all_rows = _nflverse_csv("stats_player", f"stats_player_week_{season_used}.csv")
        needle = player_name.strip().lower()
        stat_rows = [
            r for r in all_rows
            if needle in (r.get("player_display_name") or "").lower()
            or needle in (r.get("player_name") or "").lower()
        ]
        if stat_rows and not player_team:
            player_team = stat_rows[-1].get("team")
        if stat_rows and not player_pos:
            player_pos = stat_rows[-1].get("position")

    # 3. Floor consistency — % games above 12 PPG (15%)
    floor_score = 0.0
    floor_detail: dict = {}
    if stat_rows:
        ppr_pts = [_safe_float(r.get("fantasy_points_ppr")) for r in stat_rows]
        games = len(ppr_pts)
        above_12 = sum(1 for p in ppr_pts if p >= 10.0)
        avg_ppg = round(sum(ppr_pts) / games, 1) if games else 0.0
        pct_above = round(above_12 / games * 100, 1) if games else 0.0
        floor_score = round(pct_above * 0.6 + min(avg_ppg / 25 * 100, 100) * 0.4, 1)
        floor_detail = {
            "games_scored": games,
            "avg_ppg": avg_ppg,
            "games_above_10": above_12,
            "pct_above_10": pct_above,
        }
    else:
        result["warnings"].append("No stats found for floor consistency — scored 0")
    result["components"]["floor_consistency"] = {
        "weight": "15%", "score": floor_score, "detail": floor_detail,
    }

    # 4. Availability — games played % (15%)
    avail_score = 0.0
    avail_detail: dict = {}
    if stat_rows:
        # Count weeks with any meaningful stat line
        active_weeks = sum(
            1 for r in stat_rows
            if _safe_float(r.get("fantasy_points_ppr")) > 0
            or _safe_float(r.get("targets")) > 0
            or _safe_float(r.get("carries")) > 0
        )
        # Regular season has 17 weeks (2021+), 16 before
        total_weeks = int(stat_rows[-1].get("season") or 2024) >= 2021 and 17 or 16
        avail_score = round(min(active_weeks / total_weeks * 100, 100), 1)
        avail_detail = {
            "active_weeks": active_weeks,
            "total_regular_season_weeks": total_weeks,
            "availability_pct": avail_score,
        }
    else:
        result["warnings"].append("No stats found for availability — scored 0")
    result["components"]["availability"] = {
        "weight": "15%", "score": avail_score, "detail": avail_detail,
    }

    # 5. Usage within offense (10%)
    usage_score = 0.0
    usage_detail: dict = {}
    if stat_rows:
        pos = player_pos or ""
        shares = [_safe_float(r.get("target_share")) for r in stat_rows if _safe_float(r.get("target_share")) > 0]
        woprs = [_safe_float(r.get("wopr")) for r in stat_rows if _safe_float(r.get("wopr")) > 0]
        carries = [_safe_float(r.get("carries")) for r in stat_rows]
        avg_share = round(sum(shares) / len(shares), 3) if shares else 0.0
        avg_wopr = round(sum(woprs) / len(woprs), 3) if woprs else 0.0
        avg_carries = round(sum(carries) / len(carries), 1)
        if pos in ("WR", "TE"):
            # 25% target share ~ elite = 100
            usage_score = round(min(avg_share * 400, 100), 1)
        elif pos == "RB":
            # Blend target share + carry volume (8 carries/game ~ workhorse = 100)
            carry_score = min(avg_carries / 15 * 100, 100)
            usage_score = round(0.5 * min(avg_share * 400, 100) + 0.5 * carry_score, 1)
        elif pos == "QB":
            usage_score = 85.0  # QB snap share is near 100 if healthy; value comes from other metrics
        usage_detail = {
            "avg_target_share": avg_share,
            "avg_wopr": avg_wopr,
            "avg_carries_per_game": avg_carries,
            "position": pos,
        }
    else:
        result["warnings"].append("No stats for usage — scored 0")
    result["components"]["usage"] = {
        "weight": "10%", "score": usage_score, "detail": usage_detail,
    }

    # 6. Offensive context — OC tier + crowding (10%)
    oc_score = 50.0
    crowding_score = 50.0
    oc_detail: dict = {}
    if player_team:
        oc_tiers = _load_oc_tiers()
        oc_info = oc_tiers.get(player_team.upper(), {})
        oc_tier = oc_info.get("tier", 3)
        oc_score = round((oc_tier / 5) * 100, 1)

        analysis = _crowding_analysis(player_team.upper(), season_used)
        if analysis:
            player_entry = next(
                (p for p in analysis["players"] if player_name.strip().lower() in p["name"].lower()),
                None,
            )
            if player_entry:
                crowding_score = player_entry["usage_consistency_score"]
                oc_detail["usage_rank"] = player_entry["usage_rank"]
                oc_detail["target_share_std"] = player_entry["target_share_std"]
                oc_detail["team_hhi"] = analysis["team_hhi"]
        oc_detail["oc_name"] = oc_info.get("oc", "unknown")
        oc_detail["oc_tier"] = oc_tier
        oc_detail["oc_notes"] = oc_info.get("notes", "")

    offensive_context_score = round(0.5 * oc_score + 0.5 * crowding_score, 1)
    result["components"]["offensive_context"] = {
        "weight": "10%", "score": offensive_context_score, "detail": oc_detail,
    }

    # ── Weighted total ────────────────────────────────────────────────────────
    weights = {
        "trade_value": 0.30,
        "team_fit": 0.10,
        "floor_consistency": 0.20,
        "availability": 0.15,
        "usage": 0.15,
        "offensive_context": 0.10,
    }
    total = round(sum(
        result["components"][k]["score"] * w for k, w in weights.items()
    ), 1)

    result["composite_score"] = total
    result["grade"] = (
        "A" if total >= 80 else
        "B" if total >= 65 else
        "C" if total >= 50 else
        "D" if total >= 35 else "F"
    )
    result["player_team"] = player_team
    result["player_position"] = player_pos
    result["sources"] = f"FantasyCalc + {NFLVERSE_SOURCE} + oc_tiers.json + Sleeper"

    return result


if __name__ == "__main__":
    mcp.run()
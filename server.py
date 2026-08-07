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

import json
import os
import sys
import time
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

from fastmcp import FastMCP

from sleeper_core import adp as _adp
from sleeper_core import http as _http
from sleeper_core import league as _league_mod
from sleeper_core import offense as _offense
from sleeper_core import players as _players
from sleeper_core import projections as _proj
from sleeper_core import stats as _stats
from sleeper_core import values as _values
from sleeper_core.config import (
    DEFAULT_TEAM_NAME,
    DEFAULT_USERNAME,
    FC_SOURCE,
    NFLVERSE_SOURCE,
    OC_TIERS_FILE,
    SPORT,
)

mcp = FastMCP("sleeper-readonly")

# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------
# These now live in sleeper_core.http. They keep their original private names
# here so the ~46 existing call sites stay untouched: this commit moves code,
# it does not change behaviour. Call sites get tidied once every module lands.

_get = _http.get_json
_alt_get = _http.alt_get
_fc_get = _http.fc_get
_nflverse_csv = _http.nflverse_csv

_load_players = _players.load_players
_player_name = _players.player_name
_enrich_players = _players.enrich_players

_league = _league_mod.resolve_league_id
_user_map = _league_mod.user_map
_format_roster_entry = _league_mod.format_roster_entry
_compute_rosters = _league_mod.compute_rosters
_compute_standings = _league_mod.compute_standings
_resolve_my_roster = _league_mod.resolve_my_roster
_resolve_roster = _league_mod.resolve_roster
_team_report = _league_mod.team_report
_current_week = _league_mod.current_week
_compute_matchups = _league_mod.compute_matchups
_matchup_for = _league_mod.matchup_for
_compute_transactions = _league_mod.compute_transactions

_SLOT_ELIGIBILITY = _proj.SLOT_ELIGIBILITY
_SKIP_SLOTS = _proj.SKIP_SLOTS
_normalize_projections = _proj.normalize
_projections_for = _proj.projections_for
_scoring_field = _proj.scoring_field
_proj_points = _proj.proj_points
_optimal_lineup = _proj.optimal_lineup

_league_format = _values.league_format
_fc_values = _values.fc_values
_fc_row = _values.fc_row

_STAT_KEEP = _stats.STAT_KEEP
_NUMERIC = _stats.NUMERIC
_current_season = _stats.current_season
_coerce = _stats.coerce
_match_player = _stats.match_player

_load_oc_tiers = _offense.load_oc_tiers
_stats_season_with_label = _offense.stats_season_with_label
_safe_float = _offense.safe_float
_team_skill_rows = _offense.team_skill_rows
_crowding_analysis = _offense.crowding_analysis

_OC_TIERS_FILE = OC_TIERS_FILE


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


@mcp.tool()
def get_matchups(league_id: str | None = None, week: int | None = None) -> dict:
    """Matchups for a given week, paired up by opponent and labeled with team
    names and scores. If week is omitted, the current NFL week is used."""
    lid = _league(league_id)
    return _compute_matchups(lid, week if week is not None else _current_week())


# --------------------------------------------------------------------------
# Tools: transactions and picks
# --------------------------------------------------------------------------


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
    """[UNOFFICIAL] Average Draft Position from FantasyFootballCalculator,
    derived from real fantasy drafts and matched to your league's format
    (superflex, dynasty and PPR level detected automatically, with a fallback
    when the exact format has too few drafts to be meaningful). Joined to
    FantasyCalc trade value, so you can see where the market drafts a player
    versus what the market thinks he is worth. Optionally filter by position.
    Sources are the third-party FantasyFootballCalculator and FantasyCalc
    APIs."""
    lid = _league(league_id)
    fmt = _league_format(lid)
    season = int(_current_season())
    return _adp.adp_rows(fmt, season, position=position, limit=limit,
                         fc_values=_fc_values(fmt))


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

# Columns to keep from the weekly player stats file (115 total — we trim hard).
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
    return _stats.depth_chart(team, position, season)


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


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------


_warmed = False


def _warm_caches() -> None:
    """Load the ~5 MB player map before serving instead of on first request.

    Without this the first question of the day pays the download cost on the
    request path, and so does the first pick of a draft. Failures are logged
    and swallowed: a cold cache is slow, not broken, and the server should
    still come up if Sleeper is briefly unreachable at boot.

    Idempotent, so it is safe to call from both the import hook and __main__.

    Logs go to stderr, never stdout — under stdio transport stdout carries the
    MCP protocol itself and a stray print corrupts the stream.
    """
    global _warmed
    if _warmed:
        return
    _warmed = True
    try:
        count = _players.warm()
        print(f"[startup] player map warm: {count} players", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - never block startup
        print(f"[startup] player map warm failed ({exc}); "
              f"will load on first use", file=sys.stderr)


# Warm at IMPORT time, not just under __main__.
#
# A managed host is given an entrypoint like "server.py:mcp" and imports this
# module to get the object — it never executes it as a script, so __main__
# never runs and a warm hook living only there would silently never fire.
#
# Opt-in via MCP_WARM so that importing server.py stays cheap for tests, and so
# stdio launches (where Claude Desktop restarts the process often) do not put a
# 5 MB fetch in front of every start. Set MCP_WARM=1 on the deployed host.
if os.environ.get("MCP_WARM"):
    _warm_caches()


if __name__ == "__main__":
    # HTTP transport for remote hosting, or a local smoke test.
    # Streamable HTTP, not SSE — SSE was deprecated in the March 2025 MCP spec.
    if os.environ.get("MCP_HTTP"):
        _warm_caches()  # no-op if the import hook already ran
        mcp.run(
            transport="http",
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8000")),
        )
    else:
        mcp.run()  # stdio for Claude Desktop, unchanged
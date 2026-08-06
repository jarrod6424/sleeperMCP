"""
League-scoped reads: rosters, standings, identity, matchups, transactions.

Everything here takes an explicit league_id. Sleeper's model is that a dynasty
league is a *chain* of per-season leagues linked by previous_league_id, so
"my league" is only ever a default, never an assumption. The 2026 league and
the 2025 league are different IDs with different rosters and different results.

Roster identity is the fiddly part. Sleeper joins rosters to users by owner_id,
and a user has three different names attached (username, display_name, and a
per-league team_name) any of which a person might type. resolve_my_roster and
resolve_roster handle those fallbacks in a deliberate order.
"""

from __future__ import annotations

from typing import Any

from .config import DEFAULT_LEAGUE_ID, DEFAULT_TEAM_NAME, DEFAULT_USERNAME, SPORT
from .http import get_json
from .players import enrich_players, load_players, player_name


def resolve_league_id(league_id: str | None) -> str:
    """Fall back to the configured league when none is given."""
    return league_id or DEFAULT_LEAGUE_ID


def user_map(league_id: str) -> dict[str, dict]:
    """Map user_id -> {display_name, team_name, is_commissioner}."""
    users = get_json(f"/league/{league_id}/users", cache=True) or []
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
# Rosters and standings
# --------------------------------------------------------------------------


def format_roster_entry(r: dict, owner: dict, players: dict, include_players: bool) -> dict:
    """Turn one raw roster plus its owner into a readable entry.

    Sleeper splits fantasy points across two integer fields — fpts and
    fpts_decimal — rather than storing a float. 117 and 20 means 117.20.
    """
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
        entry["starters"] = enrich_players(starters, players)
        # "players" is everyone rostered; bench is that minus the starters.
        bench_ids = [p for p in (r.get("players") or []) if p not in set(starters)]
        entry["bench"] = enrich_players(bench_ids, players)
    return entry


def compute_rosters(lid: str, include_players: bool) -> list[dict]:
    """Every roster in the league. Loads the player map once, not per roster."""
    rosters = get_json(f"/league/{lid}/rosters", cache=True) or []
    umap = user_map(lid)
    players = load_players() if include_players else {}
    return [
        format_roster_entry(r, umap.get(r.get("owner_id"), {}), players, include_players)
        for r in rosters
    ]


def compute_standings(lid: str) -> list[dict]:
    """Standings sorted by wins, then points for. Derived from rosters —
    Sleeper has no standings endpoint."""
    ranked = sorted(
        compute_rosters(lid, include_players=False),
        key=lambda x: (x["wins"], x["points_for"]),
        reverse=True,
    )
    for i, row in enumerate(ranked, start=1):
        row["rank"] = i
    return ranked


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def resolve_my_roster(
    lid: str,
    username: str | None = None,
    team_name: str | None = None,
) -> dict | None:
    """Find a user's roster in the league.

    Tries username -> user_id via the user endpoint first, since that is an
    exact identity join. Falls back to team name, then display name, both of
    which are user-editable and can collide.

    username and team_name default to the configured identity. Pass them
    explicitly for a second manager — that is the seam multi-user support
    grows from, without the caller needing to know about the env defaults.

    Returns {roster, owner, matched_by} or None.
    """
    username = username if username is not None else DEFAULT_USERNAME
    team_name = team_name if team_name is not None else DEFAULT_TEAM_NAME

    rosters = get_json(f"/league/{lid}/rosters", cache=True) or []
    umap = user_map(lid)

    def roster_for(uid: str) -> dict | None:
        return next((r for r in rosters if r.get("owner_id") == uid), None)

    # 1. Username -> user_id via the user endpoint, then match owner.
    if username:
        user = get_json(f"/user/{username}", cache=True)
        uid = user.get("user_id") if user else None
        if uid:
            r = roster_for(uid)
            if r:
                return {"roster": r, "owner": umap.get(uid, {}), "matched_by": "username"}

    # 2. Team name match within the league.
    if team_name:
        target = team_name.strip().lower()
        for uid, info in umap.items():
            if (info.get("team_name") or "").strip().lower() == target:
                r = roster_for(uid)
                if r:
                    return {"roster": r, "owner": info, "matched_by": "team_name"}

    # 3. Display name match within the league.
    if username:
        target = username.strip().lower()
        for uid, info in umap.items():
            if (info.get("display_name") or "").strip().lower() == target:
                r = roster_for(uid)
                if r:
                    return {"roster": r, "owner": info, "matched_by": "display_name"}

    return None


def resolve_roster(lid: str, query: str) -> dict | None:
    """Resolve any team by team name, manager display name, or username.

    Exact match on any field first, then substring. Exact-first matters: in a
    league with "Combs Cavaliers" and "Combs", a substring-first search would
    return the wrong team for the exact query "Combs".

    Returns {roster, owner, matched_by} or None.
    """
    q = (query or "").strip().lower()
    if not q:
        return None
    rosters = get_json(f"/league/{lid}/rosters", cache=True) or []
    users = get_json(f"/league/{lid}/users") or []

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

    for u in users:
        if q in [f for f in fields(u) if f]:
            r = roster_for(u.get("user_id"))
            if r:
                return {"roster": r, "owner": owner_of(u), "matched_by": "exact"}

    for u in users:
        if any(q in f for f in fields(u) if f):
            r = roster_for(u.get("user_id"))
            if r:
                return {"roster": r, "owner": owner_of(u), "matched_by": "partial"}

    return None


def team_report(lid: str, roster: dict, owner: dict, matched_by: str, players: dict) -> dict:
    """Full scouting report for one roster: players, record, rank, and both
    this week's and next week's matchup."""
    entry = format_roster_entry(roster, owner, players, include_players=True)
    entry["matched_by"] = matched_by
    rid = roster.get("roster_id")

    standings = compute_standings(lid)
    for row in standings:
        if row["roster_id"] == rid:
            entry["rank"] = row["rank"]
            entry["teams_in_league"] = len(standings)
            break

    week = current_week()
    entry["this_week"] = matchup_for(lid, week, rid)
    entry["next_week"] = matchup_for(lid, week + 1, rid)
    return entry


# --------------------------------------------------------------------------
# Matchups
# --------------------------------------------------------------------------


def current_week() -> int:
    """The live NFL week. Note this reads global NFL state, not league state,
    so it keeps moving during the season — pin it explicitly in tests."""
    state = get_json(f"/state/{SPORT}", cache=True) or {}
    return state.get("week") or state.get("display_week") or 1


def compute_matchups(lid: str, week: int) -> dict:
    """Matchups for a week, paired by matchup_id and labelled with team names."""
    raw = get_json(f"/league/{lid}/matchups/{week}", cache=True) or []
    rosters = get_json(f"/league/{lid}/rosters", cache=True) or []
    umap = user_map(lid)
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

    # None sorts last: an unpaired roster has a null matchup_id.
    matchups = [
        {"matchup_id": mid, "teams": teams}
        for mid, teams in sorted(pairs.items(), key=lambda kv: (kv[0] is None, kv[0]))
    ]
    return {"league_id": lid, "week": week, "matchups": matchups}


def matchup_for(lid: str, week: int, roster_id: Any) -> dict | None:
    """One roster's matchup for a week: own points, opponent, opponent points.

    None when the roster has no matchup that week, for example past the end of
    the schedule. For future weeks the pairing exists but points stay null
    until the games are played.
    """
    for mu in compute_matchups(lid, week)["matchups"]:
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


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------


def compute_transactions(lid: str, week: int) -> list[dict]:
    """Enriched transaction list for one week (Sleeper calls a week a "leg").

    Sleeper returns adds and drops as {player_id: roster_id} maps, so each one
    needs both a player lookup and a roster lookup to become readable.
    """
    txns = get_json(f"/league/{lid}/transactions/{week}", cache=True) or []
    players = load_players()
    rosters = get_json(f"/league/{lid}/rosters", cache=True) or []
    umap = user_map(lid)
    roster_owner = {
        r.get("roster_id"): umap.get(r.get("owner_id"), {}).get("team_name", "unknown")
        for r in rosters
    }

    def names(d: dict | None, team_key: str) -> list[dict]:
        out = []
        for pid, rid in (d or {}).items():
            rec = player_name(pid, players)
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

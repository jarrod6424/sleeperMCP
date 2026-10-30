"""
Positional strength of schedule from nflverse.

What each defense has allowed to QB / RB / WR / TE so far this season,
applied to a team's remaining opponents. This is a measurement: how soft or
hard a schedule looks from points allowed. It does not say what to do with it.

Defense-vs-position is noisy over half a season, so each defense's rate is
shrunk toward the league average by SHRINK_GAMES of average play before it is
used. A defense that has played 7 games moves about halfway from average.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .config import STATS_CACHE_TTL
from .http import nflverse_csv
from .stats import to_nflverse_team

SOS_POSITIONS = ("QB", "RB", "WR", "TE")
SHRINK_GAMES = 7.0


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def points_allowed(season: str | int, reception_points: float = 1.0) -> dict[str, Any]:
    """Fantasy points each defense has allowed per game to each position.

    Points are nflverse standard fantasy points plus reception_points per
    catch, so they follow the league's PPR setting. League bonuses (yardage
    thresholds and the like) are not modelled.
    """
    rows = nflverse_csv(
        "stats_player",
        f"stats_player_week_{season}.csv",
        row_filter=lambda r: (r.get("season_type") or "REG") == "REG"
        and r.get("position") in SOS_POSITIONS,
        ttl=STATS_CACHE_TTL,
    )
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    weeks: dict[str, set] = defaultdict(set)
    last_week = 0
    for row in rows or []:
        defense = to_nflverse_team(row.get("opponent_team"))
        if not defense:
            continue
        week = int(_num(row.get("week")))
        pts = _num(row.get("fantasy_points")) + reception_points * _num(row.get("receptions"))
        totals[defense][row["position"]] += pts
        weeks[defense].add(week)
        last_week = max(last_week, week)

    team_games = sum(len(w) for w in weeks.values())
    league_avg = {
        pos: (sum(t[pos] for t in totals.values()) / team_games if team_games else 0.0)
        for pos in SOS_POSITIONS
    }
    by_defense = {
        defense: {
            pos: {"games": len(weeks[defense]), "per_game": totals[defense][pos] / len(weeks[defense])}
            for pos in SOS_POSITIONS
        }
        for defense in totals
    }
    return {
        "by_defense": by_defense,
        "league_avg": league_avg,
        "through_week": last_week,
        "reception_points": reception_points,
    }


def remaining_opponents(season: str | int, first_week: int, last_week: int) -> dict[str, list]:
    """Each team's opponent per week in [first_week, last_week]; None is a bye."""
    season_s = str(season)
    rows = nflverse_csv(
        "schedules",
        "games.csv",
        row_filter=lambda r: str(r.get("season") or "") == season_s
        and (r.get("game_type") or "REG") == "REG",
        ttl=STATS_CACHE_TTL,
    )
    span = list(range(int(first_week), int(last_week) + 1))
    opp: dict[str, dict[int, str]] = defaultdict(dict)
    for row in rows or []:
        week = int(_num(row.get("week")))
        if week not in span:
            continue
        home, away = to_nflverse_team(row.get("home_team")), to_nflverse_team(row.get("away_team"))
        opp[home][week] = away
        opp[away][week] = home
    return {team: [by_week.get(w) for w in span] for team, by_week in opp.items()}


def matchup_factor(defense: str, position: str, allowed: dict[str, Any]) -> float:
    """Shrunk ratio of a defense's points allowed to the league average; 1.0 is average."""
    avg = allowed["league_avg"].get(position) or 0.0
    rec = (allowed["by_defense"].get(defense) or {}).get(position)
    if not avg or not rec:
        return 1.0
    weight = rec["games"] / (rec["games"] + SHRINK_GAMES)
    return 1.0 + weight * (rec["per_game"] / avg - 1.0)


def schedule_factor(
    team: str | None,
    position: str | None,
    allowed: dict[str, Any],
    opponents: dict[str, list],
) -> dict[str, Any]:
    """How a player's remaining schedule compares with an average one.

    factor      mean matchup factor over the games left (1.05 = 5% softer)
    multiplier  factor scaled by games / weeks, so remaining byes count
    Unsupported positions and unknown teams come back as nulls with a reason.
    """
    pos = (position or "").split(",")[0].strip().upper()
    nfl_team = to_nflverse_team(team)
    through = f"measured:through_week_{allowed.get('through_week')}"
    if pos not in SOS_POSITIONS:
        return {"factor": None, "multiplier": None, "games": None, "byes": None,
                "provenance": "unsourced"}
    if not nfl_team or nfl_team not in opponents:
        return {"factor": None, "multiplier": None, "games": None, "byes": None,
                "provenance": "missing:no_team_schedule"}
    schedule = opponents[nfl_team]
    games = [d for d in schedule if d]
    factors = [matchup_factor(d, pos, allowed) for d in games]
    factor = sum(factors) / len(factors) if factors else 1.0
    multiplier = sum(factors) / len(schedule) if schedule else 1.0
    return {
        "factor": round(factor, 4),
        "multiplier": round(multiplier, 4),
        "games": len(games),
        "byes": len(schedule) - len(games),
        "provenance": through,
    }

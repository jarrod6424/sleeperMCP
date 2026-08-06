"""
nflverse weekly stats: column selection, type coercion, player matching.

nflverse publishes open NFL data as CSVs on GitHub releases (MIT licensed, no
auth). The weekly player stats file carries ~115 columns; STAT_KEEP trims that
to the couple of dozen worth surfacing, because handing an LLM 115 columns per
player-week is mostly noise.

Everything here is per-season, and a completed season never changes — which is
what makes these the most stable tools in the codebase.
"""

from __future__ import annotations

from .config import SPORT
from .http import get_json

# Columns worth keeping from the weekly player stats file.
STAT_KEEP = {
    "player_display_name", "position", "team", "week", "season", "opponent_team",
    "targets", "receptions", "receiving_yards", "receiving_tds",
    "receiving_air_yards", "receiving_yards_after_catch",
    "target_share", "air_yards_share", "wopr", "racr",
    "carries", "rushing_yards", "rushing_tds",
    "completions", "attempts", "passing_yards", "passing_tds",
    "passing_interceptions", "passing_air_yards", "passing_cpoe",
    "fantasy_points", "fantasy_points_ppr",
}

# Columns that should come back as numbers, not strings. CSV gives us strings
# for everything, so without this every stat would sort lexicographically —
# "9" would outrank "10".
NUMERIC = {
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


def current_season() -> str:
    """The active NFL season per Sleeper's state endpoint."""
    state = get_json(f"/state/{SPORT}", cache=True) or {}
    return str(state.get("season") or state.get("league_season") or "2024")


def coerce(row: dict, keep: set[str]) -> dict:
    """Keep only the wanted columns and turn numeric strings into numbers.

    Drops R-style missing markers ("NA", "NULL", "") rather than passing them
    through as strings, so a missing stat is absent instead of misleading.
    """
    out = {}
    for k, v in row.items():
        if k not in keep:
            continue
        if v in ("", "NA", "NULL", "None"):
            continue
        if k in NUMERIC:
            try:
                out[k] = float(v) if "." in str(v) else int(v)
                continue
            except (ValueError, TypeError):
                pass
        out[k] = v
    return out


def match_player(rows: list[dict], name: str, name_col: str) -> list[dict]:
    """Case-insensitive substring match on a name column.

    Substring rather than exact: nflverse and Sleeper disagree on punctuation
    and suffixes often enough ("Amon-Ra St. Brown", "Michael Pittman Jr.")
    that exact matching drops real players.
    """
    needle = name.strip().lower()
    return [r for r in rows if needle in (r.get(name_col) or "").lower()]

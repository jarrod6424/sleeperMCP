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

from .config import DEPTH_CHART_CACHE_TTL, NFLVERSE_SOURCE, SPORT
from .http import get_json, nflverse_csv

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


# Sleeper and nflverse disagree on some team abbreviations. The live one is the
# Rams: Sleeper says LAR, nflverse says LA. Querying nflverse with "LAR" matched
# nothing and returned an empty result — which custom_score_player then treated
# as "no crowding data" and silently scored at the neutral default, so every
# Rams player got a quietly neutered offensive-context component.
#
# The rest are relocation and style variants included so a caller typing any
# common form gets an answer rather than silence.
NFLVERSE_TEAM_ALIASES = {
    "LAR": "LA",   # Rams — the one that actually bites
    "STL": "LA",
    "OAK": "LV",
    "LVR": "LV",
    "SD": "LAC",
    "SDG": "LAC",
    "JAC": "JAX",
    "WSH": "WAS",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
}


def to_nflverse_team(team: str | None) -> str:
    """Normalise a team abbreviation to the form nflverse files use."""
    up = (team or "").strip().upper()
    return NFLVERSE_TEAM_ALIASES.get(up, up)


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


# --------------------------------------------------------------------------
# Depth charts
# --------------------------------------------------------------------------
# nflverse changed its depth chart source after the 2024 season and renamed
# every column this code reads. Both layouts are supported, because a query
# for 2024 still hits the old files.
#
#   2025+     dt  team  player_name  pos_abb   pos_rank    pos_grp
#   <=2024    -   club_code  full_name  position  depth_team  formation
#
# The 2025 schema also dropped week entirely, replacing it with dt, an ISO8601
# load timestamp. ISO8601 sorts correctly as a string, so "latest snapshot"
# works for both once you know which column to read.

DEPTH_SCHEMAS = (
    {
        "label": "2025+",
        "team": "team",
        "name": "player_name",
        "pos": "pos_abb",
        "rank": "pos_rank",
        "group": "pos_grp",
        "stamp": "dt",
        "stamp_numeric": False,
    },
    {
        "label": "legacy",
        "team": "club_code",
        "name": "full_name",
        "pos": "position",
        "rank": "depth_team",
        "group": "formation",
        "stamp": "week",
        "stamp_numeric": True,
    },
)


# The 2025+ feed reports alignment, not fantasy position: a receiver is LWR,
# RWR or SWR depending on where he lines up, never plain "WR". Asking for "WR"
# has to expand to all three or it matches nothing.
FANTASY_POS_ALIASES = {
    "QB": {"QB"},
    "RB": {"RB", "HB", "TB", "FB"},
    "WR": {"WR", "LWR", "RWR", "SWR"},
    "TE": {"TE", "TE2"},
    "K": {"K", "PK"},
}

# Fantasy-relevant groups first. The unfiltered chart is mostly defense, and an
# LLM reading top-down should hit the positions that can be started, not the
# defensive line.
_POS_ORDER = ["QB", "RB", "HB", "TB", "FB", "LWR", "RWR", "SWR", "WR", "TE", "TE2", "K", "PK"]


def expand_position(position: str) -> set[str]:
    """Fantasy position -> the alignment codes that satisfy it."""
    up = position.strip().upper()
    return FANTASY_POS_ALIASES.get(up, {up})


def _pos_sort_key(pos: str) -> tuple[int, str]:
    try:
        return (_POS_ORDER.index(pos), pos)
    except ValueError:
        return (len(_POS_ORDER), pos)


def detect_depth_schema(rows: list[dict]) -> dict | None:
    """Identify which depth chart layout a file uses, by column presence."""
    if not rows:
        return None
    sample = rows[0]
    for schema in DEPTH_SCHEMAS:
        if schema["team"] in sample:
            return schema
    return None


def depth_chart(team: str, position: str | None = None, season: str | None = None) -> dict:
    """Latest depth chart for one team, in whichever schema the season uses.

    Filters by team during the CSV parse rather than after. depth_charts_2025
    is a 50 MB file (see nflverse_csv) and materializing all of it costs
    hundreds of megabytes of Python objects — enough to kill a small container.
    One team's rows are a rounding error by comparison.
    """
    season = season or current_season()
    team_up = to_nflverse_team(team)

    # The team column differs by schema, and we do not know which schema the
    # file uses until we have seen a row — so the predicate checks both. Only
    # one of the two columns exists in any given file. The closure also grabs
    # the header off the first row, so an unrecognized layout can still be
    # reported even when nothing matches.
    header: list[str] = []

    def keep(row: dict) -> bool:
        if not header:
            header.extend(row.keys())
        return (row.get("team") or row.get("club_code") or "").upper() == team_up

    rows = nflverse_csv("depth_charts", f"depth_charts_{season}.csv",
                        row_filter=keep, ttl=DEPTH_CHART_CACHE_TTL)

    if not header:
        return {"error": "no depth chart data", "season": season}

    schema = detect_depth_schema(rows) or detect_depth_schema([dict.fromkeys(header)])
    if schema is None:
        return {
            "error": "unrecognized depth chart schema",
            "season": season,
            "columns_seen": sorted(header)[:15],
        }

    wanted = expand_position(position) if position else None
    matched = [
        r for r in rows
        if wanted is None or (r.get(schema["pos"]) or "").upper() in wanted
    ]
    if not matched:
        return {
            "error": "no depth chart found",
            "team": team,
            "position": position,
            "season": season,
            "schema": schema["label"],
        }

    def stamp_of(r: dict):
        raw = r.get(schema["stamp"]) or ""
        if schema["stamp_numeric"]:
            try:
                return int(raw)
            except (ValueError, TypeError):
                return 0
        return str(raw)

    latest = max(stamp_of(r) for r in matched)
    current = [r for r in matched if stamp_of(r) == latest]

    by_pos: dict[str, list] = {}
    for r in current:
        try:
            depth = int(float(r.get(schema["rank"]) or 99))
        except (ValueError, TypeError):
            depth = 99
        by_pos.setdefault((r.get(schema["pos"]) or "UNK").upper(), []).append({
            "name": r.get(schema["name"]),
            "depth": depth,
            "formation": r.get(schema["group"]) or None,
        })
    for pos_key in by_pos:
        by_pos[pos_key].sort(key=lambda e: e["depth"])

    ordered = {k: by_pos[k] for k in sorted(by_pos, key=_pos_sort_key)}

    return {
        "team": team_up,
        "season": season,
        "as_of": latest,
        "schema": schema["label"],
        "source": NFLVERSE_SOURCE,
        "depth_chart": ordered,
    }

"""
Configuration: endpoints, identity defaults, cache locations and TTLs.

Everything here is environment-driven with a sensible fallback, so the same
code runs locally, on Horizon, and inside the draft app without edits.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root. config.py lives in sleeper_core/, so the root is two levels up.
# Anything resolving a bundled data file must go through this rather than
# Path(__file__).parent, which now points at the package, not the repo.
ROOT_DIR = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# Sleeper — documented API
# --------------------------------------------------------------------------

BASE_URL = "https://api.sleeper.app/v1"
DEFAULT_LEAGUE_ID = os.environ.get("SLEEPER_LEAGUE_ID", "1312218810614300672")
SPORT = os.environ.get("SLEEPER_SPORT", "nfl")

# Identity used to resolve "my team" without passing a roster_id.
DEFAULT_USERNAME = os.environ.get("SLEEPER_USERNAME", "JarrodLee")
DEFAULT_TEAM_NAME = os.environ.get("SLEEPER_TEAM_NAME", "Pine Bluff Escapees")

# --------------------------------------------------------------------------
# Sleeper — undocumented host (stats and projections)
# --------------------------------------------------------------------------
# Not part of the supported API. Can change or disappear without notice, which
# is why it gets its own client and its own failure domain.

ALT_BASE_URL = "https://api.sleeper.com"

# --------------------------------------------------------------------------
# FantasyCalc — trade values (third party)
# --------------------------------------------------------------------------
# Semi-official: documented by FantasyCalc in a guest post, but with no formal
# API docs or stated rate limits.

FC_BASE_URL = "https://api.fantasycalc.com"
FC_SOURCE = "fantasycalc.com trade values (unofficial, third party)"

# --------------------------------------------------------------------------
# FantasyFootballCalculator — ADP (third party)
# --------------------------------------------------------------------------
# A separate source, because FantasyCalc does not serve ADP. Their response
# carries a maybeAdp field but it is null for every player, with or without an
# includeAdp parameter. FantasyCalc's own API walkthrough pulls ADP from here
# instead, which is a fair signal that this is the conventional source.

FFC_BASE_URL = "https://fantasyfootballcalculator.com"
FFC_SOURCE = "fantasyfootballcalculator.com ADP (unofficial, third party)"

# --------------------------------------------------------------------------
# nflverse — open data on GitHub releases (MIT licensed)
# --------------------------------------------------------------------------

NFLVERSE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"
NFLVERSE_SOURCE = "nflverse open data (MIT licensed, github.com/nflverse)"

# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------
# On an ephemeral host this directory may not survive a restart. That is fine:
# every cache degrades to a refetch. It is worth pointing SLEEPER_CACHE_DIR at
# a writable path anyway, since the player map is ~5 MB and the nflverse CSVs
# are 2-7 MB per season.

CACHE_DIR = Path(
    os.environ.get("SLEEPER_CACHE_DIR", Path.home() / ".cache" / "sleeper-mcp")
)

PLAYER_CACHE_TTL = 18 * 60 * 60  # Sleeper asks for at most one fetch per day
PROJ_CACHE_TTL = 6 * 60 * 60     # revised through the week as news breaks
FC_CACHE_TTL = 6 * 60 * 60       # FantasyCalc recomputes daily
FFC_CACHE_TTL = 24 * 60 * 60     # ADP is a 30-day rolling aggregate, moves slowly

# nflverse TTLs are split by how fast each dataset actually moves. A single
# value was wrong in both directions: depth_charts_2026.csv had not changed in
# two months, so a 6h TTL re-fetched an identical file four times a day, while
# injuries at the same 6h could be badly stale on a game day.
#
# These are "how often to check", not "how often to download" — conditional
# requests mean an unchanged file costs a 304 with no body.
NFLVERSE_CACHE_TTL = 6 * 60 * 60        # default for anything unclassified
DEPTH_CHART_CACHE_TTL = 24 * 60 * 60    # teams file depth charts weekly
STATS_CACHE_TTL = 24 * 60 * 60          # only changes once games are complete
INJURY_CACHE_TTL = 1 * 60 * 60          # the one that genuinely needs freshness

MEM_TTL = 3600.0  # short-lived in-process cache, avoids hammering within a session

# --------------------------------------------------------------------------
# Bundled data
# --------------------------------------------------------------------------

OC_TIERS_FILE = Path(
    os.environ.get("SLEEPER_OC_TIERS_FILE", ROOT_DIR / "oc_tiers.json")
)

# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

USER_AGENT = "sleeper-mcp-readonly/1.0"
DEFAULT_TIMEOUT = 30.0
NFLVERSE_TIMEOUT = 60.0  # larger files, slower origin

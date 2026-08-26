"""
Yahoo Fantasy configuration: OAuth credentials, league identity, cache paths.

Yahoo requires OAuth 2.0 for every league-scoped call. Sleeper needs none of
this, so it lives in its own package rather than sleeper_core/config.py.
"""

from __future__ import annotations

import os

from pathlib import Path

from sleeper_core.config import CACHE_DIR, USER_AGENT

YAHOO_API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
YAHOO_TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

DEFAULT_LEAGUE_KEY = os.environ.get("YAHOO_LEAGUE_KEY", "")
DEFAULT_TEAM_NAME = os.environ.get("YAHOO_TEAM_NAME", "")

YAHOO_CLIENT_ID = os.environ.get("YAHOO_CLIENT_ID", "")
YAHOO_CLIENT_SECRET = os.environ.get("YAHOO_CLIENT_SECRET", "")
YAHOO_REFRESH_TOKEN = os.environ.get("YAHOO_REFRESH_TOKEN", "")

# Optional full token blob (access + refresh + expiry). When set, it wins over
# the discrete YAHOO_REFRESH_TOKEN above and is updated in place after refresh.
YAHOO_TOKEN_JSON = os.environ.get("YAHOO_TOKEN_JSON", "")

TOKEN_CACHE_FILE = Path(
    os.environ.get("YAHOO_TOKEN_CACHE_FILE", CACHE_DIR / "yahoo_oauth.json")
)

YAHOO_USER_AGENT = f"{USER_AGENT} yahoo"

# Scoring format used when projecting Yahoo lineups with Sleeper projection
# fields. Prefer parsed Yahoo reception modifiers when available; this is the
# fallback: ppr | half_ppr | std
YAHOO_SCORING_FORMAT = os.environ.get("YAHOO_SCORING_FORMAT", "ppr").strip().lower()

# Auction budget for get_auction_budgets(platform="yahoo"). Yahoo settings do
# not always expose a budget; default $200 when unset.
_raw_auction = os.environ.get("YAHOO_AUCTION_BUDGET", "").strip()
YAHOO_AUCTION_BUDGET = int(_raw_auction) if _raw_auction.isdigit() else None

"""
Yahoo Fantasy HTTP client (OAuth bearer, JSON responses).
"""

from __future__ import annotations

from typing import Any

import httpx

from .auth import auth_status, get_access_token
from .config import YAHOO_API_BASE, YAHOO_USER_AGENT


class YahooConfigError(RuntimeError):
    """Raised when Yahoo credentials or league configuration is missing."""


def get_json(path: str) -> dict[str, Any]:
    """GET a Yahoo fantasy resource and return parsed JSON."""
    try:
        token = get_access_token()
    except RuntimeError as exc:
        raise YahooConfigError(str(exc)) from exc

    url = f"{YAHOO_API_BASE}/{path.lstrip('/')}"
    if "format=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}format=json"

    response = httpx.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": YAHOO_USER_AGENT,
        },
        timeout=30.0,
    )
    if response.status_code == 401:
        raise YahooConfigError(
            "Yahoo rejected the access token. Re-run python tools/yahoo_auth.py "
            f"or refresh credentials. Status: {auth_status()}"
        )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}

"""
Yahoo OAuth 2.0 token load, refresh, and persistence.

The MCP server never runs a browser. Run `python tools/yahoo_auth.py` once
locally to obtain a refresh token, then deploy the token via environment
variables or the on-disk cache file this module maintains.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

from .config import (
    TOKEN_CACHE_FILE,
    YAHOO_CLIENT_ID,
    YAHOO_CLIENT_SECRET,
    YAHOO_REFRESH_TOKEN,
    YAHOO_TOKEN_JSON,
    YAHOO_TOKEN_URL,
    YAHOO_USER_AGENT,
)

_TOKEN_SKEW_SECONDS = 60


def _read_cache() -> dict[str, Any] | None:
    if not TOKEN_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_cache(data: dict[str, Any]) -> None:
    TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _env_token() -> dict[str, Any] | None:
    if YAHOO_TOKEN_JSON.strip():
        try:
            data = json.loads(YAHOO_TOKEN_JSON)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
    if YAHOO_REFRESH_TOKEN.strip():
        return {"refresh_token": YAHOO_REFRESH_TOKEN.strip()}
    return None


def _expires_at(token: dict[str, Any]) -> float:
    if "expires_at" in token:
        return float(token["expires_at"])
    created = float(token.get("created_at", 0))
    expires_in = float(token.get("expires_in", 3600))
    return created + expires_in


def _is_valid(token: dict[str, Any]) -> bool:
    access = token.get("access_token")
    if not access:
        return False
    return time.time() < (_expires_at(token) - _TOKEN_SKEW_SECONDS)


def _refresh(refresh_token: str) -> dict[str, Any]:
    if not YAHOO_CLIENT_ID or not YAHOO_CLIENT_SECRET:
        raise RuntimeError(
            "YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET are required to refresh tokens"
        )
    auth = base64.b64encode(
        f"{YAHOO_CLIENT_ID}:{YAHOO_CLIENT_SECRET}".encode()
    ).decode()
    response = httpx.post(
        YAHOO_TOKEN_URL,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": YAHOO_USER_AGENT,
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30.0,
    )
    response.raise_for_status()
    data = response.json()
    now = time.time()
    data.setdefault("created_at", now)
    data["expires_at"] = now + float(data.get("expires_in", 3600))
    if "refresh_token" not in data:
        data["refresh_token"] = refresh_token
    return data


def get_access_token() -> str:
    """Return a valid access token, refreshing and persisting when needed."""
    candidates: list[dict[str, Any]] = []
    env = _env_token()
    if env:
        candidates.append(env)
    cached = _read_cache()
    if cached:
        candidates.append(cached)

    token = candidates[0] if candidates else None
    if token and _is_valid(token):
        return str(token["access_token"])

    refresh_token = None
    for candidate in candidates:
        refresh_token = candidate.get("refresh_token")
        if refresh_token:
            break
    if not refresh_token:
        raise RuntimeError(
            "Yahoo is not configured. Set YAHOO_TOKEN_JSON or YAHOO_REFRESH_TOKEN "
            "(plus YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET), or run "
            "python tools/yahoo_auth.py to create a token cache."
        )

    refreshed = _refresh(str(refresh_token))
    _write_cache(refreshed)
    return str(refreshed["access_token"])


def auth_status() -> dict[str, Any]:
    """Non-secret summary for error messages."""
    return {
        "client_id_set": bool(YAHOO_CLIENT_ID),
        "client_secret_set": bool(YAHOO_CLIENT_SECRET),
        "refresh_token_set": bool(YAHOO_REFRESH_TOKEN or YAHOO_TOKEN_JSON),
        "token_cache": str(TOKEN_CACHE_FILE),
        "token_cache_exists": TOKEN_CACHE_FILE.exists(),
    }

"""One-time Yahoo OAuth setup: obtain and cache a refresh token."""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yahoo_core.config import TOKEN_CACHE_FILE, YAHOO_TOKEN_URL, YAHOO_USER_AGENT


def _basic_auth(client_id: str, client_secret: str) -> str:
    import base64

    return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


def _exchange_code(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
) -> dict:
    response = httpx.post(
        YAHOO_TOKEN_URL,
        headers={
            "Authorization": f"Basic {_basic_auth(client_id, client_secret)}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": YAHOO_USER_AGENT,
        },
        data={
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="Obtain Yahoo Fantasy OAuth tokens")
    parser.add_argument("--client-id", default=os.environ.get("YAHOO_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.environ.get("YAHOO_CLIENT_SECRET"))
    parser.add_argument(
        "--redirect-uri",
        default=os.environ.get("YAHOO_REDIRECT_URI", "oob"),
        help='Registered redirect URI. Use "oob" for out-of-band desktop apps.',
    )
    parser.add_argument(
        "--out",
        default=str(TOKEN_CACHE_FILE),
        help="Where to write the token JSON cache",
    )
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        print(
            "Set YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET (or pass --client-id/--client-secret).",
            file=sys.stderr,
        )
        return 1

    auth_url = (
        "https://api.login.yahoo.com/oauth2/request_auth"
        f"?client_id={args.client_id}&redirect_uri={args.redirect_uri}&response_type=code"
    )
    print("Open this URL in a browser, approve access, then paste the redirect URL or code:\n")
    print(auth_url)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    pasted = input("\nPaste authorization code or full redirect URL: ").strip()
    if pasted.startswith("http"):
        parsed = urlparse(pasted)
        code = parse_qs(parsed.query).get("code", [None])[0]
    else:
        code = pasted
    if not code:
        print("No authorization code found.", file=sys.stderr)
        return 1

    token = _exchange_code(args.client_id, args.client_secret, args.redirect_uri, code)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(token, indent=2), encoding="utf-8")

    print(f"\nWrote token cache to {out_path}")
    print("Add these to your environment (or .env):")
    print(f"YAHOO_CLIENT_ID={args.client_id}")
    print(f"YAHOO_CLIENT_SECRET={args.client_secret}")
    if token.get("refresh_token"):
        print(f"YAHOO_REFRESH_TOKEN={token['refresh_token']}")
    print(f"YAHOO_TOKEN_JSON={json.dumps(token)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Smoke-test a running MCP server over Streamable HTTP.

    python tests/smoke_http.py                          # local, default port
    python tests/smoke_http.py https://your-horizon-url/mcp
    python tests/smoke_http.py <url> --token <bearer>

Runs a real MCP conversation: initialize, notifications/initialized,
tools/list, then one actual tool call. That last step matters — a server can
advertise 37 tools and still fail the moment one of them touches the network,
which is exactly what a cold container or a blocked datacenter IP looks like.

Use it twice: once against localhost to confirm the server works on your
machine, then against the Horizon URL to confirm it works from the internet.
Anything that passes locally and fails remotely is a hosting problem, not a
code problem, and that is a useful thing to be able to tell apart.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import httpx

PROTOCOL_VERSION = "2025-06-18"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def parse_body(text: str):
    """Streamable HTTP may answer as JSON or as an SSE 'data:' frame."""
    m = re.search(r"^data: (.*)$", text, re.M)
    return json.loads(m.group(1) if m else text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default="http://127.0.0.1:8000/mcp")
    ap.add_argument("--token", help="bearer token, if the server requires one")
    ap.add_argument("--tool", default="get_nfl_state", help="tool to call")
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    headers = dict(HEADERS)
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    print(f"target: {args.url}\n")
    client = httpx.Client(timeout=args.timeout, follow_redirects=True)

    # 1. initialize
    r = client.post(args.url, headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                   "clientInfo": {"name": "smoke", "version": "0"}},
    })
    if r.status_code != 200:
        print(f"FAIL  initialize -> HTTP {r.status_code}")
        print(r.text[:400])
        if r.status_code in (401, 403):
            print("\n      Auth rejected. Check the token, or whether the server "
                  "expects OAuth rather than a bearer header.")
        return 1

    info = parse_body(r.text)["result"]["serverInfo"]
    session = r.headers.get("mcp-session-id")
    print(f"PASS  initialize      {info['name']} v{info['version']}")
    print(f"      session         {session or '(none)'}")
    if session:
        headers["mcp-session-id"] = session

    client.post(args.url, headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    # 2. tools/list
    r = client.post(args.url, headers=headers,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = parse_body(r.text)["result"]["tools"]
    undocumented = [t["name"] for t in tools if not t.get("description")]
    print(f"PASS  tools/list      {len(tools)} tools")
    if undocumented:
        print(f"WARN  no description: {undocumented}")
        print("      Descriptions are how Claude picks a tool — a missing one "
              "means that tool effectively will not be called.")

    # 3. a real call, which is where upstream problems actually surface
    r = client.post(args.url, headers=headers, json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": args.tool, "arguments": {}},
    })
    payload = parse_body(r.text)
    if "error" in payload:
        print(f"FAIL  {args.tool} -> {payload['error']}")
        return 1

    result = payload["result"]
    if result.get("isError"):
        print(f"FAIL  {args.tool} returned an error:")
        print("     ", str(result.get("content"))[:400])
        print("\n      The server is up but the call failed. Usually an upstream "
              "problem: rate limiting, or a datacenter IP being treated "
              "differently from a home connection.")
        return 1

    body = result.get("structuredContent") or result.get("content")
    print(f"PASS  {args.tool}   {str(body)[:160]}")
    print("\nAll good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
The four HTTP clients and the low-level fetchers built on them.

Why four clients and not one: each upstream has a different reliability
profile. Sleeper's documented API is stable. The undocumented projections host
can vanish without notice. FantasyCalc has no published rate limits. nflverse
serves multi-megabyte files off GitHub releases and needs a longer timeout.
Keeping them separate means a failure in one does not take the other three
down with it.

All calls are read-only. There are no write paths in this module.

Note on sync vs async: these are synchronous httpx clients, matching the rest
of the codebase. Under HTTP transport FastMCP runs sync tools in a threadpool,
so each in-flight call holds a worker thread while blocked on the network.
Fine for a person asking questions from a phone. If the draft app needs
concurrency, it should import this module in-process and manage its own
threads rather than fanning out over MCP.
"""

from __future__ import annotations

import csv
import gzip
import io
import time
from typing import Any, Callable

import httpx

from .config import (
    ALT_BASE_URL,
    BASE_URL,
    CACHE_DIR,
    DEFAULT_TIMEOUT,
    FC_BASE_URL,
    MEM_TTL,
    NFLVERSE_BASE,
    NFLVERSE_CACHE_TTL,
    NFLVERSE_TIMEOUT,
    USER_AGENT,
)

_HEADERS = {"User-Agent": USER_AGENT}

# Clients are constructed at import. Fine for a long-running server; if the app
# ever needs explicit lifecycle control, make these lazy and add an aclose_all().
_client = httpx.Client(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT, headers=_HEADERS)
_alt_client = httpx.Client(base_url=ALT_BASE_URL, timeout=DEFAULT_TIMEOUT, headers=_HEADERS)
_fc_client = httpx.Client(base_url=FC_BASE_URL, timeout=DEFAULT_TIMEOUT, headers=_HEADERS)
_nfl_client = httpx.Client(follow_redirects=True, timeout=NFLVERSE_TIMEOUT, headers=_HEADERS)

# --------------------------------------------------------------------------
# In-process caches
# --------------------------------------------------------------------------
# Module-level, so they are shared by every importer of this package. That is
# what you want for the server. If the draft app ever needs isolated caches,
# wrap these in a class it can instantiate per session.

_mem_cache: dict[str, tuple[float, Any]] = {}

# Parsed nflverse CSVs (2-7 MB each; re-parsing per call is the expensive part).
_csv_mem_cache: dict[str, list[dict]] = {}
_csv_mem_cache_ts: dict[str, float] = {}


def clear_caches() -> None:
    """Drop every in-process cache. Useful in tests and after a long idle."""
    _mem_cache.clear()
    _csv_mem_cache.clear()
    _csv_mem_cache_ts.clear()


# --------------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------------


def get_json(path: str, *, cache: bool = False) -> Any:
    """GET a Sleeper API path and return parsed JSON. Read-only.

    A 404 returns None rather than raising: several Sleeper endpoints use it to
    mean "nothing here yet" (no bracket, no picks), which is not an error.
    """
    if cache:
        hit = _mem_cache.get(path)
        if hit and (time.time() - hit[0]) < MEM_TTL:
            return hit[1]
    resp = _client.get(path)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    if cache:
        _mem_cache[path] = (time.time(), data)
    return data


def alt_get(path: str, params: list | None = None) -> Any:
    """GET against the undocumented api.sleeper.com host. Read-only."""
    resp = _alt_client.get(path, params=params)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def fc_get(path: str, params: list | None = None) -> Any:
    """GET against the FantasyCalc API. Read-only."""
    resp = _fc_client.get(path, params=params)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def nflverse_csv(
    tag: str,
    filename: str,
    row_filter: Callable[[dict], bool] | None = None,
) -> list[dict]:
    """Download and disk-cache an nflverse CSV, returning parsed rows.

    Returns [] on any failure rather than raising. nflverse is a nice-to-have
    enrichment source; if GitHub is unreachable the stats tools should degrade,
    not take down the twenty-odd tools that never touch it.

    row_filter is a predicate applied while parsing, and it is not an
    optimization you can skip on the big files. depth_charts_2025.csv is
    50 MB — the 2025 schema swapped the week column for a dt load timestamp,
    so the file is every snapshot ever taken rather than one per week. Turning
    that into a list of dicts costs several hundred MB of Python objects and
    will OOM a small container. Filtering during the parse keeps only the rows
    that matter, usually a few hundred.

    When row_filter is given the parsed-row cache is bypassed in both
    directions: the result is specific to that predicate, and caching the full
    unfiltered file is exactly the thing being avoided.
    """
    now = time.time()
    if row_filter is None:
        hit = _csv_mem_cache.get(filename)
        if hit is not None and (now - _csv_mem_cache_ts.get(filename, 0)) < NFLVERSE_CACHE_TTL:
            return hit

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / filename
    if cache_file.exists() and (now - cache_file.stat().st_mtime) < NFLVERSE_CACHE_TTL:
        raw = cache_file.read_bytes()
    else:
        try:
            resp = _nfl_client.get(f"{NFLVERSE_BASE}/{tag}/{filename}")
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            raw = resp.content
        except Exception:
            return []
        try:
            cache_file.write_bytes(raw)
        except OSError:
            pass

    try:
        text = gzip.decompress(raw).decode("utf-8") if filename.endswith(".gz") else raw.decode("utf-8")
    except Exception:
        return []
    del raw  # release the encoded copy before building rows

    try:
        reader = csv.DictReader(io.StringIO(text))
        if row_filter is None:
            rows = list(reader)
        else:
            rows = [r for r in reader if row_filter(r)]
    except Exception:
        return []
    finally:
        del text

    if row_filter is None:
        _csv_mem_cache[filename] = rows
        _csv_mem_cache_ts[filename] = now
    return rows

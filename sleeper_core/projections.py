"""
Weekly projections and lineup optimization.

UNOFFICIAL. Everything that fetches here goes through Sleeper's undocumented
projections endpoint on api.sleeper.com. It is not part of the supported API,
its response shape is not guaranteed, and Sleeper has pulled stats endpoints
before at a data provider's request. Treat it as best-effort: it may stop
working at any time, and when it does only the projection tools should notice.

That is also why normalize() accepts two different payload shapes. An
undocumented endpoint is free to return a list one week and a dict the next.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .config import CACHE_DIR, PROJ_CACHE_TTL, SPORT
from .http import alt_get, get_json

# Which positions may fill which lineup slot. Anything not listed falls back to
# "only its own name", so an unknown slot never silently accepts everyone.
SLOT_ELIGIBILITY: dict[str, set] = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "DEF": {"DEF"},
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}

# Slots that hold players but do not score.
SKIP_SLOTS = {"BN", "IR", "TAXI", "NA"}


def normalize(raw: Any) -> dict[str, dict]:
    """Reduce a projections payload to {player_id: stats_dict}.

    Handles both a list of records and a dict keyed by player_id, because the
    undocumented shape is not guaranteed to stay the same.
    """
    out: dict[str, dict] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            pid = item.get("player_id") or (item.get("player") or {}).get("player_id")
            if pid is not None:
                out[str(pid)] = item.get("stats") or {}
    elif isinstance(raw, dict):
        for pid, item in raw.items():
            if isinstance(item, dict):
                out[str(pid)] = item.get("stats") or item
    return out


def projections_for(season: str, week: int) -> dict[str, dict]:
    """Normalized weekly projections, disk-cached for a few hours.

    Cached per season+week, so a completed week is fetched once and never
    again — only the live week actually churns.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"proj_{SPORT}_{season}_wk{week}.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < PROJ_CACHE_TTL:
        try:
            return json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            pass

    params = [("season_type", "regular")]
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        params.append(("position[]", pos))
    raw = alt_get(f"/projections/{SPORT}/{season}/{week}", params=params)
    norm = normalize(raw)
    try:
        cache_file.write_text(json.dumps(norm))
    except OSError:
        pass
    return norm


def scoring_field(lid: str) -> tuple[str, str]:
    """Pick the projection field matching the league's scoring, from points
    per reception. Returns (field_name, human_label)."""
    league = get_json(f"/league/{lid}", cache=True) or {}
    rec = (league.get("scoring_settings") or {}).get("rec", 0) or 0
    if rec >= 1:
        return "pts_ppr", "PPR"
    if rec >= 0.5:
        return "pts_half_ppr", "Half-PPR"
    return "pts_std", "Standard"


def proj_points(pid: str, proj: dict, field: str) -> float:
    """One player's projected points, falling back across scoring formats.

    A player missing the league's exact field still gets a number rather than a
    zero, which would otherwise read as "projected to score nothing" and bury
    them at the bottom of a lineup ranking.
    """
    stats = proj.get(str(pid)) or {}
    val = stats.get(field)
    if val is None:
        val = stats.get("pts_ppr") or stats.get("pts_half_ppr") or stats.get("pts_std")
    return round(float(val), 2) if val is not None else 0.0


def optimal_lineup(slots: list[str], pool: list[dict], score_key: str = "proj") -> list[dict]:
    """Greedy best-ball lineup: fill the most restrictive slots first, each
    with the highest-scored eligible player still available.

    Restrictive-first matters. Filling FLEX before QB could hand your only
    quarterback to a SUPER_FLEX and leave QB empty. Sorting slots by how few
    positions they accept avoids that.

    score_key lets start/sit tilt the greedy order (projection vs floor)
    without changing eligibility. A heuristic, not a provably optimal
    assignment, but reliable for spotting an obvious mistake.
    """
    ranked = sorted(
        pool,
        key=lambda p: p.get(score_key) if p.get(score_key) is not None else p.get("proj", 0.0),
        reverse=True,
    )
    used: set = set()
    assigned: list[dict] = []
    order = sorted(
        range(len(slots)),
        key=lambda i: len(SLOT_ELIGIBILITY.get(slots[i], {slots[i]})),
    )
    for i in order:
        slot = slots[i]
        elig = SLOT_ELIGIBILITY.get(slot, {slot})
        pick = next(
            (p for p in ranked if p["player_id"] not in used and p["position"] in elig),
            None,
        )
        if pick:
            used.add(pick["player_id"])
            assigned.append({"slot": slot, **pick})
    return assigned

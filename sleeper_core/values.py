"""
FantasyCalc trade values, matched to the league's exact format.

Third party and semi-official: FantasyCalc documented this API in a guest post,
but there are no formal docs and no stated rate limits. Isolated behind its own
client so a failure here only affects the trade-value tools.

Two things make this join clean. FantasyCalc prices players *per format* — a
superflex dynasty QB is worth far more than a 1QB redraft QB — so league_format
translates Sleeper settings into their query parameters rather than pulling
generic rankings. And every FantasyCalc player object carries a sleeperId, so
the join back to Sleeper is exact with no name matching.
"""

from __future__ import annotations

import json
import time

from .config import CACHE_DIR, FC_CACHE_TTL
from .http import fc_get, get_json


def league_format(lid: str) -> dict:
    """Translate Sleeper league settings into FantasyCalc parameters.

    The four signals, all inferred rather than configured:
      ppr        from scoring_settings.rec
      numQbs     2 if a SUPER_FLEX slot exists, else 1
      numTeams   total_rosters
      isDynasty  settings.type == 2
    """
    league = get_json(f"/league/{lid}", cache=True) or {}
    rec = (league.get("scoring_settings") or {}).get("rec", 0) or 0
    ppr = 1 if rec >= 1 else (0.5 if rec >= 0.5 else 0)
    positions = league.get("roster_positions") or []
    num_qbs = 2 if "SUPER_FLEX" in positions else 1
    num_teams = league.get("total_rosters") or 12
    is_dynasty = (league.get("settings") or {}).get("type") == 2
    return {
        "ppr": ppr,
        "numQbs": num_qbs,
        "numTeams": num_teams,
        "isDynasty": is_dynasty,
    }


def fc_values(fmt: dict) -> list[dict]:
    """FantasyCalc values for a league format, disk-cached for a few hours.

    The cache key encodes the format, so two leagues with different settings
    do not share a cache entry.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = (
        f"fc_dyn{int(fmt['isDynasty'])}_qb{fmt['numQbs']}"
        f"_tm{fmt['numTeams']}_ppr{fmt['ppr']}.json"
    )
    cache_file = CACHE_DIR / key
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < FC_CACHE_TTL:
        try:
            return json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            pass

    params = [
        ("isDynasty", str(fmt["isDynasty"]).lower()),
        ("numQbs", fmt["numQbs"]),
        ("numTeams", fmt["numTeams"]),
        ("ppr", fmt["ppr"]),
    ]
    raw = fc_get("/values/current", params=params) or []
    try:
        cache_file.write_text(json.dumps(raw))
    except OSError:
        pass
    return raw


def fc_row(v: dict) -> dict:
    """Flatten one FantasyCalc value record into a readable row.

    Their optional fields are prefixed "maybe" and can be null — maybeAdp in
    particular is frequently absent outside redraft season.
    """
    p = v.get("player") or {}
    return {
        "name": p.get("name"),
        "position": p.get("position"),
        "team": p.get("maybeTeam"),
        "age": p.get("maybeAge"),
        "value": v.get("value"),
        "redraft_value": v.get("redraftValue"),
        "overall_rank": v.get("overallRank"),
        "position_rank": v.get("positionRank"),
        "tier": v.get("maybeTier"),
        "adp": v.get("maybeAdp"),
        "trend_30_day": v.get("trend30Day"),
        "trade_frequency": v.get("maybeTradeFrequency"),
        "sleeper_id": p.get("sleeperId"),
    }

"""
Team offense context: usage concentration and offensive-coordinator tiers.

The question this answers is "how crowded is this player's offense". A receiver
with a 30% target share on a team that funnels everything to one player is a
different asset from a receiver with 30% on a team that spreads it around, even
if their box scores match.

Two measures:

  usage_consistency_score   per player, rewards a high average target share and
                            punishes week-to-week variance. A boom/bust WR2 and
                            a steady WR2 score very differently.

  team_hhi                  Herfindahl-Hirschman index over target shares, the
                            same concentration measure used in antitrust. Sum
                            of squared shares: high means one or two players
                            dominate, low means targets are spread thin.

OC tiers come from oc_tiers.json, a hand-maintained file. Teams missing from it
fall back to tier 3 with an "unknown" label rather than failing.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from typing import Any

from .config import OC_TIERS_FILE, STATS_CACHE_TTL
from .http import nflverse_csv
from .stats import current_season

SKILL_POSITIONS = {"WR", "RB", "TE", "QB"}


def load_oc_tiers() -> dict[str, dict]:
    """Read the hand-maintained OC tier file. Keys starting with _ are notes."""
    if OC_TIERS_FILE.exists():
        try:
            data = json.loads(OC_TIERS_FILE.read_text())
            return {k: v for k, v in data.items() if not k.startswith("_")}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def stats_season_with_label() -> tuple[str, str]:
    """Pick a season that actually has game data, and say which it is.

    In the offseason the current season exists but has no rows yet, so this
    falls back a year. The label matters: a user asking about 2026 usage in
    August should be told they are looking at 2025.
    """
    current = current_season()
    rows = nflverse_csv("stats_player", f"stats_player_week_{current}.csv",
                        ttl=STATS_CACHE_TTL)
    if rows:
        return current, f"{current} (current season)"
    prev = str(int(current) - 1)
    return prev, f"{prev} (historical — {current} season data not yet available)"


def safe_float(val: Any, default: float = 0.0) -> float:
    """float() that returns a default instead of raising on junk or None."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def team_skill_rows(team: str, season: str) -> list[dict]:
    """Every weekly stat row for skill-position players on one team."""
    rows = nflverse_csv("stats_player", f"stats_player_week_{season}.csv",
                        ttl=STATS_CACHE_TTL)
    return [
        r for r in rows
        if r.get("team", "").upper() == team.upper()
        and r.get("position") in SKILL_POSITIONS
    ]


def crowding_analysis(team: str, season: str) -> dict:
    """Per-player usage consistency plus team-level target concentration.

    Returns {} when the season has no rows, which callers treat as "no data"
    rather than "an offense with no players".
    """
    rows = team_skill_rows(team, season)
    if not rows:
        return {}

    player_weeks: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        name = r.get("player_display_name") or r.get("player_name") or ""
        pos = r.get("position") or ""
        tgt_share = safe_float(r.get("target_share"))
        carries = safe_float(r.get("carries"))
        targets = safe_float(r.get("targets"))
        # A week with neither a target nor a carry is a healthy scratch or a
        # blowout cameo. Including it would drag every average down.
        if targets + carries == 0:
            continue
        player_weeks[name].append({
            "pos": pos,
            "target_share": tgt_share,
            "targets": targets,
            "carries": carries,
            "week": r.get("week"),
        })

    players_out = []
    for name, weeks in player_weeks.items():
        if not weeks:
            continue
        pos = weeks[0]["pos"]
        shares = [w["target_share"] for w in weeks if w["target_share"] > 0]
        avg_share = round(statistics.mean(shares), 3) if shares else 0.0
        share_std = round(statistics.stdev(shares), 3) if len(shares) > 1 else 0.0
        avg_targets = round(statistics.mean(w["targets"] for w in weeks), 1)
        games = len(weeks)
        # Reward volume, punish variance, clamp to 0-100. The 200/300 weights
        # are tuned so a ~25% share with low variance lands around 40-50.
        consistency = max(0.0, min(100.0, round(avg_share * 200 - share_std * 300, 1)))
        players_out.append({
            "name": name,
            "position": pos,
            "games": games,
            "avg_target_share": avg_share,
            "target_share_std": share_std,
            "avg_targets_per_game": avg_targets,
            "usage_consistency_score": consistency,
        })

    players_out.sort(key=lambda p: p["avg_target_share"], reverse=True)
    for i, p in enumerate(players_out, start=1):
        p["usage_rank"] = i

    all_shares = [p["avg_target_share"] for p in players_out if p["avg_target_share"] > 0]
    hhi = round(sum(s ** 2 for s in all_shares), 3) if all_shares else 0.0
    if hhi > 0.35:
        concentration = "high — targets concentrated in 1-2 players"
    elif hhi > 0.20:
        concentration = "moderate — clear hierarchy but multiple contributors"
    else:
        concentration = "distributed — targets spread across many players"

    return {
        "players": players_out,
        "team_hhi": hhi,
        "concentration": concentration,
    }

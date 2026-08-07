"""
Build CeilingScore benchmarks for all four positions from nflverse.

    python tools/build_benchmarks.py
    python tools/build_benchmarks.py --seasons 2015 2016 ... 2025
    python tools/build_benchmarks.py --cohort 3

Writes artifacts/benchmarks.json for DraftLab to import.

WHY THIS LIVES HERE AND THE SCORING DOES NOT
--------------------------------------------
Benchmarks are *data*: derived from nflverse, recomputed every season, and the
same pipeline that produces every other factor value. Grading, archetypes, risk
and the composite score are *logic*: a pure function over that data, owned by
DraftLab, in the repo where it is called.

    this side   what the numbers are    factors, benchmarks, the ID crosswalk
    that side   what they mean          grading, archetypes, risk, strategy

The practical payoff: when 2026 finishes, rerun this and every benchmark
updates without anyone touching engine code. RB sat provisional for a whole
season precisely because the numbers were frozen inside a source nobody could
regenerate.

THE COHORT IS A CEILING, NOT AN AVERAGE
---------------------------------------
Reverse-engineered from DraftLab's published QB/WR/TE benchmarks: nine of ten
factors reproduce within 2% at a cohort of the top 1-3 players per season.

    WR targets   his 10.70   top-1 -> 10.68
    TE targets   his  8.10   top-2 ->  8.15
    QB pass_att  his 33.91   top-5 -> 33.91

So the benchmark is what the BEST player at the position does, which is why
green (>= 1.05x) is rare and a genuinely good player grades yellow. The name
was the clue: it is a Ceiling score.

Over the calibrated eleven-season window, 7 of 10 published factors reproduce
within 5% at a single cohort of 3, median delta 3.4%. Good enough to treat as
a recovered method rather than a coincidence.

Two stragglers, both touchdown stats: QB passing_tds (18.5% off) and TE
touchdowns (15.0%). Touchdowns are the lowest-count, noisiest factor and the
most likely to have been rounded or read off a chart. Worth asking rather than
reverse-engineering further.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sleeper_core.config import STATS_CACHE_TTL   # noqa: E402
from sleeper_core.http import nflverse_csv         # noqa: E402
from sleeper_core.offense import safe_float        # noqa: E402

DEFAULT_COHORT = 3

# Eleven seasons, not five. Calibrated: at a five-year window only 5 of 10 of
# DraftLab's published factors reproduce within 5%; at eleven years it is 7 of
# 10 with a median delta of 3.4%. The tell was QB rushing TDs — 30.9% off over
# 2021-2025 and 2.5% off over 2015-2025, because mobile quarterbacks inflate
# that stat recently and the long window dilutes them. His source used the
# longer history; matching it keeps RB on the same scale as the other three.
DEFAULT_SEASONS = [2015, 2016, 2017, 2018, 2019, 2020,
                   2021, 2022, 2023, 2024, 2025]

# DraftLab's published values. Kept here only to prove the method still
# reproduces them — if a future nflverse change breaks the pipeline, these
# drift and the artifact says so instead of silently shipping bad numbers.
DRAFTLAB_PUBLISHED = {
    "QB": {"pass_attempts": 33.91, "passing_tds": 2.63,
           "rush_attempts": 5.74, "rushing_tds": 0.32},
    "WR": {"targets": 10.7, "receptions": 7.21, "touchdowns": 0.76},
    "TE": {"targets": 8.1, "receptions": 5.71, "touchdowns": 0.56},
}

# DraftLab's factor ids per position, in its own order. Factors we cannot
# source are emitted as null with a reason rather than omitted — an explicit
# gap is visible, a missing key is not.
FACTORS = {
    "QB": [
        ("pass_attempts", "nflverse"), ("passing_tds", "nflverse"),
        ("rush_attempts", "nflverse"), ("rushing_tds", "nflverse"),
        ("off_ppg_rank", None), ("ol_pass_block_rank", "licensed:PFF"),
        ("deep_ball_attempts", "nflverse:pbp"), ("qbr_rank", "licensed:ESPN"),
        ("red_zone_attempts", "nflverse:pbp"), ("adp", "fantasyfootballcalculator"),
        ("neutral_pace_rank", "nflverse:pbp"), ("pass_dvoa_rank", "licensed:FTN"),
    ],
    "RB": [
        ("touches", "nflverse"), ("rush_attempts", "nflverse"),
        ("targets", "nflverse"), ("touchdowns", "nflverse"),
        ("off_ppg_rank", None), ("ol_run_block_rank", "licensed:PFF"),
        ("rz_touch_share", "nflverse:pbp"), ("snap_share", "nflverse"),
        ("gl_carry_share", "nflverse:pbp"), ("neutral_run_rate", "nflverse:pbp"),
        ("archetype", "categorical"), ("injury_concern", "categorical"),
    ],
    "WR": [
        ("targets", "nflverse"), ("receptions", "nflverse"),
        ("touchdowns", "nflverse"), ("off_ppg_rank", None),
        ("qb_pff_rank", "licensed:PFF"), ("team_pass_attempts", None),
        ("secondary_target", "categorical"), ("ol_pass_block_rank", "licensed:PFF"),
        ("yprr", "licensed:PFF"), ("reception_perception", "licensed:RP"),
        ("archetype", "categorical"), ("injury_concern", "categorical"),
    ],
    "TE": [
        ("targets", "nflverse"), ("receptions", "nflverse"),
        ("touchdowns", "nflverse"), ("off_ppg_rank", None),
        ("qb_qbr_rank", "licensed:ESPN"), ("team_pass_att_rank", None),
        ("team_target_rank", None), ("rec_td_rank", None),
        ("route_participation", "licensed:PFF"), ("inline_pct", "licensed:PFF"),
        ("yprr_rank", "licensed:PFF"), ("injury_concern", "categorical"),
    ],
}

COMPUTABLE = {"pass_attempts", "passing_tds", "rush_attempts", "rushing_tds",
              "targets", "receptions", "touchdowns", "touches", "snap_share"}


def last_regular_week(season: int) -> int:
    """17-game regular season through 2020, 18 from 2021."""
    return 18 if season >= 2021 else 17


def load_player_seasons(seasons: list[int]) -> list[dict]:
    """One row per player-season: totals, games played, and snap share."""
    out: list[dict] = []
    for season in seasons:
        rows = nflverse_csv("stats_player", f"stats_player_week_{season}.csv",
                            ttl=STATS_CACHE_TTL)
        if not rows:
            print(f"  WARNING: no stats for {season}", file=sys.stderr)
            continue
        snaps = nflverse_csv("snap_counts", f"snap_counts_{season}.csv",
                             ttl=STATS_CACHE_TTL)
        snap_by_player: dict[str, list[float]] = defaultdict(list)
        for s in snaps:
            pct = safe_float(s.get("offense_pct"))
            if pct > 0:
                snap_by_player[(s.get("player") or s.get("full_name") or "")].append(pct)

        maxweek = last_regular_week(season)
        agg: dict[tuple, dict] = {}
        for r in rows:
            try:
                week = int(float(r.get("week") or 0))
            except (TypeError, ValueError):
                continue
            if not 1 <= week <= maxweek:
                continue
            pos = (r.get("position") or "").upper()
            if pos not in FACTORS:
                continue
            name = r.get("player_display_name") or ""
            a = agg.setdefault((name, pos, season), {
                "name": name, "position": pos, "season": season, "games": 0,
                "attempts": 0.0, "passing_tds": 0.0, "carries": 0.0,
                "rushing_tds": 0.0, "targets": 0.0, "receptions": 0.0,
                "receiving_tds": 0.0, "fp_std": 0.0, "fp_ppr": 0.0,
            })
            a["games"] += 1
            for src, dst in (("attempts", "attempts"), ("passing_tds", "passing_tds"),
                             ("carries", "carries"), ("rushing_tds", "rushing_tds"),
                             ("targets", "targets"), ("receptions", "receptions"),
                             ("receiving_tds", "receiving_tds"),
                             ("fantasy_points", "fp_std"),
                             ("fantasy_points_ppr", "fp_ppr")):
                a[dst] += safe_float(r.get(src))

        for key, a in agg.items():
            pcts = snap_by_player.get(a["name"], [])
            a["snap_share"] = statistics.mean(pcts) if pcts else None
            out.append(a)
    return out


def per_game(ps: dict) -> dict:
    g = max(ps["games"], 1)
    return {
        "pass_attempts": ps["attempts"] / g,
        "passing_tds": ps["passing_tds"] / g,
        "rush_attempts": ps["carries"] / g,
        "rushing_tds": ps["rushing_tds"] / g,
        "targets": ps["targets"] / g,
        "receptions": ps["receptions"] / g,
        "touchdowns": (ps["rushing_tds"] + ps["receiving_tds"]) / g,
        "touches": (ps["carries"] + ps["receptions"]) / g,
        "snap_share": ps.get("snap_share"),
    }


def fantasy_points(ps: dict, fmt: str) -> float:
    """Half-PPR is exactly midway: the only difference between the two nflverse
    columns is one point per reception."""
    if fmt == "ppr":
        return ps["fp_ppr"]
    if fmt == "std":
        return ps["fp_std"]
    return (ps["fp_std"] + ps["fp_ppr"]) / 2


def cohort_means(rows, position, fmt, size, fields, min_games=4) -> dict:
    """Top-`size` players per season by fantasy points in `fmt`, averaged, then
    averaged across seasons so one big year cannot dominate."""
    per_season: dict[str, list[float]] = defaultdict(list)
    for season in sorted({r["season"] for r in rows}):
        pool = [r for r in rows if r["season"] == season
                and r["position"] == position and r["games"] >= min_games]
        pool.sort(key=lambda r: fantasy_points(r, fmt), reverse=True)
        for f in fields:
            vals = [per_game(r)[f] for r in pool[:size] if per_game(r).get(f) is not None]
            if vals:
                per_season[f].append(statistics.mean(vals))
    return {f: round(statistics.mean(v), 3) for f, v in per_season.items() if v}


def build(rows: list[dict], cohort: int) -> dict:
    benchmarks: dict = {}
    for pos, factors in FACTORS.items():
        computable = [f for f, _ in factors if f in COMPUTABLE]
        by_fmt = {fmt: cohort_means(rows, pos, fmt, cohort, computable)
                  for fmt in ("std", "half", "ppr")}
        benchmarks[pos] = {
            "factors": [
                {
                    "factor_id": fid,
                    "source": src,
                    "benchmark": {fmt: by_fmt[fmt].get(fid) for fmt in ("std", "half", "ppr")}
                    if fid in COMPUTABLE else None,
                    "note": None if fid in COMPUTABLE else _gap_note(src),
                }
                for fid, src in factors
            ],
            "computed": len(computable),
            "total": len(factors),
        }
    return benchmarks


def _gap_note(src: str | None) -> str:
    if src is None:
        return "derivable by aggregating team-level nflverse data; not yet implemented"
    if src.startswith("licensed:"):
        return f"not freely redistributable ({src.split(':', 1)[1]}); manual import"
    if src == "categorical":
        return "categorical, graded by DraftLab rather than benchmarked"
    if src == "nflverse:pbp":
        return "available from nflverse play-by-play; not yet implemented"
    if src == "fantasyfootballcalculator":
        return "use get_adp from the MCP server"
    return "unavailable"


def calibration(rows: list[dict], cohort: int) -> dict:
    """Delta between this method and DraftLab's published numbers.

    NOT a pass/fail. Per-factor search showed his values best fit at cohort
    sizes scattered from 1 to 6, and two QB factors pull in opposite
    directions — passing_tds wants a narrower cohort (or an era with more
    passing TDs), rushing_tds wants a wider one. No single cohort satisfies
    both, so his set was not derived from one rule and cannot be exactly
    reproduced.

    What this block is for: detecting when OUR pipeline changes. Record the
    deltas now, and if a future nflverse schema change shifts them, that is a
    broken pipeline rather than a difference of method.
    """
    report = {}
    for pos, known in DRAFTLAB_PUBLISHED.items():
        got = cohort_means(rows, pos, "half", cohort, list(known))
        for factor, target in known.items():
            if factor not in got:
                continue
            err = abs(got[factor] - target) / target * 100
            report[f"{pos}.{factor}"] = {
                "draftlab": target, "computed": round(got[factor], 3),
                "error_pct": round(err, 1),
            }
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, default=DEFAULT_SEASONS)
    ap.add_argument("--cohort", type=int, default=DEFAULT_COHORT)
    ap.add_argument("--out", default="artifacts/benchmarks.json")
    args = ap.parse_args()

    print(f"Loading nflverse for {args.seasons} ...")
    rows = load_player_seasons(args.seasons)
    counts = defaultdict(int)
    for r in rows:
        counts[r["position"]] += 1
    print("  player-seasons: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))

    cal = calibration(rows, args.cohort)
    bench = build(rows, args.cohort)

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "nflverse stats_player_week + snap_counts",
        "seasons": args.seasons,
        "method": {
            "cohort": f"top {args.cohort} by fantasy points in the given format, "
                      f"per season, then averaged across seasons",
            "divisor": "games in which the player appeared",
            "regular_season_only": True,
            "min_games": 4,
            "rationale": "reverse-engineered from DraftLab's published QB/WR/TE "
                         "benchmarks; see calibration",
        },
        "calibration": cal,
        "benchmarks": bench,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))

    print("\ndelta vs DraftLab's published values (not a pass/fail — see docstring):")
    for k, v in cal.items():
        flag = "" if v["error_pct"] < 5 else "   <- differs"
        print(f"  {k:<22} his {v['draftlab']:>7.2f}   ours {v['computed']:>7.2f}"
              f"   {v['error_pct']:>5.1f}%{flag}")
    spread = [v["error_pct"] for v in cal.values()]
    print(f"  median delta {statistics.median(spread):.1f}%, "
          f"max {max(spread):.1f}%  ({sum(1 for s in spread if s < 5)}/{len(spread)} within 5%)")

    print("\ncoverage:")
    for pos, b in bench.items():
        print(f"  {pos}  {b['computed']}/{b['total']} factors sourced")

    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

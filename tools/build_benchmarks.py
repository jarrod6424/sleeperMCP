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

Over the calibrated eleven-season window, 11 of 17 published factors agree
within 10% at a single cohort of 3, median 4.3%.

NEITHER SET IS GROUND TRUTH
---------------------------
DraftLab's numbers were transcribed by hand from screenshots of a video, of an
analysis whose own method is undocumented. So a gap is not an error on either
side — it is two independent inferences disagreeing, with no authority to
appeal to. Do not tune this script to close one.

That makes the agreement the result worth reporting: chart-reading and eleven
seasons of nflverse converging to a median 4.3% across 17 factors is real
mutual corroboration.

PERCENT ERROR IS THE WRONG METRIC FOR RANK FACTORS
--------------------------------------------------
Run with --spread. Each benchmark is a mean of 11 seasonal values, and the
dispersion of those values decides whether a gap against DraftLab means
anything. Relative standard error by factor type:

    rank factors      10-16%   off_ppg_rank, rec_td_rank, team_pass_att_rank
    volume factors     2-3%    targets, receptions, pass_attempts

So a 15% gap on a rank is unreadable, while a 15% gap on a volume stat is
enormous. Judging by percent alone inverts the answer, and did: three of the
six factors flagged as divergent are within 2 SE and were never disagreements.

    TE.rec_td_rank         27.4%   z=1.9   noise (worst % in the table)
    TE.team_pass_att_rank  14.2%   z=1.2   noise
    WR.off_ppg_rank        12.6%   z=1.0   noise
    TE.touchdowns          15.0%   z=2.0   borderline
    QB.off_ppg_rank        33.7%   z=3.8   real
    QB.passing_tds         18.5%   z=6.9   real, and large

14 of 17 of his values sit within 2 SE of ours. That is the corroboration
result, and it is stronger than any single close match — note that
TE.off_ppg_rank agreeing to 0.02 is luck, not evidence, since its SE is 1.83
on a 1-32 scale.

HIS QB SET IS NOT FROM A SINGLE COHORT
--------------------------------------
The two real divergences are both QB, and they demand opposite fixes.

    passing_tds   2.63 vs 2.14   needs a NARROWER cohort. SD is 0.23, so 2.63
                                 is ~7 SE out and cannot be a top-3 draw. It is
                                 about where top-1 lands (Mahomes 2018 3.13/g,
                                 Rodgers 2020 3.00/g).
    off_ppg_rank  6.35 vs 4.21   needs a WIDER cohort. Top-3 fantasy QBs are
                                 nearly by definition on elite offenses, so the
                                 value collapses to 4.21; widening regresses
                                 toward the league mean of 16.

No single cohort satisfies both, so his QB numbers were not produced by one
rule. Ours are. That is the argument for owning benchmarks here — not that his
are wrong, but that one rule across four positions can be re-derived every
February and audited by anyone.

Deliberately not tuned per factor. Fitting each factor to its own cohort would
match him better and mean less.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Same opt-in as server.py: corporate/local networks that TLS-inspect (e.g.
# antivirus HTTPS scanning) present a private root certifi does not trust.
# Set USE_OS_TRUSTSTORE=1 locally; unnecessary and skipped on a clean host.
if os.environ.get("USE_OS_TRUSTSTORE"):
    import truststore

    truststore.inject_into_ssl()

from sleeper_core.adp import name_keys               # noqa: E402
from sleeper_core.config import CACHE_DIR, STATS_CACHE_TTL   # noqa: E402
from sleeper_core.http import nflverse_csv         # noqa: E402
from sleeper_core.offense import safe_float        # noqa: E402
from sleeper_core.stats import to_nflverse_team     # noqa: E402

DEFAULT_COHORT = 3

# Eleven seasons, not five. Calibrated: at a five-year window only 5 of 10 of
# DraftLab's published factors reproduce within 5%; at eleven years it is 7 of
# 10 with a median delta of 3.4%. The tell was QB rushing TDs — 30.9% off over
# 2021-2025 and 2.5% off over 2015-2025, because mobile quarterbacks inflate
# that stat recently and the long window dilutes them. His source used the
# longer history; matching it keeps RB on the same scale as the other three.
DEFAULT_SEASONS = [2015, 2016, 2017, 2018, 2019, 2020,
                   2021, 2022, 2023, 2024, 2025]

# DraftLab's published values — a REFERENCE, not ground truth.
#
# These were transcribed by hand from screenshots of a video, of an analysis
# whose own method is unknown. So a difference between his number and ours is
# not an error on either side; it is two independent inferences disagreeing.
#
# They are kept for two reasons:
#   1. Corroboration. Two unrelated methods — his chart-reading, our eleven
#      seasons of nflverse — agreeing to a median 4.3% across 17 factors is
#      real evidence both are roughly right.
#   2. Regression detection. These deltas are recorded in every artifact. If a
#      future nflverse schema change shifts them, that is OUR pipeline breaking,
#      which is exactly what the numbers are useful for.
#
# What they are NOT is a target to optimise against. Where the correct
# definition and the closest match to his number diverge, take correctness.
DRAFTLAB_PUBLISHED = {
    "QB": {"pass_attempts": 33.91, "passing_tds": 2.63,
           "rush_attempts": 5.74, "rushing_tds": 0.32,
           "off_ppg_rank": 6.35},
    "WR": {"targets": 10.7, "receptions": 7.21, "touchdowns": 0.76,
           "off_ppg_rank": 8.94, "team_pass_attempts": 594.94},
    "TE": {"targets": 8.1, "receptions": 5.71, "touchdowns": 0.56,
           "off_ppg_rank": 11.78, "team_pass_att_rank": 11.81,
           "team_target_rank": 1.43, "rec_td_rank": 1.38},
    # Added once DraftLab's RB benchmarks left provisional:0 — sourced from a
    # different video than the QB/WR/TE set above (an FSE league-winner
    # analysis), transcribed by hand the same way. Same caveat applies: a
    # reference, not ground truth. Three of DraftLab's RB factors —
    "RB": {"touches": 21.5, "rush_attempts": 17.3, "targets": 5.4,
           "receptions": 4.25, "touchdowns": 0.98, "off_ppg_rank": 9.5,
           "yards_per_carry": 4.89, "yards_per_touch": 5.82, "team_wins": 9.85},
}

# Not every factor is a per-game rate. Getting this wrong silently divides a
# season total by 17 and produces a plausible-looking wrong number.
#   per_game     rate stats — divide by games played
#   season_total team_pass_attempts, benchmark 594.94, clearly a full season
#   rank         already a rank; average it as-is
FACTOR_KIND = {
    "team_pass_attempts": "season_total",
    "off_ppg_rank": "rank",
    "team_pass_att_rank": "rank",
    "team_target_rank": "rank",
    "rec_td_rank": "rank",
    "snap_share": "rate",          # already a percentage
    "neutral_pace_rank": "rank",
    "qbr_rank": "rank",
    "qb_qbr_rank": "rank",
    "qb_pff_rank": "rank",
    "route_participation": "rate",  # already a percentage
    "secondary_target": "season_total",
    "team_wins": "season_total",
    "rz_touch_share": "rate",      # already a percentage, see load_rb_pbp_season
    "gl_carry_share": "rate",
    "neutral_run_rate": "rate",
    "ol_pass_block_rank": "rank",
    "ol_run_block_rank": "rank",
    "pass_epa_rank": "rank",
    "target_share": "rate",
    "yprr": "rate",
    "reception_perception": "rate",
}

# DraftLab's factor ids per position, in its own order. Factors we cannot
# source are emitted as null with a reason rather than omitted — an explicit
# gap is visible, a missing key is not.
FACTORS = {
    "QB": [
        ("pass_attempts", "nflverse"), ("passing_tds", "nflverse"),
        ("rush_attempts", "nflverse"), ("rushing_tds", "nflverse"),
        ("off_ppg_rank", "nflverse"), ("ol_pass_block_rank", "nflverse:pbp:proxy"),
        ("deep_ball_attempts", "nflverse:pbp"), ("qbr_rank", "nflverse:espn_qbr"),
        ("red_zone_attempts", "nflverse:pbp"),
        ("neutral_pace_rank", "nflverse:pbp"), ("pass_epa_rank", "nflverse:pbp:proxy"),
        ("injury_concern", "nflverse:injuries"),
    ],
    "RB": [
        ("touches", "nflverse"), ("rush_attempts", "nflverse"),
        ("targets", "nflverse"), ("receptions", "nflverse"),
        ("touchdowns", "nflverse"),
        ("off_ppg_rank", "nflverse"), ("ol_run_block_rank", "nflverse:pbp:proxy"),
        ("yards_per_carry", "nflverse"), ("yards_per_touch", "nflverse"),
        ("team_wins", "nflverse:schedules"),
        ("rz_touch_share", "nflverse:pbp"), ("snap_share", "nflverse"),
        ("gl_carry_share", "nflverse:pbp"), ("neutral_run_rate", "nflverse:pbp"),
        ("archetype", "categorical"), ("injury_concern", "nflverse:injuries"),
    ],
    "WR": [
        ("targets", "nflverse"), ("receptions", "nflverse"),
        ("yards_per_catch", "nflverse"), ("yac_per_reception", "nflverse"),
        ("target_share", "nflverse"),
        ("touchdowns", "nflverse"), ("off_ppg_rank", "nflverse"),
        ("qb_pff_rank", "nflverse:espn_qbr"), ("team_pass_attempts", "nflverse"),
        ("route_participation", "nflverse:participation"),
        ("secondary_target", "nflverse"), ("ol_pass_block_rank", "nflverse:pbp:proxy"),
        ("neutral_pace_rank", "nflverse:pbp"),
        ("yprr", "nflverse:participation"), ("reception_perception", "nflverse:ngs"),
        ("archetype", "categorical"), ("injury_concern", "nflverse:injuries"),
    ],
    "TE": [
        ("targets", "nflverse"), ("receptions", "nflverse"),
        ("touchdowns", "nflverse"), ("off_ppg_rank", "nflverse"),
        ("qb_qbr_rank", "nflverse:espn_qbr"), ("team_pass_att_rank", "nflverse"),
        ("team_target_rank", "nflverse"), ("rec_td_rank", "nflverse"),
        ("route_participation", "nflverse:participation"),
        ("ol_pass_block_rank", "nflverse:pbp:proxy"),
        ("neutral_pace_rank", "nflverse:pbp"),
        ("inline_pct", "licensed:PFF"),
        ("yprr_rank", "licensed:PFF"), ("injury_concern", "nflverse:injuries"),
    ],
}

COMPUTABLE = {"pass_attempts", "passing_tds", "rush_attempts", "rushing_tds",
              "targets", "receptions", "touchdowns", "touches", "snap_share",
              # team context, aggregated from the same weekly stats file
              "off_ppg_rank", "team_pass_attempts", "team_pass_att_rank",
              "team_target_rank", "rec_td_rank",
              # QB-only, from play-by-play (see load_qb_pbp_season)
              "deep_ball_attempts", "red_zone_attempts", "neutral_pace_rank",
              # QB/TE/WR, from ESPN QBR via nflverse (see load_espn_qbr_season)
              "qbr_rank", "qb_qbr_rank", "qb_pff_rank",
              # TE/WR, from nflverse participation (see load_route_participation)
              "route_participation",
              # WR-only, from same-team target competition (see _attach_wr_secondary_targets)
              "secondary_target",
              # WR-only volume efficiency (see _efficiency_yards / load_player_seasons)
              "yards_per_catch", "yac_per_reception", "target_share",
              # WR-only, from participation on_pass counts (see load_route_details)
              "yprr",
              # WR-only, from Next Gen Stats receiving (see load_ngs_catch_pct)
              "reception_perception",
              # RB-only efficiency + team context (see per_game / load_team_wins_season)
              "yards_per_carry", "yards_per_touch", "team_wins",
              # RB-only, from play-by-play (see load_rb_pbp_season)
              "rz_touch_share", "gl_carry_share", "neutral_run_rate",
              # OL proxies from play-by-play (see load_ol_proxy_season)
              "ol_pass_block_rank", "ol_run_block_rank",
              # QB-only, team pass EPA rank proxy (see load_pass_epa_ranks)
              "pass_epa_rank"}


_POINTS_CACHE: dict[int, dict] = {}
# Records whether off_ppg_rank came from real scores or the fallback proxy.
# Shipped in the artifact: a circular proxy must not look like real data.
_OFF_PPG_SOURCE = {"used": None}


def team_points_per_game(season: int) -> dict:
    """Points scored per game by each team, from nflverse's games file.

    A ~1 MB CSV of game results, not the multi-hundred-MB play-by-play. Cached
    on disk like everything else. Returns {} on any failure so the caller can
    fall back rather than crash.
    """
    if season in _POINTS_CACHE:
        return _POINTS_CACHE[season]

    cache = CACHE_DIR / "nfldata_games.csv"
    raw = None
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 7 * 24 * 3600:
        raw = cache.read_text(encoding="utf-8", errors="replace")
    else:
        try:
            import httpx
            r = httpx.get("https://github.com/nflverse/nfldata/raw/master/data/games.csv",
                          timeout=60.0, follow_redirects=True)
            r.raise_for_status()
            raw = r.text
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(raw, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            # Distinguish "the network won't let me out" from "the file moved".
            # These look identical in a stack trace and lead to opposite fixes:
            # one wasted a round chasing a master/main branch rename when the
            # URL had been correct all along and the sandbox simply blocked
            # raw.githubusercontent.com.
            blocked = any(s in f"{type(exc).__name__}: {exc}".lower()
                          for s in ("proxy", "403", "forbidden", "ssl",
                                    "connect", "timeout", "resolve"))
            print(f"  WARNING: could not fetch games.csv ({type(exc).__name__}: {exc})",
                  file=sys.stderr)
            if blocked:
                print("           This looks like blocked egress, not a bad URL. Run this\n"
                      "           script on a machine that can already reach nflverse —\n"
                      "           if get_player_stats works there, so will this.",
                      file=sys.stderr)
            print("           off_ppg_rank falls back to the fantasy-points proxy,\n"
                  "           which is circular. Do not ship those values.",
                  file=sys.stderr)
            _POINTS_CACHE[season] = {}
            return {}

    scored: dict[str, list[float]] = defaultdict(list)
    for row in csv.DictReader(io.StringIO(raw)):
        try:
            if int(row.get("season") or 0) != season:
                continue
        except (TypeError, ValueError):
            continue
        if (row.get("game_type") or "REG") != "REG":
            continue
        for side, opp in (("home", "away"), ("away", "home")):
            team = to_nflverse_team(row.get(f"{side}_team"))
            pts = row.get(f"{side}_score")
            if team and pts not in (None, ""):
                scored[team].append(safe_float(pts))

    out = {t: sum(v) / len(v) for t, v in scored.items() if v}
    _POINTS_CACHE[season] = out
    return out


def last_regular_week(season: int) -> int:
    """17-game regular season through 2020, 18 from 2021."""
    return 18 if season >= 2021 else 17


QB_DEEP_BALL_AIR_YARDS = 20.0  # standard NFL-analytics "deep ball" threshold
QB_RED_ZONE_YARDLINE = 20.0    # yardline_100 <= 20 = inside the 20

_QB_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _qb_name_key(name: str) -> str:
    """First-initial + last-name key, e.g. 'Josh Allen' / 'J.Allen' -> 'jallen'.

    Bridges stats_player_week's full display names against play-by-play's
    abbreviated passer/rusher names. This project has never fetched
    play-by-play before, so the exact live format of passer_player_name is
    unverified from here (sandboxed, no network) -- if the real join rate
    comes back low when this actually runs, print diagnostics and check the
    real column value rather than trusting this blind.
    """
    parts = [p for p in (name or "").replace(".", " ").split()
             if p.strip(".").lower() not in _QB_NAME_SUFFIXES]
    if not parts:
        return ""
    return (parts[0][0] + "".join(parts[1:])).lower()


def _attach_team_qbr_ranks(agg: dict, qbr: dict) -> None:
    """Attach QB personal qbr_rank and team-primary ranks to WR/TE rows."""
    for a in agg.values():
        if a["position"] != "QB":
            continue
        hit = None
        qb_name_key = _qb_name_key(a["name"])
        for k in name_keys(a["name"]):
            if k in qbr:
                hit = qbr[k]
                break
            if qb_name_key in qbr:
                hit = qbr[qb_name_key]
                break
        if hit:
            a["qbr_rank"] = hit["rank"]

    primary_by_team: dict[str, int] = {}
    candidates: dict[str, list] = defaultdict(list)
    for entry in qbr.values():
        if entry.get("team"):
            candidates[entry["team"]].append(entry)
    for team, ents in candidates.items():
        ents.sort(key=lambda e: e["qb_plays"], reverse=True)
        primary_by_team[team] = ents[0]["rank"]

    for a in agg.values():
        team = a.get("team")
        if not team or team not in primary_by_team:
            continue
        if a["position"] == "TE":
            a["qb_qbr_rank"] = primary_by_team[team]
        elif a["position"] == "WR":
            a["qb_pff_rank"] = primary_by_team[team]


def classify_secondary_target(player_targets: float, secondary_targets: float) -> str:
    r = secondary_targets / player_targets
    if r < 0.75:
        return "less"
    if r < 1.00:
        return "same"
    return "more"


def _attach_wr_secondary_targets(season_rows: list[dict]) -> None:
    """Same-team WR target competition: max teammate season targets + band."""
    by_team: dict[str, list[dict]] = defaultdict(list)
    for r in season_rows:
        if r.get("position") == "WR" and r.get("team"):
            by_team[r["team"]].append(r)

    for wrs in by_team.values():
        if len(wrs) < 2:
            continue
        for wr in wrs:
            player_targets = wr.get("targets") or 0
            if player_targets <= 0:
                continue
            others = [w for w in wrs if w is not wr]
            secondary_targets = max(w["targets"] for w in others)
            wr["secondary_target"] = secondary_targets
            wr["secondary_target_cat"] = classify_secondary_target(
                player_targets, secondary_targets
            )


def _neutral_script(row: dict) -> bool:
    """Within two scores and outside the two-minute drill of either half.

    Standard neutral-pace definition (e.g. Football Outsiders): garbage time
    and hurry-up both distort plays-per-game in ways that have nothing to do
    with how fast an offense actually wants to play.
    """
    diff, qtr_raw, secs = (row.get("score_differential"), row.get("qtr"),
                           row.get("game_seconds_remaining"))
    if diff in (None, "") or qtr_raw in (None, "") or secs in (None, ""):
        return False
    if abs(safe_float(diff)) > 8:
        return False
    try:
        qtr = int(float(qtr_raw))
    except (TypeError, ValueError):
        return False
    return not (qtr in (2, 4) and safe_float(secs) < 120)


def load_qb_pbp_season(season: int) -> tuple[dict, dict]:
    """QB deep-ball rate, red-zone involvement, and team neutral-script pace,
    from one season's play-by-play.

    Returns (qb_stats, team_neutral_plays):
      qb_stats[_qb_name_key(name)] = {"weeks": {wk, ...}, "deep": n, "rz": n}
        -- summed across every team the player threw/ran for that season,
        same team-agnostic-season-total convention as every other factor in
        this file (only TEAM-level aggregates need the team-locking guard
        _attach_team_context has; an individual's own counting stats never
        did, see build_benchmarks's team-attribution fix).
      team_neutral_plays[team][week] = neutral-script offensive play count.

    Filtered during parsing: nflverse's play-by-play file is multi-hundred-MB
    per season -- team_points_per_game's own docstring calls that out as the
    file this ISN'T. Building every column into a dict per play would repeat
    the OOM risk nflverse_csv's row_filter exists to avoid (see its
    depth_charts example).

    Best-effort: returns ({}, {}) on any failure (network blocked, file not
    yet published for this season) so the caller leaves these three factors
    unset rather than fabricate zeros.
    """
    def keep(row):
        st = row.get("season_type")
        if st and st != "REG":
            return False
        return (row.get("play_type") or "") in ("pass", "run")

    rows = nflverse_csv("pbp", f"play_by_play_{season}.csv", row_filter=keep,
                        ttl=STATS_CACHE_TTL)
    if not rows:
        return {}, {}

    all_passers = {_qb_name_key(r.get("passer_player_name"))
                   for r in rows
                   if r.get("play_type") == "pass" and r.get("passer_player_name")}
    all_passers.discard("")

    qb_stats: dict[str, dict] = defaultdict(lambda: {"weeks": set(), "deep": 0, "rz": 0})
    team_neutral_plays: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for r in rows:
        team = to_nflverse_team(r.get("posteam"))
        week = str(r.get("week") or "")
        play_type = r.get("play_type")

        if team and week and _neutral_script(r):
            team_neutral_plays[team][week] += 1

        if not week:
            continue

        if play_type == "pass":
            key = _qb_name_key(r.get("passer_player_name"))
            if not key:
                continue
            s = qb_stats[key]
            s["weeks"].add(week)
            air = r.get("air_yards")
            if air not in (None, "") and safe_float(air) >= QB_DEEP_BALL_AIR_YARDS:
                s["deep"] += 1
            yl = r.get("yardline_100")
            if yl not in (None, "") and safe_float(yl) <= QB_RED_ZONE_YARDLINE:
                s["rz"] += 1
        elif play_type == "run":
            key = _qb_name_key(r.get("rusher_player_name"))
            if not key or key not in all_passers:
                continue  # not a QB scramble/sneak -- a real rusher's carry
            s = qb_stats[key]
            s["weeks"].add(week)
            yl = r.get("yardline_100")
            if yl not in (None, "") and safe_float(yl) <= QB_RED_ZONE_YARDLINE:
                s["rz"] += 1

    return dict(qb_stats), {t: dict(w) for t, w in team_neutral_plays.items()}


def neutral_pace_ranks(team_neutral_plays: dict) -> dict[str, int]:
    """Team rank by neutral-script plays/game, 1 = fastest (most plays)."""
    avg = {}
    for team, weeks in team_neutral_plays.items():
        if weeks:
            avg[team] = sum(weeks.values()) / len(weeks)
    order = sorted(avg, key=lambda t: avg[t], reverse=True)
    return {t: i + 1 for i, t in enumerate(order)}


def _truthy(v) -> bool:
    if v in (None, ""):
        return False
    try:
        return float(v) != 0.0
    except (TypeError, ValueError):
        return bool(v)


def pressure_rates_from_rows(rows: list[dict]) -> dict[str, float]:
    """Team pressure_rate = pressured / dropbacks from pbp-like dict rows."""
    dropbacks: dict[str, int] = defaultdict(int)
    pressured: dict[str, int] = defaultdict(int)
    for r in rows:
        team = to_nflverse_team(r.get("posteam"))
        if not team:
            continue
        is_dropback = _truthy(r.get("pass_attempt")) or _truthy(r.get("sack"))
        if not is_dropback:
            continue
        dropbacks[team] += 1
        if _truthy(r.get("sack")) or _truthy(r.get("qb_hit")) or _truthy(r.get("qb_scramble")):
            pressured[team] += 1
    return {t: pressured[t] / dropbacks[t] for t in dropbacks if dropbacks[t]}


def stuff_rates_from_rows(rows: list[dict]) -> dict[str, float]:
    """Team stuff_rate = (rush yards <= 0) / rush attempts."""
    rushes: dict[str, int] = defaultdict(int)
    stuffed: dict[str, int] = defaultdict(int)
    for r in rows:
        team = to_nflverse_team(r.get("posteam"))
        if not team or not _truthy(r.get("rush_attempt")):
            continue
        rushes[team] += 1
        yd = r.get("rushing_yards")
        if yd in (None, ""):
            continue
        if safe_float(yd) <= 0:
            stuffed[team] += 1
    return {t: stuffed[t] / rushes[t] for t in rushes if rushes[t]}


def rank_teams_ascending(rate_by_team: dict[str, float]) -> dict[str, int]:
    """Lowest rate = rank 1. Ties broken by team abbreviation ascending."""
    order = sorted(rate_by_team.keys(), key=lambda t: (rate_by_team[t], t))
    return {t: i + 1 for i, t in enumerate(order)}


def pass_epa_mean_from_rows(rows: list[dict]) -> dict[str, float]:
    sums: dict[str, float] = defaultdict(float)
    n: dict[str, int] = defaultdict(int)
    for r in rows:
        if (r.get("season_type") or "REG") not in ("REG",):
            continue
        if not _truthy(r.get("pass")) and (r.get("play_type") or "") != "pass":
            continue
        team = to_nflverse_team(r.get("posteam"))
        epa = r.get("epa")
        if not team or epa in (None, ""):
            continue
        sums[team] += safe_float(epa)
        n[team] += 1
    return {t: sums[t] / n[t] for t in n if n[t] > 0}


def rank_teams_descending(value_by_team: dict[str, float]) -> dict[str, int]:
    order = sorted(value_by_team.keys(), key=lambda t: (-value_by_team[t], t))
    return {t: i + 1 for i, t in enumerate(order)}


def load_pass_epa_ranks(season: int) -> dict[str, int]:
    def keep(row):
        st = row.get("season_type")
        if st and st != "REG":
            return False
        return _truthy(row.get("pass")) or (row.get("play_type") or "") == "pass"

    rows = nflverse_csv("pbp", f"play_by_play_{season}.csv", row_filter=keep,
                        ttl=STATS_CACHE_TTL)
    if not rows:
        return {}
    return rank_teams_descending(pass_epa_mean_from_rows(rows))


def load_ol_proxy_season(season: int) -> tuple[dict[str, int], dict[str, int]]:
    """Team OL pass (pressure) and run (stuff) block ranks from one season pbp.

    Returns (ol_pass_block_rank, ol_run_block_rank) keyed by nflverse team.
    Best-effort: ({}, {}) on empty fetch — never fabricate ranks.
    """
    def keep(row):
        st = row.get("season_type")
        if st and st != "REG":
            return False
        return (row.get("play_type") or "") in ("pass", "run")

    rows = nflverse_csv("pbp", f"play_by_play_{season}.csv", row_filter=keep,
                        ttl=STATS_CACHE_TTL)
    if not rows:
        return {}, {}
    pass_ranks = rank_teams_ascending(pressure_rates_from_rows(rows))
    run_ranks = rank_teams_ascending(stuff_rates_from_rows(rows))
    return pass_ranks, run_ranks


def load_rb_pbp_season(season: int) -> tuple[dict, dict, dict, dict]:
    """RB red-zone touch share, goal-line carry share, and team neutral-script
    run rate, from one season's play-by-play. TDD-001.

    Returns (rb_stats, team_rz_totals, team_gl_totals, team_neutral_runs):
      rb_stats[_qb_name_key(name)] = {"weeks": {wk, ...}, "rz_touches": n,
        "gl_carries": n} -- this player's own red-zone touches (rush attempts
        + targets, yardline_100 <= 20) and goal-line carries (rush attempts
        with goal_to_go == 1), summed across the season. Built from every
        rusher and targeted receiver in the file, not RB-filtered at parse
        time -- rushing and targets aren't position-locked the way passing is
        for load_qb_pbp_season's QB-only qb_stats, so this dict is looked up
        selectively later, only for rows load_player_seasons has already
        classified as RB.
      team_rz_totals[team] = team's total red-zone touches, EVERY position.
        An RB-only denominator would be trivially ~100% for any team with one
        healthy back and would not discriminate between players -- the
        fantasy-relevant question is whether the offense features this back
        near the goal line at all, not whether he has a backup.
      team_gl_totals[team] = team's total goal-line carries, every position
        INCLUDING QB -- a goal-line sneak is real competition for that touch,
        not a different category of play (resolved decision, TDD-001; a team
        with a rushing QB will structurally show lower gl_carry_share for its
        RB1 than an otherwise-identical team without one -- documented
        behaviour, not a bug).
      team_neutral_runs[team] = {"runs": n, "plays": n} -- neutral-script run
        plays and total offensive plays. neutral_run_rate = runs / plays,
        team-level, attached to every RB on that team the same way
        off_ppg_rank is.

    goal_to_go is a confirmed real column in nflfastR's released pbp schema
    (data-raw/pbp_datatypes.csv, numeric) -- verified against the source
    rather than assumed, unlike this file's other pbp thresholds. Used
    directly; no yardline cutoff invented for "goal line".

    Best-effort: returns ({}, {}, {}, {}) on any failure (network blocked,
    file not yet published for this season), same convention as
    load_qb_pbp_season -- leaves these three factors unset for the caller
    rather than fabricate zeros.
    """
    def keep(row):
        st = row.get("season_type")
        if st and st != "REG":
            return False
        return (row.get("play_type") or "") in ("pass", "run")

    rows = nflverse_csv("pbp", f"play_by_play_{season}.csv", row_filter=keep,
                        ttl=STATS_CACHE_TTL)
    if not rows:
        return {}, {}, {}, {}

    rb_stats: dict[str, dict] = defaultdict(
        lambda: {"weeks": set(), "rz_touches": 0, "gl_carries": 0})
    team_rz_totals: dict[str, int] = defaultdict(int)
    team_gl_totals: dict[str, int] = defaultdict(int)
    team_neutral_runs: dict[str, dict[str, int]] = defaultdict(
        lambda: {"runs": 0, "plays": 0})

    for r in rows:
        team = to_nflverse_team(r.get("posteam"))
        week = str(r.get("week") or "")
        play_type = r.get("play_type")
        yl = r.get("yardline_100")
        in_red_zone = yl not in (None, "") and safe_float(yl) <= QB_RED_ZONE_YARDLINE
        goal_to_go = safe_float(r.get("goal_to_go")) == 1

        if team and _neutral_script(r):
            team_neutral_runs[team]["plays"] += 1
            if play_type == "run":
                team_neutral_runs[team]["runs"] += 1

        if not week:
            continue

        if play_type == "run":
            if in_red_zone and team:
                team_rz_totals[team] += 1
            if goal_to_go and team:
                team_gl_totals[team] += 1
            key = _qb_name_key(r.get("rusher_player_name"))
            if key:
                s = rb_stats[key]
                s["weeks"].add(week)
                if in_red_zone:
                    s["rz_touches"] += 1
                if goal_to_go:
                    s["gl_carries"] += 1
        elif play_type == "pass":
            receiver = r.get("receiver_player_name")
            if in_red_zone and team and receiver:
                team_rz_totals[team] += 1
            key = _qb_name_key(receiver)
            if key and in_red_zone:
                s = rb_stats[key]
                s["weeks"].add(week)
                s["rz_touches"] += 1

    return (dict(rb_stats), dict(team_rz_totals), dict(team_gl_totals),
            {t: dict(v) for t, v in team_neutral_runs.items()})


def _route_rates_from_events(events: list[dict]) -> dict[str, float]:
    """Convert pre-aggregated pass-play participation to percentages."""
    out = {}
    for event in events:
        denom = event["team_pass_plays"]
        if denom <= 0:
            continue
        out[event["player_key"]] = round(100.0 * event["on_pass"] / denom, 3)
    return out


def compute_yprr(receiving_yards: float, on_pass: int) -> float | None:
    if not on_pass or on_pass <= 0:
        return None
    return float(receiving_yards) / float(on_pass)


def load_ngs_catch_pct(season: int) -> dict[str, float]:
    """Season catch % from nflverse Next Gen Stats receiving (week 0).

    Returns {} on failure. Values are 0–100. Attribution: NFL Next Gen Stats
    via nflverse.
    """
    try:
        rows = nflverse_csv(
            "nextgen_stats", "ngs_receiving.csv", ttl=STATS_CACHE_TTL,
        )
        if not rows:
            return {}
        out: dict[str, float] = {}
        for r in rows:
            try:
                if int(float(r.get("season") or 0)) != season:
                    continue
            except (TypeError, ValueError):
                continue
            if (r.get("season_type") or "").upper() not in ("REG", "REGULAR", ""):
                if r.get("season_type") not in (None, ""):
                    continue
            try:
                week = int(float(r.get("week")))
            except (TypeError, ValueError):
                continue
            if week != 0:
                continue
            name = r.get("player_display_name") or ""
            if not name:
                continue
            raw = safe_float(r.get("catch_percentage"))
            if raw <= 0:
                continue
            pct = raw * 100.0 if raw <= 1.0 else raw
            out[_qb_name_key(name)] = pct
        return out
    except Exception:  # noqa: BLE001
        return {}


def load_team_wins_season(season: int) -> dict[str, int]:
    """Regular-season win totals by team from nflverse schedules/games.csv.

    Best-effort: {} on failure. Ties and unfinished scores are skipped.
    """
    rows = nflverse_csv(
        "schedules", "games.csv", ttl=STATS_CACHE_TTL, prefer_gzip=False,
    )
    if not rows:
        return {}
    wins: dict[str, int] = defaultdict(int)
    for r in rows:
        try:
            if int(float(r.get("season") or 0)) != season:
                continue
        except (TypeError, ValueError):
            continue
        if (r.get("game_type") or "REG") != "REG":
            continue
        home = (r.get("home_team") or "").upper()
        away = (r.get("away_team") or "").upper()
        if not home or not away:
            continue
        try:
            hs = float(r.get("home_score") or "")
            as_ = float(r.get("away_score") or "")
        except (TypeError, ValueError):
            continue
        if hs > as_:
            wins[home] += 1
        elif as_ > hs:
            wins[away] += 1
    return dict(wins)


def load_route_details(season: int, position: str) -> dict[str, dict[str, float | int]]:
    """Route participation rate and on_pass counts from pbp_participation + pbp.

    nflverse's participation ``route`` is the primary receiver's route type,
    not a per-player route flag. This instead measures 100 * (regular-season
    team pass plays where the player's GSIS id appears in ``offense_players``) /
    (team pass plays in games where that player participated), plus raw
    ``on_pass`` counts for YPRR proxy. ``position`` must be ``TE`` or ``WR``.
    Best-effort: {} on failure. Attribution: FTN via nflverse for 2023+ (CC-BY-SA).
    """
    position = (position or "").upper()
    if position not in {"TE", "WR"}:
        return {}
    try:
        participation = nflverse_csv(
            "pbp_participation", f"pbp_participation_{season}.csv",
            ttl=STATS_CACHE_TTL,
        )
        if not participation:
            return {}
        pbp = nflverse_csv(
            "pbp", f"play_by_play_{season}.csv",
            row_filter=lambda row: (
                row.get("season_type") == "REG" and row.get("play_type") == "pass"
            ),
            ttl=STATS_CACHE_TTL,
        )
        stats_rows = nflverse_csv(
            "stats_player", f"stats_player_week_{season}.csv",
            ttl=STATS_CACHE_TTL,
        )
        if not pbp or not stats_rows:
            return {}

        required_participation = {"nflverse_game_id", "play_id", "offense_players"}
        required_pbp = {"game_id", "play_id", "posteam"}
        required_stats = {"player_id", "player_display_name", "position", "team"}
        if (not required_participation <= set(participation[0])
                or not required_pbp <= set(pbp[0])
                or not required_stats <= set(stats_rows[0])):
            return {}

        player_by_gsis = {
            row["player_id"]: {
                "player_key": _qb_name_key(row["player_display_name"]),
                "team": to_nflverse_team(row["team"]),
            }
            for row in stats_rows
            if (row.get("position") or "").upper() == position
            and row.get("player_id") and row.get("player_display_name")
            and to_nflverse_team(row.get("team"))
        }
        gsis_ids_by_key: dict[str, set[str]] = defaultdict(set)
        for gsis_id, player in player_by_gsis.items():
            gsis_ids_by_key[player["player_key"]].add(gsis_id)
        colliding_keys = {
            key for key, gsis_ids in gsis_ids_by_key.items() if len(gsis_ids) > 1
        }
        player_by_gsis = {
            gsis_id: player
            for gsis_id, player in player_by_gsis.items()
            if player["player_key"] not in colliding_keys
        }
        if not player_by_gsis:
            return {}

        participation_by_play = {
            (row["nflverse_game_id"], str(row["play_id"])): row["offense_players"]
            for row in participation
            if row.get("nflverse_game_id") and row.get("play_id") not in (None, "")
            and row.get("offense_players")
        }
        active_games_by_gsis: dict[str, set[str]] = defaultdict(set)
        for row in participation:
            game_id = row.get("nflverse_game_id")
            if not game_id:
                continue
            for gsis_id in (row.get("offense_players") or "").replace(";", " ").split():
                if gsis_id in player_by_gsis:
                    active_games_by_gsis[gsis_id].add(game_id)

        team_pass_plays: dict[tuple[str, str], int] = defaultdict(int)
        on_pass: dict[str, int] = defaultdict(int)
        for row in pbp:
            team = to_nflverse_team(row["posteam"])
            game_id = row["game_id"]
            if not team or not game_id:
                continue
            team_pass_plays[(game_id, team)] += 1
            offense_players = participation_by_play.get((game_id, str(row["play_id"])))
            if offense_players:
                for gsis_id in offense_players.replace(";", " ").split():
                    if gsis_id in player_by_gsis:
                        on_pass[gsis_id] += 1

        player_totals: dict[str, dict[str, int]] = defaultdict(
            lambda: {"team_pass_plays": 0, "on_pass": 0})
        for gsis_id, player in player_by_gsis.items():
            key = player["player_key"]
            team = player["team"]
            player_totals[key]["team_pass_plays"] += sum(
                team_pass_plays[(game_id, team)]
                for game_id in active_games_by_gsis[gsis_id]
            )
            player_totals[key]["on_pass"] += on_pass[gsis_id]

        rates = _route_rates_from_events([
            {"player_key": key, **totals} for key, totals in player_totals.items()
        ])
        return {
            key: {"rate": rates[key], "on_pass": totals["on_pass"]}
            for key, totals in player_totals.items()
            if key in rates
        }
    except Exception:  # noqa: BLE001
        return {}


def load_route_participation(season: int, position: str) -> dict[str, float]:
    """Route participation % only; see load_route_details for on_pass counts."""
    details = load_route_details(season, position)
    return {k: float(v["rate"]) for k, v in details.items() if "rate" in v}


def load_te_route_participation(season: int) -> dict[str, float]:
    return load_route_participation(season, "TE")


def load_espn_qbr_season(season: int) -> dict:
    """Season Total QBR ranks from nflverse espn_data release.

    File is multi-season (`qbr_season_level.csv`); filter to `season`.
    Rank qualified QBs by qbr_total descending (1 = best). Best-effort: {} on failure.
    """
    rows = nflverse_csv("espn_data", "qbr_season_level.csv", ttl=STATS_CACHE_TTL)
    if not rows:
        return {}
    season_rows = []
    for r in rows:
        try:
            if int(float(r.get("season") or 0)) != season:
                continue
        except (TypeError, ValueError):
            continue
        # Accept qualified True/true/1; if column absent, keep row with qb_plays >= 1
        qual = str(r.get("qualified") or "True").lower()
        if qual in ("false", "0", "no"):
            continue
        name = r.get("name_display") or r.get("player_name") or ""
        qbr = safe_float(r.get("qbr_total") or r.get("qbr"))
        if not name or qbr <= 0:
            continue
        season_rows.append({
            "key": _qb_name_key(name),
            "name": name,
            "qbr": qbr,
            "qb_plays": int(safe_float(r.get("qb_plays"))),
            "team": to_nflverse_team(r.get("team_abb") or r.get("team")),
        })
    season_rows.sort(key=lambda x: x["qbr"], reverse=True)
    out = {}
    for i, row in enumerate(season_rows, start=1):
        out[row["key"]] = {
            "qbr": row["qbr"],
            "qb_plays": row["qb_plays"],
            "team": row["team"],
            "rank": i,
        }
    return out


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
        team_weeks: dict[str, set] = defaultdict(set)
        # Team-level pass-attempt/points totals, keyed off each ROW's own team —
        # never off a player's collapsed season entry. A player traded mid-season
        # (Joe Flacco: CLE weeks 1-4, CIN weeks 6-18) gets exactly one "team" on
        # his agg row (whichever appeared first), so summing team totals from agg
        # silently drops his attempts from every team but that first one. That
        # starved CIN's 2025 team_pass_attempts by 256 (640 real vs 384 measured)
        # and dragged down off_ppg_rank/team_pass_attempts for every teammate
        # (e.g. Ja'Marr Chase) who never changed teams at all — a plausible wrong
        # number, not an error, exactly the failure mode this file warns about.
        team_totals: dict[str, dict] = defaultdict(lambda: {"pass_attempts": 0.0, "fp_total": 0.0})
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
            team = (r.get("team") or "").upper()
            if team:
                team_weeks[team].add(week)
                team_totals[team]["pass_attempts"] += safe_float(r.get("attempts"))
                team_totals[team]["fp_total"] += (
                    safe_float(r.get("fantasy_points")) + safe_float(r.get("fantasy_points_ppr"))
                ) / 2
            a = agg.setdefault((name, pos, season), {
                "name": name, "position": pos, "season": season, "games": 0,
                "team": team,
                "attempts": 0.0, "passing_tds": 0.0, "carries": 0.0,
                "rushing_tds": 0.0, "rushing_yards": 0.0,
                "targets": 0.0, "receptions": 0.0, "receiving_yards": 0.0,
                "yac_sum": 0.0, "yac_n": 0,
                "target_share_sum": 0.0, "target_share_n": 0,
                "receiving_tds": 0.0, "fp_std": 0.0, "fp_ppr": 0.0,
            })
            a["games"] += 1
            for src, dst in (("attempts", "attempts"), ("passing_tds", "passing_tds"),
                             ("carries", "carries"), ("rushing_tds", "rushing_tds"),
                             ("rushing_yards", "rushing_yards"),
                             ("targets", "targets"), ("receptions", "receptions"),
                             ("receiving_yards", "receiving_yards"),
                             ("receiving_tds", "receiving_tds"),
                             ("fantasy_points", "fp_std"),
                             ("fantasy_points_ppr", "fp_ppr")):
                a[dst] += safe_float(r.get(src))
            if r.get("receiving_yards_after_catch") not in (None, ""):
                a["yac_sum"] += safe_float(r.get("receiving_yards_after_catch"))
                a["yac_n"] += 1
            if r.get("target_share") not in (None, ""):
                a["target_share_sum"] += safe_float(r.get("target_share"))
                a["target_share_n"] += 1
            a["fp_half"] = (a["fp_std"] + a["fp_ppr"]) / 2

        for key, a in agg.items():
            if a.get("yac_n"):
                a["receiving_yards_after_catch"] = a["yac_sum"]
            if a.get("target_share_n"):
                a["target_share"] = a["target_share_sum"] / a["target_share_n"]
            pcts = snap_by_player.get(a["name"], [])
            a["snap_share"] = statistics.mean(pcts) if pcts else None

        _attach_team_context(list(agg.values()), team_weeks, team_totals)

        team_wins = load_team_wins_season(season)
        if team_wins:
            matched = 0
            for a in agg.values():
                team = a.get("team")
                if team and team in team_wins:
                    a["team_wins"] = team_wins[team]
                    matched += 1
            print(f"  schedules {season}: matched {matched}/{len(agg)} players "
                  f"to team_wins", file=sys.stderr)
        else:
            print(f"  WARNING: no schedules for {season}; team_wins left unset",
                  file=sys.stderr)

        # QB-only play-by-play enrichment. Best-effort: an empty fetch (network
        # blocked, or this season's file not yet published) must not silently
        # zero out deep_ball_attempts/red_zone_attempts/neutral_pace_rank for
        # every QB -- it leaves them unset, same as any other unsourced factor.
        qb_stats, team_neutral = load_qb_pbp_season(season)
        if qb_stats or team_neutral:
            pace_rank = neutral_pace_ranks(team_neutral) if team_neutral else {}
            qb_rows = [a for a in agg.values() if a["position"] == "QB"]
            matched = 0
            for a in qb_rows:
                stats = qb_stats.get(_qb_name_key(a["name"])) if qb_stats else None
                if stats and stats["weeks"]:
                    a["deep_ball_count"] = stats["deep"]
                    a["rz_count"] = stats["rz"]
                    a["pbp_games"] = len(stats["weeks"])
                    matched += 1
                if a["team"] in pace_rank:
                    a["neutral_pace_rank"] = pace_rank[a["team"]]
            for a in agg.values():
                if a["position"] in ("WR", "TE") and a["team"] in pace_rank:
                    a["neutral_pace_rank"] = pace_rank[a["team"]]
            print(f"  play-by-play {season}: matched {matched}/{len(qb_rows)} QBs "
                  f"to deep_ball_attempts/red_zone_attempts; "
                  f"pace ranks for QB/WR/TE", file=sys.stderr)
        else:
            print(f"  WARNING: no play-by-play for {season}; deep_ball_attempts/"
                  f"red_zone_attempts/neutral_pace_rank left unset", file=sys.stderr)

        # OL proxies (separate call OK; same pbp file is disk-cached by nflverse_csv)
        ol_pass, ol_run = load_ol_proxy_season(season)
        if ol_pass or ol_run:
            for a in agg.values():
                team = a.get("team")
                if not team:
                    continue
                if a["position"] in ("QB", "WR", "TE") and team in ol_pass:
                    a["ol_pass_block_rank"] = ol_pass[team]
                if a["position"] == "RB" and team in ol_run:
                    a["ol_run_block_rank"] = ol_run[team]
            print(f"  ol proxy {season}: pass ranks={len(ol_pass)} run ranks={len(ol_run)}",
                  file=sys.stderr)
        else:
            print(f"  WARNING: no ol proxy for {season}; ol_*_block_rank left unset",
                  file=sys.stderr)

        epa_ranks = load_pass_epa_ranks(season)
        if epa_ranks:
            for a in agg.values():
                if a["position"] == "QB" and a.get("team") in epa_ranks:
                    a["pass_epa_rank"] = epa_ranks[a["team"]]

        # RB-only play-by-play enrichment, TDD-001. Same best-effort contract
        # as the QB block above: an empty fetch must leave these three unset
        # for every RB, not silently zero them out.
        rb_stats, team_rz, team_gl, team_neutral = load_rb_pbp_season(season)
        if rb_stats:
            rb_rows = [a for a in agg.values() if a["position"] == "RB"]
            matched = 0
            for a in rb_rows:
                stats = rb_stats.get(_qb_name_key(a["name"]))
                team = a["team"]
                if stats and stats["weeks"] and team in team_rz and team_rz[team]:
                    a["rz_touch_share"] = stats["rz_touches"] / team_rz[team]
                    matched += 1
                if stats and stats["weeks"] and team in team_gl and team_gl[team]:
                    a["gl_carry_share"] = stats["gl_carries"] / team_gl[team]
                if team in team_neutral and team_neutral[team]["plays"]:
                    a["neutral_run_rate"] = (
                        team_neutral[team]["runs"] / team_neutral[team]["plays"]
                    )
            print(f"  play-by-play {season}: matched {matched}/{len(rb_rows)} RBs "
                  f"to rz_touch_share/gl_carry_share", file=sys.stderr)
        else:
            print(f"  WARNING: no play-by-play for {season}; rz_touch_share/"
                  f"gl_carry_share/neutral_run_rate left unset", file=sys.stderr)

        for pos in ("TE", "WR"):
            details = load_route_details(season, pos)
            if details:
                pos_rows = [a for a in agg.values() if a["position"] == pos]
                matched = 0
                yprr_matched = 0
                for a in pos_rows:
                    d = details.get(_qb_name_key(a["name"]))
                    if not d:
                        continue
                    if "rate" in d:
                        a["route_participation"] = d["rate"]
                        matched += 1
                    if pos == "WR":
                        yprr = compute_yprr(
                            a.get("receiving_yards") or 0.0, int(d.get("on_pass") or 0),
                        )
                        if yprr is not None:
                            a["yprr"] = yprr
                            yprr_matched += 1
                msg = (f"  participation {season}: matched {matched}/{len(pos_rows)} "
                       f"{pos}s to route_participation")
                if pos == "WR":
                    msg += f", {yprr_matched} to yprr"
                print(msg, file=sys.stderr)
            else:
                print(f"  WARNING: no participation for {season} {pos}; "
                      f"route_participation left unset", file=sys.stderr)

        qbr = load_espn_qbr_season(season)
        if qbr:
            _attach_team_qbr_ranks(agg, qbr)
        else:
            print(f"  WARNING: no ESPN QBR for {season}; qbr_rank/qb_qbr_rank/"
                  f"qb_pff_rank left unset", file=sys.stderr)

        _attach_wr_secondary_targets(list(agg.values()))

        catch_pct = load_ngs_catch_pct(season)
        if catch_pct:
            wr_rows = [a for a in agg.values() if a["position"] == "WR"]
            matched = 0
            for a in wr_rows:
                pct = catch_pct.get(_qb_name_key(a["name"]))
                if pct is not None:
                    a["reception_perception"] = pct
                    matched += 1
            print(f"  ngs {season}: matched {matched}/{len(wr_rows)} WRs to "
                  f"reception_perception", file=sys.stderr)
        else:
            print(f"  WARNING: no NGS receiving for {season}; "
                  f"reception_perception left unset", file=sys.stderr)

        out.extend(agg.values())
    return out


def _attach_team_context(season_rows: list[dict], team_weeks: dict, team_totals: dict) -> None:
    """Team-level ranks and totals, computed from the same weekly rows.

    Four of DraftLab's factors are about the offence a player sits in rather
    than the player. All of them fall out of the stats file we already have —
    no play-by-play needed.

      off_ppg_rank        team rank by offensive fantasy points per game
      team_pass_attempts  team season pass attempts
      team_pass_att_rank  team rank by that
      team_target_rank    the player's rank WITHIN his team by targets
      rec_td_rank         the player's rank within his team by receiving TDs

    The last two are why an elite TE benchmarks at 1.43 — the tight ends that
    matter are their team's first or second option, not their tenth.
    """
    by_team: dict[str, list[dict]] = defaultdict(list)
    for r in season_rows:
        if r.get("team"):
            by_team[r["team"]].append(r)

    totals = {}
    for team in team_weeks:
        games = max(len(team_weeks.get(team, ())), 1)
        t = team_totals.get(team, {"fp_total": 0.0, "pass_attempts": 0.0})
        totals[team] = {
            "fp_total": t["fp_total"],
            "games": games,
            "pass_attempts": t["pass_attempts"],
        }

    att_order = sorted(totals, key=lambda t: totals[t]["pass_attempts"], reverse=True)
    att_rank = {t: i + 1 for i, t in enumerate(att_order)}

    # off_ppg_rank uses ACTUAL POINTS SCORED, not summed fantasy points.
    #
    # Two failed attempts got us here. Ranking teams by their skill players'
    # fantasy points is circular — the player being evaluated is inside the sum
    # — and the error tracked how much each position contributes to its own
    # total (QB 30% off, WR 18%, TE 4.6%). Subtracting just that player from
    # just his own team was worse still (QB 284% off): it compares one team
    # missing its quarterback against 31 teams that still have theirs.
    #
    # The measure was never fantasy points. "Offensive PPG" is points on the
    # scoreboard, one ranking for all positions — which is why his three
    # benchmarks (QB 6.35, WR 8.94, TE 11.78) differ: same ranking, different
    # cohorts. Elite quarterbacks play for better offences than elite tight
    # ends do.
    ppg = team_points_per_game(season_rows[0]["season"]) if season_rows else {}
    if ppg:
        _OFF_PPG_SOURCE["used"] = "points_scored"
        order = sorted(ppg, key=lambda t: ppg[t], reverse=True)
        pts_rank = {t: i + 1 for i, t in enumerate(order)}
    else:
        _OFF_PPG_SOURCE["used"] = "fantasy_points_proxy"
        # Fall back to the fantasy-points proxy, clearly flagged, rather than
        # emitting nothing. Circular, but better than a silent gap.
        fp_order = sorted(totals, key=lambda t: totals[t]["fp_total"] / totals[t]["games"],
                          reverse=True)
        pts_rank = {t: i + 1 for i, t in enumerate(fp_order)}

    for team, players in by_team.items():
        for p in players:
            p["off_ppg_rank"] = pts_rank.get(team)

    for team, players in by_team.items():
        # Within-team ranks count PASS CATCHERS only. Ranking against the whole
        # roster let a receiving back push an elite tight end from 1st to 2nd,
        # which is why our TE ranks came in worse than DraftLab's.
        catchers = [p for p in players if p["position"] in ("WR", "TE")]
        tgt_order = sorted(catchers, key=lambda p: p["targets"], reverse=True)
        td_order = sorted(catchers, key=lambda p: p["receiving_tds"], reverse=True)
        tgt_rank = {id(p): i + 1 for i, p in enumerate(tgt_order)}
        td_rank = {id(p): i + 1 for i, p in enumerate(td_order)}
        for p in players:
            p["team_pass_attempts"] = totals[team]["pass_attempts"]
            p["team_pass_att_rank"] = att_rank[team]
            p["team_target_rank"] = tgt_rank.get(id(p))
            p["rec_td_rank"] = td_rank.get(id(p))


def _efficiency_yards(ps: dict) -> dict[str, float]:
    """Season-efficiency rates from aggregated yardage and volume."""
    carries = ps.get("carries") or 0
    rec = ps.get("receptions") or 0
    rush_yd = ps.get("rushing_yards") or 0
    rec_yd = ps.get("receiving_yards") or 0
    out: dict[str, float] = {}
    if carries > 0:
        out["yards_per_carry"] = rush_yd / carries
    touches = carries + rec
    if touches > 0:
        out["yards_per_touch"] = (rush_yd + rec_yd) / touches
    if rec > 0:
        out["yards_per_catch"] = rec_yd / rec
        if "receiving_yards_after_catch" in ps:
            out["yac_per_reception"] = ps["receiving_yards_after_catch"] / rec
    return out


def per_game(ps: dict) -> dict:
    """Factor values for one player-season, scaled per FACTOR_KIND."""
    g = max(ps["games"], 1)
    passthrough = {k: ps.get(k) for k in FACTOR_KIND if k in ps}
    out = {**passthrough, **{
        "pass_attempts": ps["attempts"] / g,
        "passing_tds": ps["passing_tds"] / g,
        "rush_attempts": ps["carries"] / g,
        "rushing_tds": ps["rushing_tds"] / g,
        "targets": ps["targets"] / g,
        "receptions": ps["receptions"] / g,
        "touchdowns": (ps["rushing_tds"] + ps["receiving_tds"]) / g,
        "touches": (ps["carries"] + ps["receptions"]) / g,
    }}
    out.update(_efficiency_yards(ps))
    # QB play-by-play factors: only present when load_qb_pbp_season actually
    # matched this player, scaled by the games pbp saw him play (not the
    # weekly-stats game count -- the two sources can disagree by a game or two
    # on a bye/injury week boundary).
    if "deep_ball_count" in ps:
        pbp_g = max(ps.get("pbp_games", g), 1)
        out["deep_ball_attempts"] = ps["deep_ball_count"] / pbp_g
    if "rz_count" in ps:
        pbp_g = max(ps.get("pbp_games", g), 1)
        out["red_zone_attempts"] = ps["rz_count"] / pbp_g
    if "secondary_target_cat" in ps:
        out["secondary_target_cat"] = ps["secondary_target_cat"]
    return out


def fantasy_points(ps: dict, fmt: str) -> float:
    """Half-PPR is exactly midway: the only difference between the two nflverse
    columns is one point per reception."""
    if fmt == "ppr":
        return ps["fp_ppr"]
    if fmt == "std":
        return ps["fp_std"]
    return (ps["fp_std"] + ps["fp_ppr"]) / 2


def cohort_series(rows, position, fmt, size, fields, min_games=4) -> dict:
    """One value per season: the top-`size` cohort's mean for each field.

    Kept separate from the collapse to a single number so the spread across
    seasons stays inspectable. A benchmark is a mean of ~11 of these, and a
    mean without its dispersion cannot say whether a gap against another
    estimate is real or noise.
    """
    per_season: dict[str, list[float]] = defaultdict(list)
    for season in sorted({r["season"] for r in rows}):
        pool = [r for r in rows if r["season"] == season
                and r["position"] == position and r["games"] >= min_games]
        pool.sort(key=lambda r: fantasy_points(r, fmt), reverse=True)
        for f in fields:
            vals = [per_game(r)[f] for r in pool[:size] if per_game(r).get(f) is not None]
            if vals:
                per_season[f].append(statistics.mean(vals))
    return dict(per_season)


def cohort_means(rows, position, fmt, size, fields, min_games=4) -> dict:
    """Top-`size` players per season by fantasy points in `fmt`, averaged, then
    averaged across seasons so one big year cannot dominate."""
    series = cohort_series(rows, position, fmt, size, fields, min_games)
    return {f: round(statistics.mean(v), 3) for f, v in series.items() if v}


def build(rows: list[dict], cohort: int) -> dict:
    benchmarks: dict = {}
    for pos, factors in FACTORS.items():
        computable = [f for f, _ in factors if f in COMPUTABLE]
        by_fmt = {fmt: cohort_means(rows, pos, fmt, cohort, computable)
                  for fmt in ("std", "half", "ppr")}

        # Ship the uncertainty next to the value. A benchmark is a mean of ~11
        # seasonal draws, and their scatter varies enormously by factor type:
        # volume stats land near 2-3% relative SE, rank stats near 10-16%.
        # Grading both against one flat 1.05x threshold treats a soft number as
        # if it were sharp, so a consumer needs to see which is which.
        series = cohort_series(rows, pos, "half", cohort, computable)
        disp = {}
        for fid, vals in series.items():
            if len(vals) < 3:
                continue
            mean = statistics.mean(vals)
            sd = statistics.stdev(vals)
            se = sd / (len(vals) ** 0.5)
            disp[fid] = {
                "seasons": len(vals),
                "sd": round(sd, 3),
                "se": round(se, 3),
                "relative_se": round(se / mean, 3) if mean else None,
            }

        benchmarks[pos] = {
            "factors": [
                {
                    "factor_id": fid,
                    "source": src,
                    "benchmark": {fmt: by_fmt[fmt].get(fid) for fmt in ("std", "half", "ppr")}
                    if fid in COMPUTABLE else None,
                    "dispersion": disp.get(fid) if fid in COMPUTABLE else None,
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
        # Retained for any future pbp-tagged factor not yet in COMPUTABLE.
        # RB's three (rz_touch_share / gl_carry_share / neutral_run_rate) and
        # QB's three are COMPUTABLE today, so they ship note: null instead.
        return "available from nflverse play-by-play; not yet implemented"
    if src == "nflverse:injuries":
        return "categorical; sourced via nflverse injuries, not cohort-benchmarked"
    if src == "fantasyfootballcalculator":
        return "use get_adp from the MCP server"
    return "unavailable"


def calibration(rows: list[dict], cohort: int) -> dict:
    """Compare our computed values against DraftLab's published reference.

    Neither side is ground truth. His numbers came from screenshots of a video;
    ours from eleven seasons of nflverse. Agreement is corroboration, not a
    grade, and the recorded deltas exist so that a future change in OUR
    pipeline is visible as movement against a fixed reference.
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



def spread_report(rows, cohort: int) -> None:
    """Is a gap against DraftLab real, or is it inside our own year-to-year noise?

    Every benchmark is a mean of ~11 seasonal values. Reporting a gap against
    that mean while ignoring how much those values scatter is how a noisy stat
    gets mistaken for a disagreement about method.

    Prints the standard error of each benchmark and how many SEs away his value
    sits. Under 2 SE, the two numbers are consistent with being estimates of the
    same quantity and there is nothing to explain.
    """
    print("\nspread across seasons — is the gap real, or is it noise?")
    print(f"  {'factor':24} {'ours':>8} {'SD':>7} {'SE':>7} {'his':>8} {'z':>6}")
    verdicts = []
    for pos, published in DRAFTLAB_PUBLISHED.items():
        fields = list(published)
        series = cohort_series(rows, pos, "half", cohort, fields)
        for f in fields:
            vals = series.get(f) or []
            if len(vals) < 3:
                continue
            mean = statistics.mean(vals)
            sd = statistics.stdev(vals)
            se = sd / (len(vals) ** 0.5)
            his = published[f]
            z = abs(his - mean) / se if se else float("inf")
            verdicts.append((f"{pos}.{f}", z))
            mark = "" if z < 2 else ("  <- outside noise" if z < 4 else "  <- well outside")
            print(f"  {pos + '.' + f:24} {mean:8.2f} {sd:7.2f} {se:7.2f} "
                  f"{his:8.2f} {z:6.1f}{mark}")

    inside = [n for n, z in verdicts if z < 2]
    print(f"\n  {len(inside)}/{len(verdicts)} of his values fall within 2 SE of ours —")
    print("  i.e. consistent with both being estimates of the same quantity.")
    if len(inside) < len(verdicts):
        print("  The rest differ by more than sampling noise explains, which points")
        print("  at a genuine difference of definition or cohort, not a bad read.")


def _artifact_warnings() -> list[str]:
    """Anything a consumer should know before trusting these numbers."""
    out = []
    if _OFF_PPG_SOURCE["used"] == "fantasy_points_proxy":
        out.append(
            "off_ppg_rank was computed from summed skill-player fantasy points, "
            "NOT actual points scored — the game-results file could not be "
            "fetched. This is circular: the player being evaluated contributes "
            "to the total his team is ranked by. The error scales with how much "
            "a position drives its own team total (QB ~29% off, WR ~16%, TE "
            "~2.5%). Treat QB and WR off_ppg_rank as unreliable until the real "
            "scores are available; run tools/probe_games_url.py to diagnose."
        )
    return out


def build_benchmarks_artifact(
    seasons: list[int] | None = None,
    cohort: int = DEFAULT_COHORT,
) -> dict:
    """Build the benchmarks.json payload (no disk I/O). Used by CLI and data_api."""
    seasons = list(seasons or DEFAULT_SEASONS)
    print(f"Loading nflverse for {seasons} ...")
    rows = load_player_seasons(seasons)
    counts = defaultdict(int)
    for r in rows:
        counts[r["position"]] += 1
    print("  player-seasons: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))

    cal = calibration(rows, cohort)
    bench = build(rows, cohort)

    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "nflverse stats_player_week + snap_counts",
        "seasons": seasons,
        # Provenance, stated positively. An empty `warnings` list is only
        # implicit evidence that the real scores were used; a reader should
        # not have to infer where a number came from from something absent.
        "off_ppg_rank_source": _OFF_PPG_SOURCE["used"],
        "method": {
            "cohort": f"top {cohort} by fantasy points in the given format, "
                      f"per season, then averaged across seasons",
            "divisor": "games in which the player appeared",
            "regular_season_only": True,
            "min_games": 4,
            "rationale": "reverse-engineered from DraftLab's published QB/WR/TE "
                         "benchmarks; see calibration",
        },
        "reference_comparison": cal,
        "warnings": _artifact_warnings(),
        "benchmarks": bench,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, default=DEFAULT_SEASONS)
    ap.add_argument("--cohort", type=int, default=DEFAULT_COHORT)
    ap.add_argument("--out", default="artifacts/benchmarks.json")
    ap.add_argument("--spread", action="store_true",
                    help="report season-to-season dispersion and whether each gap "
                         "against DraftLab exceeds sampling noise")
    args = ap.parse_args()

    if args.spread:
        print(f"Loading nflverse for {args.seasons} ...")
        rows = load_player_seasons(args.seasons)
        spread_report(rows, args.cohort)

    payload = build_benchmarks_artifact(seasons=args.seasons, cohort=args.cohort)
    cal = payload["reference_comparison"]
    bench = payload["benchmarks"]

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))

    print("\nvs DraftLab's reference values (corroboration, not a grade):")
    for k, v in cal.items():
        flag = "" if v["error_pct"] < 10 else "   <- diverges"
        print(f"  {k:<22} his {v['draftlab']:>7.2f}   ours {v['computed']:>7.2f}"
              f"   {v['error_pct']:>5.1f}%{flag}")
    spread = [v["error_pct"] for v in cal.values()]
    print(f"  median {statistics.median(spread):.1f}%, max {max(spread):.1f}%  "
          f"({sum(1 for s in spread if s < 10)}/{len(spread)} agree within 10%)")
    print("  Two independent estimates. Divergence is a question, not a defect.")

    print("\ncoverage:")
    for pos, b in bench.items():
        print(f"  {pos}  {b['computed']}/{b['total']} factors sourced")

    for w in payload["warnings"]:
        print(f"\nWARNING: {w}")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Dynasty decline risk: which players who are producing NOW are likely to drop
off over the next one to two seasons.

THIS MODULE SCORES PLAYERS
--------------------------
Everything else in sleeper_core reports measurements. This one ranks people,
on purpose: it is a research tool for the league owner's own dynasty
decisions, added after the DraftLab boundary stopped applying to new work.
Every score ships with the inputs behind it, the flags that drove it, and the
model's honest historical hit rate, so the number can be argued with.

WHAT IT PREDICTS
----------------
Population: "producers" in season S, i.e. the top 36 WR, 24 RB and 14 TE by PPR
points per game among players with enough games. A player who has already
fallen out of that group is not scored. He has already declined, and the
point of this tool is to see it coming.

Outcome: PPR points per game in S+1 (risk_1yr) or S+2 (risk_2yr) below 75% of
season S. Seasons where the player played fewer than 4 games are dropped from
training rather than counted as a decline, because the data cannot tell a
retirement from a torn ACL.

HOW GOOD IT IS
--------------
Not very, and the output says so. Leave-one-season-out AUC is about 0.62
(1 yr) and 0.64 (2 yr). The value is in the tails: the top risk quintile
declined roughly 64% of the time within two years against a 41% base rate.
The per-band historical rates are stored in the model file and attached to
every player, so a 0.80 is never read as "80% certain".

THREE TRAPS THIS AVOIDS (found by reading output, not by tests)
--------------------------------------------------------------
1. A breakout is only a warning after 25. Under 25, a big jump in PPG was
   followed by a decline LESS often than no jump (26% vs 31% over two years).
   So only d_ppg_old is a model input. An earlier version also fed in the
   young jump with its own small weight; Matthew Golden's +12 PPG (6.6 sd)
   extrapolated that into a top-quintile score the history does not support.
2. A recovery is not a breakout. The jump is measured against the best of the
   previous two seasons, not just last season. Otherwise a star bouncing back
   from an injury year (Jefferson 2026) gets flagged as a regression candidate.
3. Standardized inputs are clipped to +/-Z_CLIP before scoring, in training
   and live alike. A linear model has no business extrapolating to 6 sd.

DATA
----
All nflverse, all public: play-by-play (targets, carries, TDs, expected TDs
by field position, YAC over expected, EPA), snap counts (games, snap share),
rosters (birth date, position, pfr/gsis/sleeper id crosswalk). Points are
standard PPR computed from play-by-play, not the league's custom scoring.

Play-by-play is ~150 MB uncompressed per season. It is streamed through
csv.reader with only the needed columns kept, and aggregated on the fly, so
memory stays flat. Do not route it through nflverse_csv(): that builds a dict
per row and would hold every column of every play.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import time
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .config import CACHE_DIR, NFLVERSE_BASE, STATS_CACHE_TTL
from .http import _fetch_raw, nflverse_csv

MODEL_PATH = Path(__file__).resolve().parent / "data" / "decline_model.json"

POSITIONS = ("WR", "RB", "TE")
PRODUCER_TOP = {"WR": 36, "RB": 24, "TE": 14}
POSITION_PEAK_AGE = {"WR": 27.5, "RB": 25.5, "TE": 28.5}
MIN_GAMES_COMPLETE = 7
MIN_GAMES_OUTCOME = 4
DECLINE_RATIO = 0.75
YOUNG_BREAKOUT_AGE = 25.0

# Field-position buckets for expected TDs (yardline_100 upper bounds).
TD_BUCKETS = (2, 5, 10, 20, 40, 100)

FEATURES = (
    "d_ppg_old", "td_frac", "td_oe_g", "snap_pct", "dur",
    "rank_frac", "yacoe", "epa_t", "age_rel", "age_over", "d_tshare", "d_tps",
    "is_te", "is_rb",
)

# Plain-English flag text. Only features whose contribution pushes risk UP by
# more than FLAG_MIN_CONTRIB (in log-odds) are reported, top three per player.
FLAG_MIN_CONTRIB = 0.15
Z_CLIP = 3.0
FLAG_LABELS = {
    "d_ppg_old": "above his recent best at 25+",
    "td_frac": "TD-dependent",
    "td_oe_g": "TDs above expectation",
    "snap_pct": "low snap share",
    "dur": "missed games",
    "rank_frac": "fringe starter at his position",
    "yacoe": "weak YAC vs expected",
    "epa_t": "low EPA per target",
    "age_rel": "age",
    "age_over": "past his position's peak age",
    "d_tshare": "target share jumped",
    "d_tps": "targets per snap jumped",
    "is_te": "TE",
    "is_rb": "RB",
}

PBP_COLS = (
    "season_type", "game_id", "posteam", "pass_attempt", "rush_attempt",
    "sack", "two_point_attempt", "receiver_player_id", "rusher_player_id",
    "complete_pass", "receiving_yards", "rushing_yards", "td_player_id",
    "fumble_lost", "fumbled_1_player_id", "yardline_100", "yards_after_catch",
    "xyac_mean_yardage", "epa", "week",
)


# --------------------------------------------------------------------------
# Small parsing helpers
# --------------------------------------------------------------------------


def _num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "NA", "NULL", "nan", "NaN"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _flag(v: Any) -> bool:
    n = _num(v)
    return n is not None and n >= 1


def _bucket(yardline: float | None) -> int:
    if yardline is None:
        return len(TD_BUCKETS) - 1
    for i, hi in enumerate(TD_BUCKETS):
        if yardline <= hi:
            return i
    return len(TD_BUCKETS) - 1


def age_on(birth_date: str | None, season: int) -> float | None:
    """Age on 1 September of the season, in years."""
    if not birth_date:
        return None
    try:
        y, m, d = (int(x) for x in str(birth_date)[:10].split("-"))
        return (date(season, 9, 1) - date(y, m, d)).days / 365.25
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
# Season aggregation (pure: takes rows, returns per-player dicts)
# --------------------------------------------------------------------------


def aggregate_pbp(rows: Iterable[dict]) -> dict:
    """Fold play-by-play rows into per-player and per-team season totals.

    rows are dicts keyed by PBP_COLS (strings, as read from CSV). Regular
    season only; two-point tries excluded. Expected TDs use this season's
    league-wide TD rate per field-position bucket, separately for targets and
    carries, so they need the whole season before they can be resolved.
    """
    players: dict[str, dict] = {}
    team_tgt: dict[str, int] = {}
    team_games: dict[str, set] = {}
    lg = {"t": [[0, 0] for _ in TD_BUCKETS], "r": [[0, 0] for _ in TD_BUCKETS]}
    max_week = 0

    def p(pid: str) -> dict:
        rec = players.get(pid)
        if rec is None:
            rec = players[pid] = {
                "team": None, "tgt": 0, "rec": 0, "car": 0, "pts": 0.0, "td": 0,
                "fl": 0, "epa_sum": 0.0, "epa_n": 0, "yacoe_sum": 0.0, "yacoe_n": 0,
                "tb": [0] * len(TD_BUCKETS), "rb": [0] * len(TD_BUCKETS),
            }
        return rec

    for r in rows:
        if (r.get("season_type") or "") != "REG" or _flag(r.get("two_point_attempt")):
            continue
        team = r.get("posteam") or ""
        if team:
            team_games.setdefault(team, set()).add(r.get("game_id"))
        wk = _num(r.get("week"))
        if wk and wk > max_week:
            max_week = int(wk)

        if _flag(r.get("fumble_lost")) and r.get("fumbled_1_player_id"):
            p(r["fumbled_1_player_id"])["fl"] += 1

        td_pid = r.get("td_player_id") or ""
        b = _bucket(_num(r.get("yardline_100")))

        if _flag(r.get("pass_attempt")) and not _flag(r.get("sack")):
            if team:
                team_tgt[team] = team_tgt.get(team, 0) + 1
            rid = r.get("receiver_player_id") or ""
            if rid:
                x = p(rid)
                x["team"] = team or x["team"]
                x["tgt"] += 1
                x["tb"][b] += 1
                is_td = td_pid == rid
                lg["t"][b][0] += 1
                lg["t"][b][1] += int(is_td)
                if _flag(r.get("complete_pass")):
                    x["rec"] += 1
                    x["pts"] += 1 + (_num(r.get("receiving_yards")) or 0) / 10
                    yac, xyac = _num(r.get("yards_after_catch")), _num(r.get("xyac_mean_yardage"))
                    if yac is not None and xyac is not None:
                        x["yacoe_sum"] += yac - xyac
                        x["yacoe_n"] += 1
                if is_td:
                    x["td"] += 1
                    x["pts"] += 6
                e = _num(r.get("epa"))
                if e is not None:
                    x["epa_sum"] += e
                    x["epa_n"] += 1

        if _flag(r.get("rush_attempt")):
            uid = r.get("rusher_player_id") or ""
            if uid:
                x = p(uid)
                x["team"] = x["team"] or team
                x["car"] += 1
                x["rb"][b] += 1
                is_td = td_pid == uid
                lg["r"][b][0] += 1
                lg["r"][b][1] += int(is_td)
                x["pts"] += (_num(r.get("rushing_yards")) or 0) / 10
                if is_td:
                    x["td"] += 1
                    x["pts"] += 6

    rate_t = [(tds / n if n else 0.0) for n, tds in lg["t"]]
    rate_r = [(tds / n if n else 0.0) for n, tds in lg["r"]]
    for x in players.values():
        x["xtd"] = sum(c * rt for c, rt in zip(x["tb"], rate_t)) + sum(
            c * rr for c, rr in zip(x["rb"], rate_r))
        x["pts"] -= 2 * x["fl"]
        del x["tb"], x["rb"]

    return {
        "players": players,
        "team_tgt": team_tgt,
        "team_games": {t: len(g) for t, g in team_games.items()},
        "max_week": max_week,
    }


def aggregate_snaps(rows: Iterable[dict], pfr_to_gsis: dict[str, str]) -> dict[str, dict]:
    """Games played, mean offensive snap share and total snaps per gsis id."""
    out: dict[str, dict] = {}
    for r in rows:
        if (r.get("game_type") or "REG") != "REG":
            continue
        snaps = _num(r.get("offense_snaps")) or 0
        if snaps <= 0:
            continue
        gsis = pfr_to_gsis.get(r.get("pfr_player_id") or "")
        if not gsis:
            continue
        x = out.setdefault(gsis, {"games": set(), "pct_sum": 0.0, "snaps": 0.0})
        if r.get("game_id") in x["games"]:
            continue
        x["games"].add(r.get("game_id"))
        x["pct_sum"] += _num(r.get("offense_pct")) or 0
        x["snaps"] += snaps
    return {
        k: {"g": len(v["games"]), "snap_pct": v["pct_sum"] / len(v["games"]), "snaps": v["snaps"]}
        for k, v in out.items()
    }


def season_table(pbp: dict, snaps: dict[str, dict], roster: dict[str, dict], season: int) -> dict[str, dict]:
    """Join pbp, snaps and roster into one per-player season record (RB/WR/TE)."""
    out: dict[str, dict] = {}
    tg = pbp["team_games"]
    for gsis, x in pbp["players"].items():
        ident = roster.get(gsis)
        if not ident or ident["position"] not in POSITIONS:
            continue
        s = snaps.get(gsis) or {}
        g = s.get("g", 0)
        team = x["team"] or ident.get("team")
        team_g = tg.get(team) or 0
        team_t = pbp["team_tgt"].get(team) or 0
        rec = {
            "gsis_id": gsis,
            "sleeper_id": ident.get("sleeper_id"),
            "name": ident.get("name"),
            "position": ident["position"],
            "team": team,
            "season": season,
            "age": age_on(ident.get("birth_date"), season),
            "g": g,
            "team_games": team_g,
            "tgt": x["tgt"],
            "rec": x["rec"],
            "car": x["car"],
            "pts": round(x["pts"], 2),
            "td": x["td"],
            "xtd": x["xtd"],
            "ppg": (x["pts"] / g) if g else None,
            "snap_pct": s.get("snap_pct"),
            "tps": (x["tgt"] / s["snaps"]) if s.get("snaps") else None,
            "tshare": ((x["tgt"] / g) / (team_t / team_g)) if g and team_g and team_t else None,
            "epa_t": (x["epa_sum"] / x["epa_n"]) if x["epa_n"] else None,
            "yacoe": (x["yacoe_sum"] / x["yacoe_n"]) if x["yacoe_n"] else None,
        }
        out[gsis] = rec
    return out


def min_games_for(team_games_median: float, complete: bool) -> int:
    if complete:
        return MIN_GAMES_COMPLETE
    return max(3, round(0.55 * team_games_median))


def producers(table: dict[str, dict], min_games: int) -> list[dict]:
    """Top-N by PPG at each position among players with enough games."""
    out = []
    for pos in POSITIONS:
        pool = [r for r in table.values()
                if r["position"] == pos and r["g"] >= min_games and r["ppg"] is not None]
        pool.sort(key=lambda r: -r["ppg"])
        top = PRODUCER_TOP[pos]
        for i, r in enumerate(pool[:top], start=1):
            out.append({**r, "pos_rank": i, "rank_frac": i / top})
    return out


def position_rank(table: dict[str, dict], gsis: str, min_games: int) -> int | None:
    r = table.get(gsis)
    if not r or r["ppg"] is None:
        return None
    pool = sorted((x["ppg"] for x in table.values()
                   if x["position"] == r["position"] and x["g"] >= min_games and x["ppg"] is not None),
                  reverse=True)
    if r["g"] < min_games:
        return None
    return pool.index(r["ppg"]) + 1


def build_features(prod: dict, prev1: dict | None, prev2: dict | None, fills: dict) -> tuple[dict, dict]:
    """Model inputs for one producer, plus provenance for each one.

    prev1/prev2 are the same player's season records one and two years back
    (or None). fills holds training medians for inputs that can be missing.
    """
    prov: dict[str, str] = {}
    f: dict[str, float] = {}

    priors = [p["ppg"] for p in (prev1, prev2)
              if p and p.get("ppg") is not None and p.get("g", 0) >= MIN_GAMES_OUTCOME]
    best_prior = max(priors) if priors else None
    d_base = (prod["ppg"] - best_prior) if best_prior is not None else 0.0
    prov["best_prior_ppg"] = "measured" if best_prior is not None else "missing:no_prior_season"
    age = prod.get("age")
    if age is None:
        age = fills["age"]
        prov["age"] = "imputed:training_median"
    else:
        prov["age"] = "measured"
    young = age < YOUNG_BREAKOUT_AGE
    f["d_ppg_old"] = 0.0 if young else d_base

    pts = prod["pts"]
    f["td_frac"] = (6 * prod["td"] / pts) if pts > 0 else 0.0
    f["td_oe_g"] = (prod["td"] - prod["xtd"]) / prod["g"]
    for k in ("snap_pct", "yacoe", "epa_t"):
        if prod.get(k) is None:
            f[k] = fills[k]
            prov[k] = "imputed:training_median"
        else:
            f[k] = prod[k]
            prov[k] = "measured"
    f["dur"] = prod["g"] / prod["team_games"] if prod["team_games"] else 1.0
    f["rank_frac"] = prod["rank_frac"]
    f["age_rel"] = age - POSITION_PEAK_AGE[prod["position"]]
    f["age_over"] = max(0.0, f["age_rel"])
    for k, src in (("d_tshare", "tshare"), ("d_tps", "tps")):
        if prev1 and prev1.get(src) is not None and prod.get(src) is not None:
            f[k] = prod[src] - prev1[src]
            prov[k] = "measured"
        else:
            f[k] = 0.0
            prov[k] = "missing:no_prior_season"
    f["is_te"] = 1.0 if prod["position"] == "TE" else 0.0
    f["is_rb"] = 1.0 if prod["position"] == "RB" else 0.0
    f["_best_prior_ppg"] = best_prior  # carried for display, not a model input
    return f, prov


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


def load_model(path: Path = MODEL_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def zscore(x: float, mean: float, std: float) -> float:
    return max(-Z_CLIP, min(Z_CLIP, (x - mean) / std))


def score(features: dict, head: dict) -> tuple[float, dict[str, float]]:
    """Logistic score plus per-feature log-odds contributions."""
    z = head["intercept"]
    contrib = {}
    for k in FEATURES:
        c = head["coef"][k] * zscore(features[k], head["mean"][k], head["std"][k])
        contrib[k] = c
        z += c
    return 1 / (1 + math.exp(-z)), contrib


def band_for(risk: float, calibration: list[dict]) -> dict:
    """Which historical risk quintile a score falls in, and how that band did."""
    for b in calibration:
        if risk <= b["max_risk"]:
            return b
    return calibration[-1]


def flags_for(contrib: dict[str, float], features: dict, prod: dict) -> list[dict]:
    detail = {
        "d_ppg_old": lambda: f"{prod['ppg']:.1f} PPG vs best prior {features['_best_prior_ppg']:.1f}",
        "td_frac": lambda: f"{features['td_frac'] * 100:.0f}% of points from TDs",
        "td_oe_g": lambda: f"{prod['td']} TDs vs {prod['xtd']:.1f} expected",
        "snap_pct": lambda: f"{features['snap_pct'] * 100:.0f}% of offensive snaps",
        "dur": lambda: f"played {prod['g']} of {prod['team_games']} games",
        "rank_frac": lambda: f"{prod['position']}{prod['pos_rank']} by PPG",
        "yacoe": lambda: f"{features['yacoe']:+.1f} YAC per catch vs expected",
        "epa_t": lambda: f"{features['epa_t']:+.2f} EPA per target",
        "age_rel": lambda: f"age {prod['age']:.1f}",
        "age_over": lambda: f"age {prod['age']:.1f} vs peak {POSITION_PEAK_AGE[prod['position']]}",
        "d_tshare": lambda: f"target share {features['d_tshare'] * 100:+.1f} pts vs last season",
        "d_tps": lambda: f"targets per snap {features['d_tps']:+.3f} vs last season",
        "is_te": lambda: "position",
        "is_rb": lambda: "position",
    }
    ranked = sorted(((c, k) for k, c in contrib.items() if c > FLAG_MIN_CONTRIB), reverse=True)[:3]
    out = []
    for c, k in ranked:
        try:
            d = detail[k]()
        except (TypeError, KeyError):
            d = None
        out.append({"flag": FLAG_LABELS[k], "detail": d, "log_odds": round(c, 2)})
    return out


# --------------------------------------------------------------------------
# Live data loading (network)
# --------------------------------------------------------------------------

_season_cache: dict[int, tuple[float, dict]] = {}


def _stream_pbp(season: int) -> Iterable[dict]:
    """Yield pbp rows (only PBP_COLS) without materialising the file."""
    raw = None
    for name in (f"play_by_play_{season}.csv.gz", f"play_by_play_{season}.csv"):
        cache_file = CACHE_DIR / name
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < STATS_CACHE_TTL:
            raw = cache_file.read_bytes()
        else:
            try:
                raw = _fetch_raw(f"{NFLVERSE_BASE}/pbp/{name}", cache_file)
            except Exception:  # noqa: BLE001 - degrade like every nflverse fetch
                raw = None
        if raw is not None:
            gz = name.endswith(".gz")
            break
    if raw is None:
        return
    stream = gzip.GzipFile(fileobj=io.BytesIO(raw)) if gz else io.BytesIO(raw)
    reader = csv.reader(io.TextIOWrapper(stream, encoding="utf-8", newline=""))
    header = next(reader, None)
    if not header:
        return
    idx = {c: header.index(c) for c in PBP_COLS if c in header}
    for row in reader:
        yield {c: (row[i] if i < len(row) else "") for c, i in idx.items()}


def load_roster(season: int) -> dict[str, dict]:
    rows = nflverse_csv(
        "rosters", f"roster_{season}.csv",
        row_filter=lambda r: (r.get("position") or "").upper() in POSITIONS,
        ttl=STATS_CACHE_TTL,
    )
    out: dict[str, dict] = {}
    for r in rows:
        gsis = r.get("gsis_id") or ""
        if not gsis:
            continue
        out[gsis] = {  # later weekly rows overwrite earlier ones: latest team wins
            "name": r.get("full_name"),
            "position": (r.get("position") or "").upper(),
            "team": r.get("team"),
            "birth_date": r.get("birth_date"),
            "pfr_id": r.get("pfr_id") or None,
            "sleeper_id": (r.get("sleeper_id") or "").split(".")[0] or None,
        }
    return out


def load_season(season: int, ttl: float = STATS_CACHE_TTL) -> dict:
    """Per-player season records for one season, cached in memory."""
    hit = _season_cache.get(season)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    roster = load_roster(season)
    pfr_to_gsis = {v["pfr_id"]: k for k, v in roster.items() if v.get("pfr_id")}
    snap_rows = nflverse_csv(
        "snap_counts", f"snap_counts_{season}.csv",
        row_filter=lambda r: (r.get("position") or "").upper() in POSITIONS,
        ttl=STATS_CACHE_TTL,
    )
    pbp = aggregate_pbp(_stream_pbp(season))
    snaps = aggregate_snaps(snap_rows, pfr_to_gsis)
    table = season_table(pbp, snaps, roster, season)
    tgs = sorted(pbp["team_games"].values())
    median_tg = tgs[len(tgs) // 2] if tgs else 0
    full = 16 if season <= 2020 else 17
    data = {
        "season": season,
        "table": table,
        "team_games_median": median_tg,
        "complete": median_tg >= full,
        "max_week": pbp["max_week"],
        "has_pbp": bool(pbp["players"]),
    }
    _season_cache[season] = (time.time(), data)
    return data


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------


def assess(seasons: dict[int, dict], season: int, model: dict) -> tuple[list[dict], dict]:
    """Score every producer in `season`. Returns (players, context)."""
    cur = seasons[season]
    min_g = min_games_for(cur["team_games_median"], cur["complete"])
    prev1 = seasons.get(season - 1, {}).get("table", {})
    prev2 = seasons.get(season - 2, {}).get("table", {})
    out = []
    for prod in producers(cur["table"], min_g):
        feats, prov = build_features(prod, prev1.get(prod["gsis_id"]), prev2.get(prod["gsis_id"]),
                                     model["fills"])
        r2, c2 = score(feats, model["heads"]["2yr"])
        r1, _ = score(feats, model["heads"]["1yr"])
        b2 = band_for(r2, model["heads"]["2yr"]["calibration"])
        out.append({
            "name": prod["name"],
            "sleeper_id": prod["sleeper_id"],
            "gsis_id": prod["gsis_id"],
            "position": prod["position"],
            "team": prod["team"],
            "age": round(prod["age"], 1) if prod["age"] is not None else None,
            "games": prod["g"],
            "ppg": round(prod["ppg"], 2),
            "position_rank": prod["pos_rank"],
            "best_prior_ppg": round(feats["_best_prior_ppg"], 2) if feats["_best_prior_ppg"] is not None else None,
            "risk_2yr": round(r2, 3),
            "risk_1yr": round(r1, 3),
            "risk_band_2yr": {
                "quintile": b2["quintile"],
                "historical_decline_rate": b2["decline_rate"],
            },
            "flags": flags_for(c2, feats, prod),
            "inputs": {
                "td_share_of_points": round(feats["td_frac"], 3),
                "tds": prod["td"],
                "expected_tds": round(prod["xtd"], 2),
                "snap_pct": round(feats["snap_pct"], 3),
                "target_share": round(prod["tshare"], 3) if prod["tshare"] is not None else None,
                "target_share_change": round(feats["d_tshare"], 3),
                "targets_per_snap_change": round(feats["d_tps"], 4),
                "yac_over_expected": round(feats["yacoe"], 2),
                "epa_per_target": round(feats["epa_t"], 3),
            },
            "provenance": prov,
        })
    out.sort(key=lambda r: -r["risk_2yr"])
    for i, r in enumerate(out, start=1):
        r["risk_rank"] = i
    ctx = {"min_games": min_g, "complete": cur["complete"], "max_week": cur["max_week"]}
    return out, ctx


def attach_values(players: list[dict], fc_rows: list[dict] | None) -> None:
    """Add FantasyCalc value by exact sleeper_id. A miss is null with a reason."""
    if fc_rows is None:
        for p in players:
            p["trade_value"] = None
            p["trade_value_note"] = "not requested"
        return
    by_id = {str(r.get("sleeper_id")): r for r in fc_rows if r.get("sleeper_id")}
    for p in players:
        r = by_id.get(str(p.get("sleeper_id") or ""))
        if r:
            p["trade_value"] = {
                "value": r.get("value"),
                "overall_rank": r.get("overall_rank"),
                "position_rank": r.get("position_rank"),
                "trend_30_day": r.get("trend_30_day"),
            }
        else:
            p["trade_value"] = None
            p["trade_value_note"] = ("no sleeper_id in nflverse roster" if not p.get("sleeper_id")
                                     else "not in FantasyCalc values for this format")


def _norm(s: str | None) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def report(
    season: int,
    model: dict,
    seasons: dict[int, dict],
    fc_rows: list[dict] | None,
    position: str | None = None,
    team: str | None = None,
    player: str | None = None,
    min_value: int | None = None,
    sort: str = "risk",
    limit: int = 30,
) -> dict:
    """Assemble the tool response. Pure given loaded seasons and FC rows."""
    cur = seasons.get(season)
    if not cur or not cur.get("has_pbp"):
        return {"error": f"no nflverse play-by-play for {season}", "season": season}
    players, ctx = assess(seasons, season, model)
    attach_values(players, fc_rows)

    rows = players
    if position:
        rows = [r for r in rows if r["position"] == position.upper()]
    if team:
        rows = [r for r in rows if (r["team"] or "").upper() == team.upper()]
    if min_value is not None:
        rows = [r for r in rows if r.get("trade_value") and (r["trade_value"]["value"] or 0) >= min_value]

    not_scored = []
    if player:
        q = _norm(player)
        rows = [r for r in rows if q in _norm(r["name"])]
        scored_ids = {r["gsis_id"] for r in rows}
        for gsis, rec in cur["table"].items():
            if q in _norm(rec["name"]) and gsis not in scored_ids:
                rank = position_rank(cur["table"], gsis, ctx["min_games"])
                not_scored.append({
                    "name": rec["name"], "position": rec["position"], "team": rec["team"],
                    "games": rec["g"], "ppg": round(rec["ppg"], 2) if rec["ppg"] is not None else None,
                    "position_rank_by_ppg": rank,
                    "reason": (f"fewer than {ctx['min_games']} games" if rec["g"] < ctx["min_games"]
                               else f"not a producer: {rec['position']}{rank} by PPG, cutoff is "
                                    f"{rec['position']}{PRODUCER_TOP[rec['position']]}. "
                                    "Already below the line this model is built to warn about."),
                })

    if sort == "value":
        rows = sorted(rows, key=lambda r: -((r.get("trade_value") or {}).get("value") or 0))

    ev = model["evaluation"]
    out = {
        "season": season,
        "in_progress": not ctx["complete"],
        "through_week": ctx["max_week"],
        "population": (f"producers: top {PRODUCER_TOP['WR']} WR / {PRODUCER_TOP['RB']} RB / "
                       f"{PRODUCER_TOP['TE']} TE by PPR PPG with >= {ctx['min_games']} games"),
        "scored": len(players),
        "model": {
            "predicts": model["definition"]["decline"],
            "fitted_at": model["fitted_at"],
            "loso_auc": {k: v["loso_auc"] for k, v in ev.items()},
            "base_rate": {k: v["base_rate"] for k, v in ev.items()},
            "calibration_2yr": [{"quintile": b["quintile"], "decline_rate": b["decline_rate"]}
                                for b in model["heads"]["2yr"]["calibration"]],
            "how_to_read": ("risk scores rank players; they are not probabilities. Use "
                            "risk_band_2yr.historical_decline_rate for what that band actually did."),
        },
        "players": rows[:limit],
        "source": "nflverse play-by-play, snap counts, rosters (MIT); FantasyCalc trade values (unofficial)",
    }
    if not ctx["complete"]:
        out["note"] = (f"Season in progress (through week {ctx['max_week']}): inputs are from a partial "
                       "season and scores will move.")
    if player:
        out["not_scored"] = not_scored
    return out

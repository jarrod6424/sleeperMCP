"""
Refit the dynasty decline-risk model and write sleeper_core/data/decline_model.json.

    python tools/fit_decline_model.py                  # train on 2020..latest
    python tools/fit_decline_model.py --first 2019 --last 2025

Run it once a season, after the regular season ends. It downloads one
play-by-play file per season (~20 MB gz each) plus snap counts and rosters,
so the first run takes a few minutes; later runs hit the disk cache.

Features come from sleeper_core.decline, the exact code the live tool uses, so
training and serving cannot drift apart.

WHAT TO LOOK AT AFTER A REFIT
-----------------------------
The script prints leave-one-season-out AUC and the decline rate by risk
quintile for both horizons. Those are the honest numbers: each season is
scored by a model that never saw it. If the top quintile stops beating the
base rate, the model has stopped working; do not ship it just because the
file was written.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sleeper_core import decline as D  # noqa: E402

L2 = 10.0
FILL_KEYS = ("snap_pct", "yacoe", "epa_t", "age")


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2) if n else 0.0


def solve(a, b):
    """Gaussian elimination with partial pivoting (small dense systems)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit_logistic(x, y, l2=L2, iters=40):
    """Ridge logistic regression by Newton's method; intercept unpenalised."""
    k = len(x[0]) + 1
    w = [0.0] * k
    xb = [[1.0] + row for row in x]
    for _ in range(iters):
        h = [[0.0] * k for _ in range(k)]
        g = [0.0] * k
        for row, yi in zip(xb, y):
            z = sum(wi * xi for wi, xi in zip(w, row))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            wt = p * (1 - p)
            for i in range(k):
                g[i] += (p - yi) * row[i]
                ri = row[i] * wt
                for j in range(i, k):
                    h[i][j] += ri * row[j]
        for i in range(k):
            for j in range(i):
                h[i][j] = h[j][i]
        for i in range(1, k):
            h[i][i] += l2
            g[i] += l2 * w[i]
        step = solve(h, g)
        w = [wi - si for wi, si in zip(w, step)]
        if max(abs(s) for s in step) < 1e-8:
            break
    return w


def standardize(rows):
    mean = {k: sum(r[k] for r in rows) / len(rows) for k in D.FEATURES}
    std = {}
    for k in D.FEATURES:
        v = sum((r[k] - mean[k]) ** 2 for r in rows) / max(1, len(rows) - 1)
        std[k] = math.sqrt(v) or 1.0
    return mean, std


def train_head(rows):
    mean, std = standardize(rows)
    x = [[D.zscore(r[k], mean[k], std[k]) for k in D.FEATURES] for r in rows]
    w = fit_logistic(x, [r["_y"] for r in rows])
    return {"intercept": w[0], "coef": dict(zip(D.FEATURES, w[1:])), "mean": mean, "std": std}


def auc(y, s):
    order = sorted(range(len(s)), key=lambda i: s[i])
    ranks = [0.0] * len(s)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and s[order[j + 1]] == s[order[i]]:
            j += 1
        for t in range(i, j + 1):
            ranks[order[t]] = (i + j) / 2 + 1
        i = j + 1
    n1 = sum(y)
    n0 = len(y) - n1
    return (sum(r for r, yi in zip(ranks, y) if yi) - n1 * (n1 + 1) / 2) / (n1 * n0)


def loso(rows):
    """Leave-one-season-out predictions."""
    preds = [0.0] * len(rows)
    for s in sorted({r["_season"] for r in rows}):
        tr = [r for r in rows if r["_season"] != s]
        head = train_head(tr)
        for i, r in enumerate(rows):
            if r["_season"] == s:
                preds[i] = D.score(r, head)[0]
    return preds


def calibration(preds, y):
    idx = sorted(range(len(preds)), key=lambda i: preds[i])
    n = len(idx)
    out = []
    for q in range(5):
        part = idx[q * n // 5:(q + 1) * n // 5]
        out.append({
            "quintile": q + 1,
            "max_risk": round(max(preds[i] for i in part), 4) if q < 4 else 1.0,
            "n": len(part),
            "decline_rate": round(sum(y[i] for i in part) / len(part), 3),
        })
    return out


def build_rows(seasons, first, last):
    """Producer feature rows with both outcomes attached where known."""
    rows = []
    for s in range(first, last + 1):
        cur = seasons[s]
        min_g = D.min_games_for(cur["team_games_median"], cur["complete"])
        p1 = seasons.get(s - 1, {}).get("table", {})
        p2 = seasons.get(s - 2, {}).get("table", {})
        n1 = seasons.get(s + 1, {}).get("table")
        n2 = seasons.get(s + 2, {}).get("table")
        for prod in D.producers(cur["table"], min_g):
            rows.append((s, prod, p1.get(prod["gsis_id"]), p2.get(prod["gsis_id"]),
                         (n1 or {}).get(prod["gsis_id"]) if n1 is not None else "unknown",
                         (n2 or {}).get(prod["gsis_id"]) if n2 is not None else "unknown"))
    return rows


def outcome(prod, nxt):
    if nxt == "unknown":
        return None
    if not nxt or nxt["g"] < D.MIN_GAMES_OUTCOME or nxt["ppg"] is None:
        return "dropped"
    return int(nxt["ppg"] < D.DECLINE_RATIO * prod["ppg"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=2020, help="first producer season")
    ap.add_argument("--last", type=int, default=None, help="last season with data (default: latest complete + 1)")
    ap.add_argument("--out", default=str(D.MODEL_PATH))
    args = ap.parse_args()

    last = args.last
    if last is None:
        from sleeper_core.stats import current_season
        last = int(current_season())

    t0 = time.time()
    seasons = {}
    for s in range(args.first - 2, last + 1):
        print(f"loading {s}...", flush=True)
        seasons[s] = D.load_season(s)
        if not seasons[s]["has_pbp"]:
            print(f"  no play-by-play for {s}; is raw.githubusercontent.com reachable?")
            return 1
    print(f"loaded in {time.time() - t0:.0f}s")

    complete = [s for s in range(args.first, last + 1) if seasons[s]["complete"]]
    raw = build_rows(seasons, args.first, max(complete))

    # Fills: training medians over producer rows with a measured value.
    fills = {k: median([prod[k] for _, prod, *_ in raw if prod.get(k) is not None]) for k in FILL_KEYS}

    heads, report = {}, {}
    for name, horizon in (("1yr", 0), ("2yr", 1)):
        rows, dropped = [], 0
        for s, prod, a, b, *nx in raw:
            y = outcome(prod, nx[horizon])
            if y is None:
                continue
            if y == "dropped":
                dropped += 1
                continue
            f, _ = D.build_features(prod, a, b, fills)
            f["_y"], f["_season"] = y, s
            rows.append(f)
        preds = loso(rows)
        ys = [r["_y"] for r in rows]
        head = train_head(rows)
        head["calibration"] = calibration(preds, ys)
        heads[name] = head
        report[name] = {
            "n": len(rows),
            "dropped_short_or_missing_next": dropped,
            "base_rate": round(sum(ys) / len(ys), 3),
            "loso_auc": round(auc(ys, preds), 3),
            "seasons": sorted({r["_season"] for r in rows}),
        }
        print(f"{name}: n={len(rows)} base={report[name]['base_rate']} "
              f"LOSO AUC={report[name]['loso_auc']} "
              f"quintiles={[b['decline_rate'] for b in head['calibration']]}")

    model = {
        "version": 1,
        "fitted_at": time.strftime("%Y-%m-%d"),
        "features": list(D.FEATURES),
        "fills": fills,
        "definition": {
            "population": "top 36 WR / 24 RB / 14 TE by PPR PPG, min games 7 (55% of team games in-season)",
            "decline": f"PPR PPG below {D.DECLINE_RATIO:.0%} of the scored season, in a season with >= {D.MIN_GAMES_OUTCOME} games",
            "excluded": "next seasons with < 4 games or no appearance (retired / long-term injured)",
            "scoring": "standard PPR computed from nflverse play-by-play",
        },
        "evaluation": report,
        "heads": heads,
    }
    Path(args.out).write_text(json.dumps(model, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

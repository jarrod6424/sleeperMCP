# WR YPRR Proxy, NGS Catch %, and Volume Factors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill WR `yprr` (participation-route proxy) and `reception_perception` (NGS catch %), add `yards_per_catch`, `yac_per_reception`, and `target_share`, bump WR known-factor coverage to 15, leave only `ol_pass_block_rank` unknown.

**Architecture:** Extend sleeperMCP `build_benchmarks.py` / `build_factors.py` to emit the five values from nflverse weekly stats, participation route counts, and Next Gen Stats receiving. DraftLab updates WR factor labels/benchmarks and `CEILING_KNOWN_FACTORS.WR` 10→15. Publish via existing R2 Action and redeploy Worker.

**Tech Stack:** Python 3.12, pytest, nflverse CSV (`stats_player`, `pbp_participation`, `nextgen_stats`), TypeScript/Vitest (DraftLab), Cloudflare R2 / Wrangler.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-11-wr-yprr-catch-volume-design.md`
- Keep factor ids `yprr` and `reception_perception`; honesty via labels + source tags
- Never fabricate numeric `0` for rates / catch % / YPRR on loader failure — leave unset
- `ol_pass_block_rank` stays `licensed:PFF` / unknown
- Out of scope: renaming ids, TE `yprr_rank`, separation/composite RP proxies, OL pass block
- TDD: failing test first; commit after each green task
- Two repos: sleeperMCP (`c:\Code\sleeperMCP`) then DraftLab (`c:\Code\fantasy-football-draft-optimizer\fantasy-football-draft-optimizer`)

## File map

| File | Responsibility |
|------|----------------|
| `tools/build_benchmarks.py` | Aggregate YAC + target_share; WR efficiency rates; route counts → yprr; NGS catch %; FACTORS / COMPUTABLE / FACTOR_KIND |
| `tools/build_factors.py` | Emit new WR factors (no TEAM_CONTEXT change required unless yprr treated as team-stale — it is player-measured; leave TEAM_CONTEXT unchanged) |
| `tests/test_wr_volume_efficiency.py` | YPC, YAC/rec, target_share |
| `tests/test_wr_yprr_proxy.py` | yards / on_pass |
| `tests/test_wr_ngs_catch_pct.py` | NGS → reception_perception |
| `docs/PLAN.md`, `docs/SAD.md` | ITEM-005 status + coverage |
| DraftLab `packages/evaluation-engine/src/config/benchmarks.ts` | New volume factors + proxy labels + cohort means |
| DraftLab `packages/evaluation-engine/src/config/grade-weights.ts` | `CEILING_KNOWN_FACTORS.WR` 10→15 |
| DraftLab `packages/evaluation-engine/src/__tests__/wr-yprr-catch-volume.test.ts` | Config / known-count assertions |

---

### Task 1: WR volume efficiency — YPC, YAC/rec, target_share

**Files:**
- Modify: `tools/build_benchmarks.py`
- Create: `tests/test_wr_volume_efficiency.py`

**Interfaces:**
- Consumes: `load_player_seasons` weekly aggregation; `_efficiency_yards(ps: dict) -> dict[str, float]`; `per_game(ps: dict) -> dict`
- Produces:
  - Season aggregates on player rows: `receiving_yards_after_catch: float`, `target_share: float` (mean of weekly shares)
  - Via `_efficiency_yards` / `per_game`: `yards_per_catch`, `yac_per_reception` when `receptions > 0`
  - `FACTORS["WR"]` includes the three ids; all three in `COMPUTABLE`
  - `FACTOR_KIND["target_share"] = "rate"` (already a share; do not divide by games again)

- [ ] **Step 1: Write the failing test**

Create `tests/test_wr_volume_efficiency.py`:

```python
from __future__ import annotations

from unittest.mock import patch

import build_benchmarks as bb


def test_per_game_computes_wr_catch_efficiency() -> None:
    ps = {
        "games": 17,
        "carries": 0.0,
        "receptions": 100.0,
        "rushing_yards": 0.0,
        "receiving_yards": 1200.0,
        "receiving_yards_after_catch": 400.0,
        "attempts": 0.0,
        "passing_tds": 0.0,
        "rushing_tds": 0.0,
        "targets": 150.0,
        "receiving_tds": 8.0,
        "target_share": 0.28,
    }
    out = bb.per_game(ps)
    assert abs(out["yards_per_catch"] - 12.0) < 1e-6
    assert abs(out["yac_per_reception"] - 4.0) < 1e-6
    assert abs(out["target_share"] - 0.28) < 1e-6


def test_per_game_zero_receptions_omits_catch_rates() -> None:
    ps = {
        "games": 10,
        "carries": 0.0,
        "receptions": 0.0,
        "rushing_yards": 0.0,
        "receiving_yards": 0.0,
        "receiving_yards_after_catch": 0.0,
        "attempts": 0.0,
        "passing_tds": 0.0,
        "rushing_tds": 0.0,
        "targets": 5.0,
        "receiving_tds": 0.0,
    }
    out = bb.per_game(ps)
    assert "yards_per_catch" not in out
    assert "yac_per_reception" not in out


def test_wr_factors_include_volume_efficiency() -> None:
    ids = [f for f, _ in bb.FACTORS["WR"]]
    for fid in ("yards_per_catch", "yac_per_reception", "target_share"):
        assert fid in ids
        assert fid in bb.COMPUTABLE
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `python -m pytest tests/test_wr_volume_efficiency.py -v`  
Working directory: `c:\Code\sleeperMCP` (ensure `tools` is on `PYTHONPATH` the same way existing tests do — typically `python -m pytest` from repo root with `conftest` / path setup already used by `test_rb_yards_factors.py`).  
Expected: FAIL (`yards_per_catch` missing from `per_game` / FACTORS)

- [ ] **Step 3: Aggregate YAC and target_share in `load_player_seasons`**

In the agg initializer (near `receiving_yards`), add:

```python
"receiving_yards_after_catch": 0.0,
"target_share_sum": 0.0,
"target_share_n": 0,
```

When summing weekly rows, also:

```python
a["receiving_yards_after_catch"] += safe_float(r.get("receiving_yards_after_catch"))
ts = safe_float(r.get("target_share"))
if ts > 0 or r.get("target_share") not in (None, ""):
    # Prefer: always count weeks with a present numeric target_share
    if r.get("target_share") not in (None, ""):
        a["target_share_sum"] += safe_float(r.get("target_share"))
        a["target_share_n"] += 1
```

After the weekly loop for each player (before or after snap_share), set:

```python
if a.get("target_share_n"):
    a["target_share"] = a["target_share_sum"] / a["target_share_n"]
```

Use a clear rule: only weeks where `target_share` is present and parseable; omit the factor if `target_share_n == 0`.

- [ ] **Step 4: Extend `_efficiency_yards`**

```python
def _efficiency_yards(ps: dict) -> dict[str, float]:
    carries = ps.get("carries") or 0
    rec = ps.get("receptions") or 0
    rush_yd = ps.get("rushing_yards") or 0
    rec_yd = ps.get("receiving_yards") or 0
    yac = ps.get("receiving_yards_after_catch") or 0
    out: dict[str, float] = {}
    if carries > 0:
        out["yards_per_carry"] = rush_yd / carries
    touches = carries + rec
    if touches > 0:
        out["yards_per_touch"] = (rush_yd + rec_yd) / touches
    if rec > 0:
        out["yards_per_catch"] = rec_yd / rec
        out["yac_per_reception"] = yac / rec
    return out
```

Add to `FACTOR_KIND`:

```python
"target_share": "rate",
"yards_per_catch": "rate",
"yac_per_reception": "rate",
```

(`yards_per_catch` / `yac_per_reception` are produced by `_efficiency_yards` into `per_game` out dict — they are not FACTOR_KIND passthrough from ps; only `target_share` needs passthrough via FACTOR_KIND.)

- [ ] **Step 5: Update `FACTORS["WR"]` and `COMPUTABLE`**

After `receptions` (before or after `touchdowns`):

```python
("yards_per_catch", "nflverse"),
("yac_per_reception", "nflverse"),
("target_share", "nflverse"),
```

Add those three names to `COMPUTABLE`.

- [ ] **Step 6: Run tests — expect PASS**

Run: `python -m pytest tests/test_wr_volume_efficiency.py tests/test_rb_yards_factors.py -v`  
Expected: PASS (RB efficiency still green)

- [ ] **Step 7: Commit (sleeperMCP)**

```bash
git add tools/build_benchmarks.py tests/test_wr_volume_efficiency.py
git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -m "feat: WR yards_per_catch, yac_per_reception, and target_share"
```

---

### Task 2: WR `yprr` from participation route counts

**Files:**
- Modify: `tools/build_benchmarks.py`
- Create: `tests/test_wr_yprr_proxy.py`

**Interfaces:**
- Consumes: existing `load_route_participation` internals / `_route_rates_from_events`
- Produces:
  - `load_route_details(season: int, position: str) -> dict[str, dict[str, float | int]]`  
    Keys = `_qb_name_key(display_name)`. Values = `{"rate": float, "on_pass": int}`. Empty `{}` on failure.
  - `load_route_participation(season, position)` remains a thin wrapper returning only rates (TE/WR callers stay valid).
  - In `load_player_seasons` for WR: if `on_pass > 0` and `receiving_yards` present, set `a["yprr"] = receiving_yards / on_pass`.
  - Source tag for `yprr` in `FACTORS["WR"]`: `nflverse:participation`
  - `yprr` in `COMPUTABLE` and `FACTOR_KIND` as `"rate"`

- [ ] **Step 1: Write the failing test**

Create `tests/test_wr_yprr_proxy.py`:

```python
from __future__ import annotations

from unittest.mock import patch

import build_benchmarks as bb


def test_yprr_from_yards_and_on_pass() -> None:
    # Unit the attach math without full CSV: simulate post-attach row via per_game
    ps = {
        "games": 17,
        "carries": 0.0,
        "receptions": 80.0,
        "rushing_yards": 0.0,
        "receiving_yards": 1200.0,
        "receiving_yards_after_catch": 300.0,
        "attempts": 0.0,
        "passing_tds": 0.0,
        "rushing_tds": 0.0,
        "targets": 120.0,
        "receiving_tds": 8.0,
        "yprr": 1200.0 / 300.0,
    }
    out = bb.per_game(ps)
    assert abs(out["yprr"] - 4.0) < 1e-6


def test_compute_yprr_skips_zero_routes() -> None:
    assert bb.compute_yprr(1200.0, 0) is None
    assert bb.compute_yprr(1200.0, 300) == 4.0


def test_wr_yprr_is_computable() -> None:
    src = dict(bb.FACTORS["WR"])
    assert src["yprr"] == "nflverse:participation"
    assert "yprr" in bb.COMPUTABLE
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `python -m pytest tests/test_wr_yprr_proxy.py -v`  
Expected: FAIL (`compute_yprr` missing / source still `licensed:PFF`)

- [ ] **Step 3: Add `compute_yprr` + refactor route details**

```python
def compute_yprr(receiving_yards: float, on_pass: int) -> float | None:
    if not on_pass or on_pass <= 0:
        return None
    return float(receiving_yards) / float(on_pass)
```

Refactor `load_route_participation` so the heavy lift lives in `load_route_details(season, position)` returning `{key: {"rate": ..., "on_pass": ...}}`. Keep:

```python
def load_route_participation(season: int, position: str) -> dict[str, float]:
    details = load_route_details(season, position)
    return {k: float(v["rate"]) for k, v in details.items() if "rate" in v}
```

Preserve colliding-name drop behavior already in the participation loader.

- [ ] **Step 4: Attach `yprr` in `load_player_seasons`**

Replace the WR/TE participation loop so WR also gets yprr:

```python
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
                yprr = compute_yprr(a.get("receiving_yards") or 0.0, int(d.get("on_pass") or 0))
                if yprr is not None:
                    a["yprr"] = yprr
                    yprr_matched += 1
        print(..., matched, yprr_matched, ...)
    else:
        print(WARNING unset)
```

- [ ] **Step 5: Retag FACTORS / COMPUTABLE / FACTOR_KIND**

```python
("yprr", "nflverse:participation"),
```

Add `"yprr"` to `COMPUTABLE` and `FACTOR_KIND["yprr"] = "rate"`.

- [ ] **Step 6: Run tests — expect PASS**

Run: `python -m pytest tests/test_wr_yprr_proxy.py tests/test_wr_route_participation.py tests/test_te_route_participation.py -v`  
Expected: PASS

- [ ] **Step 7: Commit (sleeperMCP)**

```bash
git add tools/build_benchmarks.py tests/test_wr_yprr_proxy.py
git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -m "feat: WR yprr proxy from participation route counts"
```

---

### Task 3: WR `reception_perception` from NGS catch %

**Files:**
- Modify: `tools/build_benchmarks.py`
- Create: `tests/test_wr_ngs_catch_pct.py`

**Interfaces:**
- Consumes: `nflverse_csv("nextgen_stats", "ngs_receiving.csv", ...)`
- Produces: `load_ngs_catch_pct(season: int) -> dict[str, float]`  
  Keys = `_qb_name_key(player_display_name)`. Values = catch percentage on **0–100** scale. Empty `{}` on failure. Prefer rows with `season == season`, `season_type == "REG"`, and `week` in `{0, "0"}` (season summary). If week-0 absent for a player, leave them unset (do not invent from weekly average unless week-0 coverage is too thin — prefer week-0 only for v1).
  Attach onto WR rows as `reception_perception`.
  Source tag: `nflverse:ngs`. In `COMPUTABLE`. `FACTOR_KIND["reception_perception"] = "rate"`.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from unittest.mock import patch

import build_benchmarks as bb


def test_load_ngs_catch_pct_uses_week_zero() -> None:
    rows = [
        {
            "season": "2024",
            "season_type": "REG",
            "week": "0",
            "player_display_name": "Amon-Ra St. Brown",
            "player_position": "WR",
            "catch_percentage": "68.5",
        },
        {
            "season": "2024",
            "season_type": "REG",
            "week": "5",
            "player_display_name": "Amon-Ra St. Brown",
            "player_position": "WR",
            "catch_percentage": "90.0",
        },
        {
            "season": "2023",
            "season_type": "REG",
            "week": "0",
            "player_display_name": "Amon-Ra St. Brown",
            "player_position": "WR",
            "catch_percentage": "70.0",
        },
    ]
    with patch.object(bb, "nflverse_csv", return_value=rows):
        out = bb.load_ngs_catch_pct(2024)
    assert abs(out[bb._qb_name_key("Amon-Ra St. Brown")] - 68.5) < 1e-6


def test_load_ngs_normalizes_fraction_to_percent() -> None:
    rows = [
        {
            "season": "2024",
            "season_type": "REG",
            "week": "0",
            "player_display_name": "Ja'Marr Chase",
            "player_position": "WR",
            "catch_percentage": "0.72",
        },
    ]
    with patch.object(bb, "nflverse_csv", return_value=rows):
        out = bb.load_ngs_catch_pct(2024)
    assert abs(out[bb._qb_name_key("Ja'Marr Chase")] - 72.0) < 1e-6


def test_load_ngs_empty_is_best_effort() -> None:
    with patch.object(bb, "nflverse_csv", return_value=[]):
        assert bb.load_ngs_catch_pct(2024) == {}


def test_wr_reception_perception_source() -> None:
    assert dict(bb.FACTORS["WR"])["reception_perception"] == "nflverse:ngs"
    assert "reception_perception" in bb.COMPUTABLE
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `python -m pytest tests/test_wr_ngs_catch_pct.py -v`  
Expected: FAIL (`load_ngs_catch_pct` missing)

- [ ] **Step 3: Implement `load_ngs_catch_pct`**

```python
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
                # Prefer REG; if season_type missing, still allow week-0 rows
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
```

Tune `season_type` filtering against a real file sample during implementation if REG is always present.

- [ ] **Step 4: Attach in `load_player_seasons`**

After WR secondary targets (or after participation):

```python
catch_pct = load_ngs_catch_pct(season)
if catch_pct:
    wr_rows = [a for a in agg.values() if a["position"] == "WR"]
    matched = 0
    for a in wr_rows:
        pct = catch_pct.get(_qb_name_key(a["name"]))
        if pct is not None:
            a["reception_perception"] = pct
            matched += 1
    print(f"  ngs {season}: matched {matched}/{len(wr_rows)} WRs to reception_perception",
          file=sys.stderr)
else:
    print(f"  WARNING: no NGS receiving for {season}; reception_perception left unset",
          file=sys.stderr)
```

- [ ] **Step 5: Retag FACTORS**

```python
("reception_perception", "nflverse:ngs"),
```

Add to `COMPUTABLE` and `FACTOR_KIND["reception_perception"] = "rate"`.

- [ ] **Step 6: Run tests — expect PASS**

Run: `python -m pytest tests/test_wr_ngs_catch_pct.py -v`  
Expected: PASS

- [ ] **Step 7: Commit (sleeperMCP)**

```bash
git add tools/build_benchmarks.py tests/test_wr_ngs_catch_pct.py
git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -m "feat: WR reception_perception from NGS catch percentage"
```

---

### Task 4: Regenerate artifacts + docs (sleeperMCP)

**Files:**
- Modify: `artifacts/benchmarks.json`, `artifacts/player_factors.json` (via builders)
- Modify: `docs/PLAN.md`, `docs/SAD.md`
- Optional helper: `tools/run_build_factors.py` (existing)

**Interfaces:**
- Consumes: Tasks 1–3 loaders
- Produces: Regenerated artifacts with five WR factors measured for featured WRs; PLAN ITEM-005 → In Review; SAD lists the five as COMPUTABLE; blocked list drops WR `yprr` / `reception_perception`

- [ ] **Step 1: Rebuild**

```bash
# from c:\Code\sleeperMCP, same truststore/SSL pattern as prior RB rebuild if needed
python tools/run_build_factors.py
# or whatever command regenerated ITEM-004 artifacts
```

Confirm stderr shows NGS / participation / volume matches for the measured season.

- [ ] **Step 2: Spot-check ARSB (or Chase) in `artifacts/player_factors.json`**

Assert present with `provenance: measured` (or equivalent emission):  
`yards_per_catch`, `yac_per_reception`, `target_share`, `yprr`, `reception_perception`.  
`ol_pass_block_rank` still unsourced.

Record WR cohort means from `artifacts/benchmarks.json` for DraftLab Task 5 (copy exact numbers into that task’s commit message / code).

- [ ] **Step 3: Update `docs/PLAN.md`**

Set ITEM-005 status to **In Review** with notes pointing at this plan + regenerated artifacts.

- [ ] **Step 4: Update `docs/SAD.md`**

Add ITEM-005 table rows; remove WR `yprr` / `reception_perception` from “Blocked — licensed”; keep only `ol_pass_block_rank` for WR.

- [ ] **Step 5: Commit (sleeperMCP)**

```bash
git add artifacts/benchmarks.json artifacts/player_factors.json docs/PLAN.md docs/SAD.md
git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -m "chore: regenerate WR yprr/catch%/volume artifacts (ITEM-005)"
```

---

### Task 5: DraftLab WR benchmarks + known-factor bump

**Files:**
- Modify: `packages/evaluation-engine/src/config/benchmarks.ts`
- Modify: `packages/evaluation-engine/src/config/grade-weights.ts`
- Create: `packages/evaluation-engine/src/__tests__/wr-yprr-catch-volume.test.ts`

**Interfaces:**
- Consumes: cohort means from sleeperMCP `artifacts/benchmarks.json` (Task 4)
- Produces: WR factor list with three new volume entries; proxy labels; `CEILING_KNOWN_FACTORS.WR === 15`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, expect, it } from 'vitest';
import { CEILING_RANGE } from '../config/grade-weights.js';
import { POSITION_BENCHMARKS } from '../config/benchmarks.js';

describe('WR yprr / catch% / volume (ITEM-005)', () => {
  it('exposes new volume factors and proxy labels', () => {
    const wr = POSITION_BENCHMARKS.WR.factors;
    const byId = Object.fromEntries(wr.map((f) => [f.id, f]));
    expect(byId.yards_per_catch?.category).toBe('volume');
    expect(byId.yac_per_reception?.category).toBe('volume');
    expect(byId.target_share?.category).toBe('volume');
    expect(byId.yprr.label.toLowerCase()).toContain('proxy');
    expect(byId.reception_perception.label.toLowerCase()).toMatch(/catch|ngs|proxy/i);
    expect(byId.yards_per_catch.benchmark).toBeGreaterThan(0);
    expect(byId.reception_perception.benchmark).not.toBe(90);
  });

  it('bumps WR known ceiling factors to 15', () => {
    // CEILING_RANGE.WR.max === 15 * green weight
    expect(CEILING_RANGE.WR.max).toBe(15 * 5);
    expect(CEILING_RANGE.WR.min).toBe(15 * -3);
  });
});
```

Adjust imports to match actual exports (`POSITION_BENCHMARKS` vs `BENCHMARKS` — use the same symbol existing tests import).

- [ ] **Step 2: Run test — expect FAIL**

Run: `npm run test -w @draftlab/evaluation-engine -- wr-yprr-catch-volume`  
Expected: FAIL (missing factors / WR still 10)

- [ ] **Step 3: Update `benchmarks.ts` WR block**

After `receptions` (volume section), insert (benchmarks = Task 4 means — replace placeholders with exact JSON values):

```typescript
{
  id: 'yards_per_catch',
  label: 'Yards per catch',
  category: 'volume',
  direction: 'higherBetter',
  benchmark: /* from benchmarks.json */,
},
{
  id: 'yac_per_reception',
  label: 'YAC per reception',
  category: 'volume',
  direction: 'higherBetter',
  benchmark: /* from benchmarks.json */,
},
{
  id: 'target_share',
  label: 'Target share',
  category: 'volume',
  direction: 'higherBetter',
  benchmark: /* from benchmarks.json */,
},
```

Update existing entries:

```typescript
{
  id: 'yprr',
  label: 'Yards per route run (proxy)',
  category: 'situational',
  direction: 'higherBetter',
  benchmark: /* from benchmarks.json */,
},
{
  id: 'reception_perception',
  label: 'Catch % (NGS proxy)',
  category: 'situational',
  direction: 'higherBetter',
  benchmark: /* from benchmarks.json — NOT 90 */,
},
```

- [ ] **Step 4: Bump `grade-weights.ts`**

```typescript
const CEILING_KNOWN_FACTORS: Record<Position, number> = {
  QB: 10,
  RB: 15,
  TE: 10,
  WR: 15,
};
```

Update the comment block to note WR 15/16 (only `ol_pass_block_rank` licensed).

- [ ] **Step 5: Run tests — expect PASS**

Run: `npm run test -w @draftlab/evaluation-engine -- wr-yprr-catch-volume`  
Expected: PASS

- [ ] **Step 6: Commit (DraftLab)**

```bash
git add packages/evaluation-engine/src/config/benchmarks.ts \
  packages/evaluation-engine/src/config/grade-weights.ts \
  packages/evaluation-engine/src/__tests__/wr-yprr-catch-volume.test.ts
git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -m "feat: WR YPRR/catch%/volume ceiling factors (ITEM-005)"
```

---

### Task 6: Publish R2 + deploy Worker + verify

**Files:** none (ops)

**Interfaces:**
- Consumes: sleeperMCP artifacts on `main`; DraftLab evaluation-engine on `main`
- Produces: Live Drake R2 artifacts + Worker serving WR known ≈ 15/16

- [ ] **Step 1: Open / merge PRs**

- sleeperMCP: branch with Tasks 1–4 → PR → merge to `main`
- DraftLab: branch with Task 5 → PR → merge to `main`

- [ ] **Step 2: Publish artifacts**

```bash
gh workflow run publish-artifacts --ref main --repo jarrod6424/sleeperMCP
gh run watch --repo jarrod6424/sleeperMCP
```

Expected: success

- [ ] **Step 3: Build packages + deploy Worker**

```bash
cd c:\Code\fantasy-football-draft-optimizer\fantasy-football-draft-optimizer
git pull
npm run build:packages
npm run deploy:worker
```

Expected: `Uploaded draftlab-api` success (build packages first if integrations exports missing)

- [ ] **Step 4: Live spot-check**

```bash
curl.exe -s "https://draftlab-api.drakedavisdev.workers.dev/api/players/amon-ra-st-brown" | python -c "..."
```

Assert: only `ol_pass_block_rank` unknown among ceiling factors; YPC / YAC / target_share / yprr / catch % graded; known ≈ 15/16.

- [ ] **Step 5: Mark ITEM-005 Done**

Update sleeperMCP `docs/PLAN.md` ITEM-005 → **Done** with deploy notes; commit on main or follow-up docs PR.

---

## Spec coverage self-check

| Spec requirement | Task |
|------------------|------|
| `yprr` = yards / on_pass; tag participation | Task 2 |
| `reception_perception` = NGS catch % 0–100 | Task 3 |
| `yards_per_catch`, `yac_per_reception`, `target_share` | Task 1 |
| Never fabricate 0; best-effort loaders | Tasks 1–3 |
| DraftLab labels, cohort means, WR 10→15 | Task 5 |
| Leave `ol_pass_block_rank` | Tasks 4–6 |
| R2 + Worker + ARSB spot-check | Task 6 |
| PLAN / SAD updates | Tasks 4, 6 |

## Placeholder scan

No TBD/TODO left in task steps; benchmark numeric placeholders in Task 5 are explicitly replaced from Task 4 `benchmarks.json` before coding that step.

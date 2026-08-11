# WR Ceiling Factors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill WR `qb_pff_rank` (ESPN QBR proxy), add WR `route_participation`, emit `secondary_target` categoricals, soft-cap ceiling injury `serious→concerned`, and bump WR known-factor coverage to 10.

**Architecture:** Extend sleeperMCP `build_benchmarks.py` / `build_factors.py` (reuse TE QBR + participation loaders). DraftLab adds WR `route_participation` to the ceiling factor list, soft-caps injury in ceiling grading, and bumps `CEILING_KNOWN_FACTORS.WR` 7→10. Publish via existing R2 Action.

**Tech Stack:** Python 3.12, pytest, nflverse CSV, TypeScript/Vitest (DraftLab), Cloudflare R2.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-11-wr-ceiling-factors-design.md`
- Never fabricate numeric `0` for ranks or route % on loader failure — leave unset
- Keep WR factor id `qb_pff_rank` (do not rename to `qb_qbr_rank`); source tag `nflverse:espn_qbr`
- `secondary_target` bands: `r = secondary_targets / player_targets` → `less` if `r < 0.75`, `same` if `0.75 <= r < 1.00`, `more` if `r >= 1.00`
- Injury artifact severity unchanged; ceiling grading only soft-caps `serious` → grade as `concerned`
- Out of scope: `yprr`, `ol_pass_block_rank`, `reception_perception`, TD/pass-volume band recalibration
- TDD: failing test first; commit after each green task
- Two repos: sleeperMCP (`c:\Code\sleeperMCP`) then DraftLab (`c:\Code\fantasy-football-draft-optimizer\fantasy-football-draft-optimizer`)

## File map

| File | Responsibility |
|------|----------------|
| `tools/build_benchmarks.py` | Generalize route loader; wire WR `qb_pff_rank` + `route_participation`; compute WR `secondary_target` / cat; `FACTORS` / `COMPUTABLE` / `FACTOR_KIND` |
| `tools/build_factors.py` | `TEAM_CONTEXT` += `qb_pff_rank`; emit `secondary_target` categorical |
| `tests/test_wr_route_participation.py` | WR route % (mocked) |
| `tests/test_wr_qb_pff_proxy.py` | WR QBR→`qb_pff_rank` (mocked) |
| `tests/test_secondary_target.py` | Secondary competition bands |
| `docs/PLAN.md`, `docs/SAD.md` | ITEM-003 status + coverage |
| DraftLab `packages/evaluation-engine/src/config/benchmarks.ts` | Add WR `route_participation`; refresh WR QBR/route means after rebuild |
| DraftLab `packages/evaluation-engine/src/config/grade-weights.ts` | `CEILING_KNOWN_FACTORS.WR` 7→10 |
| DraftLab `packages/evaluation-engine/src/grade-factor.ts` | Ceiling soft-cap serious→concerned |
| DraftLab `packages/evaluation-engine/src/__tests__/wr-ceiling-factors.test.ts` | Soft-cap + WR config |
| DraftLab `apps/api/src/data/__tests__/load-artifact.test.ts` | Assert `secondary_target` categorical retained |

---

### Task 1: Generalize route participation loader for WR

**Files:**
- Modify: `tools/build_benchmarks.py`
- Create: `tests/test_wr_route_participation.py`
- Modify: `tests/test_te_route_participation.py` (only if rename breaks imports — keep TE tests green via alias)

**Interfaces:**
- Consumes: existing `load_te_route_participation` / `_route_rates_from_events` patterns
- Produces: `load_route_participation(season: int, position: str) -> dict[str, float]`
  Keys = `_qb_name_key(display_name)`. Values = route participation percent.
  `position` in `{"TE","WR"}`. Empty `{}` on failure.
  Keep `load_te_route_participation(season) -> load_route_participation(season, "TE")` as a thin alias so TE call sites stay valid until updated.

- [ ] **Step 1: Write the failing test**

Create `tests/test_wr_route_participation.py` mirroring `tests/test_te_route_participation.py`: assert `load_route_participation(2024, "WR") == {}` on empty CSV; assert a WR on 2 of 3 team pass plays in active games grades ~66.7%. Copy the TE fixture structure; filter stats `position == "WR"`.

- [ ] **Step 2: Run test — expect FAIL**

Run: `python -m pytest tests/test_wr_route_participation.py -v`
Expected: FAIL (`load_route_participation` missing)

- [ ] **Step 3: Implement `load_route_participation`**

Refactor `load_te_route_participation` body into `load_route_participation(season, position)` filtering `stats_player` by `position in {TE,WR}`. Alias:

```python
def load_te_route_participation(season: int) -> dict[str, float]:
    return load_route_participation(season, "TE")
```

- [ ] **Step 4: Wire WR rates in `load_player_seasons`**

Loop `for pos in ("TE", "WR")` calling `load_route_participation(season, pos)` and writing `a["route_participation"]` for matching rows (same as current TE block).

- [ ] **Step 5: Update FACTORS**

In `FACTORS["WR"]`, after `team_pass_attempts`, insert `("route_participation", "nflverse:participation")`. `COMPUTABLE` already has `route_participation`.

- [ ] **Step 6: Run tests — expect PASS**

Run: `python -m pytest tests/test_wr_route_participation.py tests/test_te_route_participation.py -v`
Expected: PASS

- [ ] **Step 7: Commit (sleeperMCP)**

```bash
git add tools/build_benchmarks.py tests/test_wr_route_participation.py
git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -m "feat: WR route_participation via shared participation loader"
```

---

### Task 2: WR `qb_pff_rank` from team primary QBR

**Files:**
- Modify: `tools/build_benchmarks.py`
- Modify: `tools/build_factors.py` (`TEAM_CONTEXT`)
- Create: `tests/test_wr_qb_pff_proxy.py`

**Interfaces:**
- Consumes: `load_espn_qbr_season` + `primary_by_team` construction
- Produces: WR season rows with `qb_pff_rank: int` (same rank TE stores as `qb_qbr_rank`)
- Produces: helper `_attach_team_qbr_ranks(agg: dict, qbr: dict) -> None` (extract from existing QBR block)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wr_qb_pff_proxy.py
from __future__ import annotations
from unittest.mock import patch
import build_benchmarks as bb

def test_wr_gets_team_primary_qb_rank_as_qb_pff_rank() -> None:
    qbr = {
        bb._qb_name_key("S.Darnold"): {
            "rank": 5, "qb_plays": 500, "team": "SEA", "qbr": 60.0,
        },
    }
    agg = {
        "jsn": {"name": "Jaxon Smith-Njigba", "position": "WR", "team": "SEA"},
        "kittle": {"name": "George Kittle", "position": "TE", "team": "SF"},
    }
    bb._attach_team_qbr_ranks(agg, qbr)
    assert agg["jsn"]["qb_pff_rank"] == 5
    assert "qb_pff_rank" not in agg["kittle"]
    assert agg["kittle"].get("qb_qbr_rank") is None  # SF not in map
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `python -m pytest tests/test_wr_qb_pff_proxy.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Extract existing QB personal rank + TE `qb_qbr_rank` attach into `_attach_team_qbr_ranks`. Extend for WR:

```python
elif a["position"] == "WR":
    a["qb_pff_rank"] = primary_by_team[team]
```

Update `FACTORS["WR"]` `qb_pff_rank` source → `nflverse:espn_qbr`. Add `qb_pff_rank` to `COMPUTABLE` and `FACTOR_KIND` as `"rank"`. Add `qb_pff_rank` to `TEAM_CONTEXT` in `build_factors.py`.

- [ ] **Step 4: Run tests — expect PASS**

Run: `python -m pytest tests/test_wr_qb_pff_proxy.py tests/test_qbr_factors.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/build_benchmarks.py tools/build_factors.py tests/test_wr_qb_pff_proxy.py
git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -m "feat: WR qb_pff_rank from team primary ESPN QBR"
```

---

### Task 3: `secondary_target` categorical emission

**Files:**
- Modify: `tools/build_benchmarks.py`
- Modify: `tools/build_factors.py`
- Create: `tests/test_secondary_target.py`

**Interfaces:**
- Produces: `classify_secondary_target(player_targets: float, secondary_targets: float) -> str` → `less|same|more`
- Produces: `_attach_wr_secondary_targets(season_rows: list[dict]) -> None` setting `secondary_target` (season total) + `secondary_target_cat`
- Produces: artifact `{ value, categorical, provenance, note }` for WR `secondary_target`
- Attach **before** per-game conversion so `targets` are season totals

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_secondary_target.py
from __future__ import annotations
import build_benchmarks as bb

def test_secondary_bands() -> None:
    assert bb.classify_secondary_target(100, 74) == "less"
    assert bb.classify_secondary_target(100, 75) == "same"
    assert bb.classify_secondary_target(100, 99) == "same"
    assert bb.classify_secondary_target(100, 100) == "more"

def test_attach_secondary_on_team_wrs() -> None:
    rows = [
        {"name": "A.Alpha", "position": "WR", "team": "DET", "targets": 140},
        {"name": "B.Beta", "position": "WR", "team": "DET", "targets": 90},
        {"name": "C.Solo", "position": "WR", "team": "LV", "targets": 80},
    ]
    bb._attach_wr_secondary_targets(rows)
    alpha = next(r for r in rows if r["name"] == "A.Alpha")
    beta = next(r for r in rows if r["name"] == "B.Beta")
    solo = next(r for r in rows if r["name"] == "C.Solo")
    assert alpha["secondary_target"] == 90
    assert alpha["secondary_target_cat"] == "less"
    assert beta["secondary_target"] == 140
    assert beta["secondary_target_cat"] == "more"
    assert "secondary_target" not in solo
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `python -m pytest tests/test_secondary_target.py -v`
Expected: FAIL

- [ ] **Step 3: Implement classify + attach + emit**

```python
def classify_secondary_target(player_targets: float, secondary_targets: float) -> str:
    r = secondary_targets / player_targets
    if r < 0.75:
        return "less"
    if r < 1.00:
        return "same"
    return "more"
```

`_attach_wr_secondary_targets`: per team with ≥2 WRs, each WR gets max teammate season targets + cat. Call from `load_player_seasons` while targets are still season totals.

`FACTORS["WR"]`: `("secondary_target", "nflverse")`. Add `secondary_target` to `TEAM_CONTEXT` and `COMPUTABLE` (numeric season totals for cohort mean).

In `build_factors.py` factor loop, special-case `secondary_target` like injury: emit `value` + `categorical` from `hit["values"]`; `missing:no_secondary` when unset; honor `stale:team_changed` / `missing:no_team_context`.

- [ ] **Step 4: Run tests — expect PASS**

Run: `python -m pytest tests/test_secondary_target.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/build_benchmarks.py tools/build_factors.py tests/test_secondary_target.py
git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -m "feat: WR secondary_target less/same/more from teammate targets"
```

---

### Task 4: Rebuild artifacts + docs (sleeperMCP)

**Files:**
- Modify: `artifacts/benchmarks.json`, `artifacts/player_factors.json` (generated)
- Modify: `docs/PLAN.md`, `docs/SAD.md`

- [ ] **Step 1: Rebuild**

```bash
cd c:\Code\sleeperMCP
.venv\Scripts\Activate.ps1
python tools/build_benchmarks.py --spread
python tools/build_factors.py
python tools/check_artifact_count.py --new artifacts/player_factors.json
```

- [ ] **Step 2: Sanity-read numbers**

Print Chase / Nacua / JSN / St. Brown for `qb_pff_rank`, `route_participation`, `secondary_target`, `injury_concern`. Confirm measured (not unsourced); injury severity still truthful (`serious` allowed on artifact).

- [ ] **Step 3: Update PLAN.md / SAD.md**

Add ITEM-003; remove WR `qb_pff_rank` from licensed-only gap list; note WR+TE route participation sourced.

- [ ] **Step 4: Commit**

```bash
git add artifacts/benchmarks.json artifacts/player_factors.json docs/PLAN.md docs/SAD.md
git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -m "chore: regenerate artifacts for WR ceiling factors"
```

---

### Task 5: DraftLab — WR route factor, known=10, injury soft-cap

**Files:**
- Modify: `packages/evaluation-engine/src/config/benchmarks.ts`
- Modify: `packages/evaluation-engine/src/config/grade-weights.ts`
- Modify: `packages/evaluation-engine/src/grade-factor.ts`
- Create: `packages/evaluation-engine/src/__tests__/wr-ceiling-factors.test.ts`
- Modify: `apps/api/src/data/__tests__/load-artifact.test.ts`

**Interfaces:**
- Produces: `gradeInjuryConcern(level, options?: { softCapSerious?: boolean })` — when `softCapSerious` and level `serious`, return `concerned`
- `gradeFactor` passes `{ softCapSerious: true }` for injury (ceiling-only path)

- [ ] **Step 1: Write failing tests**

```typescript
import { describe, expect, it } from 'vitest';
import { gradeInjuryConcern } from '../grade-factor.js';
import { CEILING_RANGE } from '../config/grade-weights.js';
import { getBenchmarkConfig } from '../config/benchmarks.js';

describe('injury ceiling soft-cap', () => {
  it('grades serious as concerned when softCapSerious is true', () => {
    expect(gradeInjuryConcern('serious', { softCapSerious: true })).toBe('concerned');
    expect(gradeInjuryConcern('serious')).toBe('red');
  });
});

describe('WR ceiling config', () => {
  it('includes route_participation and known-factor range for 10', () => {
    const cfg = getBenchmarkConfig('WR', 2025);
    expect(cfg.factors.some((f) => f.id === 'route_participation')).toBe(true);
    expect(CEILING_RANGE.WR.max).toBe(50);
    expect(CEILING_RANGE.WR.min).toBe(-30);
  });
});
```

Also assert load-artifact retains `secondary_target` categorical `less`.

- [ ] **Step 2: Run — expect FAIL**

```bash
cd c:\Code\fantasy-football-draft-optimizer\fantasy-football-draft-optimizer\packages\evaluation-engine
npx vitest run src/__tests__/wr-ceiling-factors.test.ts
```

- [ ] **Step 3: Implement**

- `CEILING_KNOWN_FACTORS.WR = 10`; comment WR 10/13
- Soft-cap in `gradeInjuryConcern` + `gradeFactor` injury branch
- Insert WR `route_participation` after `team_pass_attempts` with cohort mean from rebuilt `artifacts/benchmarks.json`
- Update `qb_pff_rank.benchmark` from same artifact; optional label `QB QBR rank (proxy)`

- [ ] **Step 4: Run tests — expect PASS**

```bash
npx vitest run src/__tests__/wr-ceiling-factors.test.ts
# from apps/api or monorepo root:
npx vitest run src/data/__tests__/load-artifact.test.ts
```

- [ ] **Step 5: Commit (DraftLab)**

```bash
git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -m "feat: WR route factor, known=10, ceiling injury soft-cap"
```

---

### Task 6: Publish R2 + spot-check board scores

- [ ] **Step 1:** Publish sleeperMCP artifacts to Drake R2 (`publish-artifacts` workflow).
- [ ] **Step 2:** Deploy DraftLab Worker if evaluation-engine is bundled there.
- [ ] **Step 3:** Spot-check Chase/Nacua/ARSB/JSN vs Taylor/Kyren/Kittle/Kelce — new factors graded; Nacua injury ceiling −1 not −3; WRs clearly above those RBs/TEs.
- [ ] **Step 4:** Mark ITEM-003 Done; open/merge PRs both repos.

---

## Spec coverage (self-review)

| Spec requirement | Task |
|------------------|------|
| WR `qb_pff_rank` QBR proxy | Task 2 |
| WR `route_participation` | Task 1 |
| `secondary_target` less/same/more | Task 3 |
| TEAM_CONTEXT / trade provenance | Tasks 2–3 |
| Artifact rebuild + docs | Task 4 |
| DraftLab WR route + CEILING_KNOWN 7→10 | Task 5 |
| Injury serious→concerned ceiling only | Task 5 |
| R2 + spot-check | Task 6 |
| Non-goals (yprr/OL/RP/TD bands) | Not tasked |

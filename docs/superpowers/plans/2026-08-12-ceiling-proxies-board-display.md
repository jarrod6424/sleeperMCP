# Ceiling Proxies + Board Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Proxy QB pass EPA and TE YPRR, delete TE `inline_pct`, then fix the player board (raw ceiling + top-5 green, SCORE column, summary/header hovers, position-accurate CONF).

**Architecture:** sleeperMCP measures new/changed factors into artifacts → DraftLab catalog + `CEILING_KNOWN_FACTORS` consume them → Angular board/detail display uses evaluation payload only (no second scoring formula). Continue on existing worktrees: DraftLab `.worktrees/archetype-top5-ladder`, sleeperMCP `.worktrees/archetype-top5-ladder` (or stacked feature branches if those already merged).

**Tech Stack:** Python/pytest (sleeperMCP builders), TypeScript/Vitest (evaluation-engine), Angular (web board), Cloudflare R2 + Worker/web deploy.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-12-drop-licensed-factors-te-yprr-design.md`
- QB id: `pass_epa_rank` (retire `pass_dvoa_rank`); label includes `(proxy)`
- TE: `yprr` rate proxy (retire `yprr_rank`); delete `inline_pct`
- Never fabricate `0` / midpoint ranks on loader failure — leave unset
- Ceiling UI: raw only, no `/60`; green = top 5 at position (ties included; provisional excluded)
- CONF denom = `ceiling.factors.length` (fallback position map); header `FACTORS` not `12 FACTORS`
- DraftScore math / `CEILING_RANGE` ±5n unchanged except known-count bumps QB 11→12, TE 12→13
- Rookie blank-slate dampening out of scope

## File map

| Area | Files |
|------|--------|
| sleeperMCP builders | `tools/build_benchmarks.py`, `tools/build_factors.py` |
| sleeperMCP tests | `tests/test_pass_epa_rank.py`, `tests/test_te_yprr_proxy.py` (create) |
| Artifacts | `artifacts/player_factors.json`, `artifacts/benchmarks.json` |
| DraftLab catalog | `packages/evaluation-engine/src/config/benchmarks.ts`, `grade-weights.ts` |
| Engine helpers | `packages/evaluation-engine/src/archetype.ts` (`explainArchetype`) |
| Engine tests | `packages/evaluation-engine/src/__tests__/…` |
| Seeds / detail | `apps/api/src/data/seed-*.ts`, `apps/web/.../player-detail.*` |
| Board UI | `apps/web/.../board.component.ts`, `.css`; optional `board-tooltips.ts` |
| Web types | `apps/web/src/app/core/api.types.ts` |
| Bootstrap | `apps/api/data/player_factors.json`, `benchmarks.json` |
| Docs | `docs/01-player-evaluation-model.md`, sleeperMCP `docs/SAD.md` |

---

### Task 1: sleeperMCP — `pass_epa_rank` loader

**Files:**
- Modify: `tools/build_benchmarks.py`
- Create: `tests/test_pass_epa_rank.py`
- Modify: `tools/build_factors.py` only if factor allow-lists duplicate FACTORS (usually inherits)

**Interfaces:**
- Produces: `pass_epa_mean_from_rows(rows) -> dict[str, float]`, `load_pass_epa_ranks(season) -> dict[str, int]` (team → rank, 1 = best)
- Consumes: existing `nflverse_csv`, `to_nflverse_team`, `rank_teams_*` patterns from OL proxy

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pass_epa_rank.py
from tools import build_benchmarks as bb

def test_pass_epa_rank_orders_higher_epa_first():
    rows = [
        {"posteam": "KC", "pass": 1, "epa": 0.4, "season_type": "REG", "play_type": "pass"},
        {"posteam": "KC", "pass": 1, "epa": 0.2, "season_type": "REG", "play_type": "pass"},
        {"posteam": "CHI", "pass": 1, "epa": -0.1, "season_type": "REG", "play_type": "pass"},
    ]
    means = bb.pass_epa_mean_from_rows(rows)
    assert means["KC"] > means["CHI"]
    ranks = bb.rank_teams_descending(means)  # or dedicated helper
    assert ranks["KC"] == 1
    assert ranks["CHI"] == 2

def test_qb_factors_use_pass_epa_not_dvoa():
    ids = [fid for fid, _ in bb.FACTORS["QB"]]
    assert "pass_epa_rank" in ids
    assert "pass_dvoa_rank" not in ids
    assert dict(bb.FACTORS["QB"])["pass_epa_rank"].startswith("nflverse")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pass_epa_rank.py -v`  
Expected: FAIL (missing helpers / still `pass_dvoa_rank`)

- [ ] **Step 3: Implement**

In `build_benchmarks.py`:
1. Replace QB tuple `("pass_dvoa_rank", "licensed:FTN")` with `("pass_epa_rank", "nflverse:pbp:proxy")`.
2. Add `pass_epa_rank` to `COMPUTABLE` and `FACTOR_KIND` as `"rank"`.
3. Add:

```python
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
    rows = nflverse_csv("pbp", f"play_by_play_{season}.csv", row_filter=keep, ttl=STATS_CACHE_TTL)
    if not rows:
        return {}
    return rank_teams_descending(pass_epa_mean_from_rows(rows))
```

4. In `load_player_seasons`, after OL proxy block, attach ranks to QBs:

```python
epa_ranks = load_pass_epa_ranks(season)
if epa_ranks:
    for a in agg.values():
        if a["position"] == "QB" and a.get("team") in epa_ranks:
            a["pass_epa_rank"] = epa_ranks[a["team"]]
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_pass_epa_rank.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/build_benchmarks.py tests/test_pass_epa_rank.py
git commit -m "feat: proxy QB pass_epa_rank from nflverse EPA"
```

---

### Task 2: sleeperMCP — TE `yprr` + drop `inline_pct` / `yprr_rank`

**Files:**
- Modify: `tools/build_benchmarks.py` (FACTORS TE list + participation loop)
- Create: `tests/test_te_yprr_proxy.py`
- Modify: `docs/SAD.md` licensed blocked list

**Interfaces:**
- Consumes: `compute_yprr`, `load_route_details`
- Produces: TE `yprr` measured like WR; no `inline_pct` / `yprr_rank` in FACTORS

- [ ] **Step 1: Write the failing test**

```python
def test_te_factors_drop_licensed_and_use_yprr():
    ids = [fid for fid, _ in bb.FACTORS["TE"]]
    assert "yprr" in ids
    assert "yprr_rank" not in ids
    assert "inline_pct" not in ids
    assert dict(bb.FACTORS["TE"])["yprr"] == "nflverse:participation"

def test_participation_loop_sets_te_yprr(monkeypatch):
    # minimal: call compute_yprr path — assert compute_yprr(900, 300) == 3.0
    assert bb.compute_yprr(900.0, 300) == 3.0
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_te_yprr_proxy.py -v`  
Expected: FAIL on FACTORS assertions

- [ ] **Step 3: Implement**

1. TE FACTORS: remove `inline_pct` and `yprr_rank` lines; add `("yprr", "nflverse:participation")` near route participation / before injury.
2. In participation loop, change:

```python
if pos in ("WR", "TE"):
    yprr = compute_yprr(a.get("receiving_yards") or 0.0, int(d.get("on_pass") or 0))
    if yprr is not None:
        a["yprr"] = yprr
        yprr_matched += 1
```

(remove `pos == "WR"` gate; log yprr_matched for both).

3. Update SAD: remove QB DVOA + TE PFF from blocked; note proxies.

- [ ] **Step 4: Tests pass + commit**

```bash
python -m pytest tests/test_te_yprr_proxy.py tests/test_pass_epa_rank.py -v
git add tools/build_benchmarks.py tests/test_te_yprr_proxy.py docs/SAD.md
git commit -m "feat: TE yprr proxy; drop inline_pct and yprr_rank"
```

---

### Task 3: Regenerate sleeperMCP artifacts

**Files:**
- Modify: `artifacts/player_factors.json`, `artifacts/benchmarks.json`

- [ ] **Step 1: Rebuild**

```bash
python tools/build_benchmarks.py
python tools/build_factors.py
python tools/check_artifact_count.py --new artifacts/player_factors.json
```

- [ ] **Step 2: Sanity**

```bash
python -c "import json; d=json.load(open('artifacts/player_factors.json'));
from collections import Counter
c=Counter();
[c.update({k:1 for k,f in p['factors'].items() if f.get('provenance')=='unsourced' and 'redistributable' in (f.get('note') or '')}) for p in d['players']];
print(c); 
qa=[p for p in d['players'] if p['name']=='Josh Allen'][0];
print('allen epa', qa['factors'].get('pass_epa_rank')); print('allen dvoa', qa['factors'].get('pass_dvoa_rank'))"
```

Expected: no FTN/PFF redistributable notes; Allen has `pass_epa_rank`, no `pass_dvoa_rank`.

- [ ] **Step 3: Commit artifacts**

```bash
git add artifacts/player_factors.json artifacts/benchmarks.json
git commit -m "chore: regenerate artifacts for pass_epa and TE yprr"
```

---

### Task 4: DraftLab catalog + known counts + seeds

**Files:**
- Modify: `packages/evaluation-engine/src/config/benchmarks.ts`
- Modify: `packages/evaluation-engine/src/config/grade-weights.ts` (`CEILING_KNOWN_FACTORS` QB 12, TE 13)
- Modify: `apps/api/src/data/seed-players.ts`, `seed-depth.ts`
- Modify: spot-check / load-artifact tests that reference old ids
- Copy: sleeperMCP artifacts → `apps/api/data/player_factors.json` + `benchmarks.json`

**Interfaces:**
- QB factor `pass_epa_rank` label `Pass EPA rank (proxy)`, `lowerBetter`, category situational
- TE: remove `inline_pct` / `yprr_rank`; add `yprr` like WR (higherBetter, proxy label)

- [ ] **Step 1: Failing test**

```typescript
// packages/evaluation-engine/src/__tests__/ceiling-catalog.test.ts
import { getBenchmarkConfig } from '../config/benchmarks.js';
import { CEILING_RANGE } from '../config/grade-weights.js';

it('QB catalog uses pass_epa_rank and 12 known slots', () => {
  const ids = getBenchmarkConfig('QB').factors.map((f) => f.id);
  expect(ids).toContain('pass_epa_rank');
  expect(ids).not.toContain('pass_dvoa_rank');
  expect(ids).toHaveLength(12);
  expect(CEILING_RANGE.QB.max).toBe(12 * 5);
});

it('TE catalog uses yprr and drops licensed ids', () => {
  const ids = getBenchmarkConfig('TE').factors.map((f) => f.id);
  expect(ids).toContain('yprr');
  expect(ids).not.toContain('yprr_rank');
  expect(ids).not.toContain('inline_pct');
  expect(ids).toHaveLength(13);
  expect(CEILING_RANGE.TE.max).toBe(13 * 5);
});
```

- [ ] **Step 2: Run — expect FAIL**

Run: `npm run test -w @draftlab/evaluation-engine -- ceiling-catalog`

- [ ] **Step 3: Edit benchmarks.ts + grade-weights.ts + seeds** (swap ids; seed `pass_dvoa_rank` → `pass_epa_rank`; TE inline/yprr_rank → `yprr` with rate-like values)

- [ ] **Step 4: Copy bootstrap JSON from sleeperMCP worktree artifacts**

- [ ] **Step 5: Tests pass + commit**

```bash
npm run test -w @draftlab/evaluation-engine -- ceiling-catalog spot-checks
git add packages/evaluation-engine apps/api/data apps/api/src/data
git commit -m "feat: DraftLab catalog pass_epa_rank and TE yprr"
```

---

### Task 5: `explainArchetype` helper

**Files:**
- Modify: `packages/evaluation-engine/src/archetype.ts`
- Create/modify: `packages/evaluation-engine/src/__tests__/explain-archetype.test.ts`
- Export from package index if needed

**Interfaces:**
- Produces: `explainArchetype(player: Player): string` — one short “Why: …” line matching half-rate rules

- [ ] **Step 1: Failing test**

```typescript
it('explains elite via top-8 half-rate', () => {
  const text = explainArchetype(
    p({ position: 'QB', seasonsInLeague: 8, positionalTop8FinishCount: 7, positionalTop12FinishCount: 7 }),
  );
  expect(text.toLowerCase()).toMatch(/top-8|rule 4|over half/);
});
```

- [ ] **Step 2: Implement** — mirror `classifySkillPosition` / `classifyQb` branches into human phrases (include age/year for veteran)

- [ ] **Step 3: Pass + commit**

```bash
git commit -m "feat: explainArchetype for board tooltips"
```

---

### Task 6: Board — raw ceiling, top-5 green, CONF denom

**Files:**
- Modify: `apps/web/src/app/features/board/board.component.ts`
- Modify: `apps/web/src/app/features/board/board.component.css` (if needed)
- Modify: `apps/web/src/app/features/player-detail/player-detail.component.ts` + `.html` (remove `/60`, `>= 30`; remove inline TE check)

**Interfaces:**
- Produces: `configuredFactorCount(row)`, `top5CeilingIdsByPosition(rows)`, `isTop5Ceiling(row)`

- [ ] **Step 1: Replace hardcoded slots**

```typescript
const POSITION_CATALOG_COUNT: Record<Position, number> = {
  QB: 12,
  RB: 16,
  TE: 13,
  WR: 17,
};

function configuredFactorCount(row: BoardPlayer): number {
  const n = row.evaluation.ceiling.factors?.length ?? 0;
  return n > 0 ? n : POSITION_CATALOG_COUNT[row.player.position];
}

function top5CeilingPlayerIds(rows: BoardPlayer[]): Set<string> {
  const byPos = new Map<Position, BoardPlayer[]>();
  for (const r of rows) {
    const c = r.evaluation.ceiling;
    if (c.provisional || c.ceilingScore == null) continue;
    const list = byPos.get(r.player.position) ?? [];
    list.push(r);
    byPos.set(r.player.position, list);
  }
  const ids = new Set<string>();
  for (const list of byPos.values()) {
    list.sort((a, b) => (b.evaluation.ceiling.ceilingScore ?? 0) - (a.evaluation.ceiling.ceilingScore ?? 0));
    if (list.length === 0) continue;
    const cutoff = list[Math.min(4, list.length - 1)].evaluation.ceiling.ceilingScore!;
    for (const r of list) {
      if ((r.evaluation.ceiling.ceilingScore ?? -Infinity) >= cutoff) ids.add(r.player.id);
    }
  }
  return ids;
}
```

- [ ] **Step 2: Template**
  - Remove `/60` spans
  - `[class.good]="top5Ids().has(row.player.id)"` using a computed from full `rows()` (not filtered-only)
  - CONF: `knownFactors / configuredFactorCount(row)`
  - Header: `FACTORS` not `12 FACTORS`
  - `factorGrades(row)` pads to `configuredFactorCount(row)`

- [ ] **Step 3: Player detail** — same raw ceiling + top-5 if board list available; delete `inline_pct` TE check block

- [ ] **Step 4: Manual/visual or component test if present; commit**

```bash
git commit -m "fix: position-accurate ceiling display and CONF denom"
```

---

### Task 7: Board — SCORE column

**Files:**
- Modify: `board.component.ts` template + CSS grid (`c-score` after ADP)
- Grid was ~12 tracks; insert ~44px column after ADP

- [ ] **Step 1: Add header + cell**

```html
<span class="c-score" [title]="headerPurpose.SCORE">SCORE</span>
<!-- row -->
<span class="c-score mono" [attr.title]="scoreTooltip(row)" tabindex="0">{{ scoreLabel(row) }}</span>
```

```typescript
scoreLabel(row: BoardPlayer): string {
  const s = row.recommendation?.contextualScore ?? row.evaluation.draftScore;
  return String(Math.round(s));
}
```

- [ ] **Step 2: Update `grid-template-columns` in `.col-head` / `.row` / responsive rules to include the new track**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: show DraftScore column on player board"
```

---

### Task 8: Summary cell hovers + header purpose blurbs

**Files:**
- Create: `apps/web/src/app/features/board/board-tooltips.ts` (pure string builders — easy to unit test)
- Modify: `board.component.ts`
- Modify: `apps/web/src/app/core/api.types.ts` — add `weights?`, ensure `rates?` on archetype
- Verify API already returns full `PlayerEvaluation` (includes `weights`); if stripped in a DTO, stop stripping

**Interfaces:**
- `buildScoreTooltip(row): string`
- `buildCeilingTooltip(row, isTop5: boolean): string`
- `buildArchetypeTooltip(row, why: string): string`
- `BOARD_HEADER_PURPOSE: Record<string, string>` from spec §7

- [ ] **Step 1: Unit tests for tooltip builders** (Vitest in web or a tiny shared util under `apps/web/src/app/features/board/`)

```typescript
it('score tooltip lists four weighted parts', () => {
  const text = buildScoreTooltip(fakeRow);
  expect(text).toMatch(/Ceiling/);
  expect(text).toMatch(/0\.40|40%/);
});
```

For score parts, reimplement the four normalizers locally (copy formulas from `draft-score.ts`) or import if the web package can depend on evaluation-engine — **prefer duplicating the four one-liners in `board-tooltips.ts` with a comment pointing at the engine** if workspace import is awkward; keep numbers identical:

```typescript
// Mirror packages/evaluation-engine/src/draft-score.ts
function normCeiling(score: number | null, pos: Position): number { ... }
```

Use `POSITION_CATALOG_COUNT` × ±5 for range (must match `CEILING_KNOWN` after Task 4).

- [ ] **Step 2: Wire cell `[title]` or a small CSS hover panel** showing multiline (`white-space: pre-line`). Prefer a `title` with `\n` only if insufficient — use `.tip` absolute panel on `:hover`/`:focus-within` for SCORE/CEILING/ARCH.

- [ ] **Step 3: Header purposes** — remove `aria-hidden="true"` from `col-head`; set `title` per header from `BOARD_HEADER_PURPOSE`.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: board score/ceiling/archetype and header tooltips"
```

---

### Task 9: Docs + R2 + deploy

**Files:**
- Modify: `docs/01-player-evaluation-model.md` (§1.2 DVOA → pass EPA; §1.4 drop inline / YPRR rank → yprr proxy)
- sleeperMCP SAD already in Task 2

- [ ] **Step 1: Doc edits + commit**

```bash
git commit -m "docs: pass EPA proxy and TE yprr; board display notes"
```

- [ ] **Step 2: Publish + deploy (Drake account env)**

```powershell
$env:NODE_OPTIONS = "--use-system-ca"
$env:CLOUDFLARE_ACCOUNT_ID = "247649a81d4e45d2f6dc4fe1ea615e75"
# from DraftLab apps/worker:
npm run wrangler -- r2 object put draftlab-artifacts/artifacts/player_factors.json --file=<sleeperMCP artifacts path> --content-type=application/json --remote
npm run wrangler -- r2 object put draftlab-artifacts/artifacts/benchmarks.json --file=<benchmarks path> --content-type=application/json --remote
npm run deploy
npm run deploy -w @draftlab/web
```

- [ ] **Step 3: Acceptance smoke**
  - Allen: `pass_epa_rank` graded; no DVOA row; CONF `/12`
  - Bowers/McBride: `yprr` present; no inline/yprr_rank; CONF `/13`
  - Board: no `/60`; SCORE column visible; header + cell hovers work; WR CONF `/17`

---

## Spec coverage checklist

| Spec section | Task |
|--------------|------|
| §1 pass_epa_rank | 1, 3, 4 |
| §2 TE yprr | 2, 3, 4 |
| §3 delete inline_pct | 2, 4, 6 |
| §4 ceiling display | 6 |
| §5 SCORE column | 7 |
| §6 cell hovers | 5, 8 |
| §7 header hovers | 8 |
| §8 CONF denom | 6 |
| Docs / R2 / deploy | 9 |
| CEILING_KNOWN bumps | 4 |

## Placeholder / consistency self-review

- No TBDs left in steps.
- Ids consistent: `pass_epa_rank`, `yprr`, deleted `pass_dvoa_rank` / `inline_pct` / `yprr_rank`.
- Catalog counts QB12 / TE13 / RB16 / WR17 aligned across Tasks 4, 6, 8.

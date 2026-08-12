# OL Proxy, Shared Factors, and QB ADP Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill `ol_pass_block_rank` / `ol_run_block_rank` via nflverse pbp proxies, share `neutral_pace_rank` with WR/TE and `injury_concern` on DraftLab QB, remove ADP from QB ceiling factors, and bump known-factor coverage to QB 11/12, WR 17/17, TE 12/14, RB 16/16.

**Architecture:** Add `load_ol_proxy_season` in sleeperMCP `build_benchmarks.py` (pressure-rate and stuff-rate team ranks), attach ranks like other team context, extend pace attach to WR/TE, drop QB `adp` from `FACTORS`. DraftLab updates factor lists / labels / `CEILING_KNOWN_FACTORS`, removes `excludeAdp`. Regenerate artifacts → R2 → Worker redeploy.

**Tech Stack:** Python 3.12, pytest, nflverse pbp CSV, TypeScript/Vitest (DraftLab), Cloudflare R2 / Wrangler.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-11-ol-proxy-shared-factors-design.md`
- Keep factor ids `ol_pass_block_rank` / `ol_run_block_rank`; honesty via labels + `nflverse:pbp:proxy`
- Never fabricate numeric `0` for OL ranks / pace on loader failure — leave unset
- Benchmarks: top-3 (default `--cohort 3`) by fantasy points per position — all positions
- Out of scope: `team_wins` on WR/TE, pass-attempt shape unify, PFF/FTN license, TE `inline_pct` / `yprr_rank`, opponent-adjusted rates
- ValueScore / `get_adp` / player `adp_round_pick` market fields stay
- TDD: failing test first; commit after each green task
- Two repos: sleeperMCP (`c:\Code\sleeperMCP`) then DraftLab (`c:\Code\fantasy-football-draft-optimizer\fantasy-football-draft-optimizer`)

## File map

| File | Responsibility |
|------|----------------|
| `tools/build_benchmarks.py` | `load_ol_proxy_season` / rank helpers; FACTORS / COMPUTABLE / FACTOR_KIND; attach OL + WR/TE pace; drop QB `adp` |
| `tools/build_factors.py` | `TEAM_CONTEXT` add `ol_pass_block_rank`, `ol_run_block_rank` |
| `tests/test_ol_proxy.py` | Pressure / stuff rate ranks from fixture rows |
| `tests/test_pace_attach_receivers.py` | WR/TE get `neutral_pace_rank` from team map |
| `docs/PLAN.md`, `docs/SAD.md` | ITEM-006 + licensed gap list |
| DraftLab `packages/evaluation-engine/src/config/benchmarks.ts` | Factor list / labels / cohort means |
| DraftLab `packages/evaluation-engine/src/config/grade-weights.ts` | `CEILING_KNOWN_FACTORS` |
| DraftLab `packages/evaluation-engine/src/ceiling.ts` | Remove `excludeAdp` |
| DraftLab tests / `seed-players.ts` / `seed-depth.ts` | Drop QB ceiling `adp` inputs; add injury / pace as needed |

---

### Task 1: OL proxy loader (pressure + stuff ranks)

**Files:**
- Modify: `tools/build_benchmarks.py`
- Create: `tests/test_ol_proxy.py`

**Interfaces:**
- Consumes: `nflverse_csv("pbp", ...)`, `to_nflverse_team`, `safe_float`
- Produces:
  - `load_ol_proxy_season(season: int) -> tuple[dict[str, int], dict[str, int]]`
    — `(ol_pass_block_rank_by_team, ol_run_block_rank_by_team)`; empty dicts on failure
  - `rank_teams_ascending(rate_by_team: dict[str, float]) -> dict[str, int]`
    — lowest rate → rank 1; ties broken by team abbr sort for determinism
  - Pure helpers usable from tests without network:
    - `pressure_rates_from_rows(rows: list[dict]) -> dict[str, float]`
    - `stuff_rates_from_rows(rows: list[dict]) -> dict[str, float]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ol_proxy.py`:

```python
from __future__ import annotations

import build_benchmarks as bb


def test_pressure_rate_and_rank() -> None:
    rows = [
        # team A: 1 sack on 2 dropbacks → pressure_rate 0.5
        {"posteam": "DET", "pass_attempt": "1", "sack": "0", "qb_hit": "0", "qb_scramble": "0"},
        {"posteam": "DET", "pass_attempt": "0", "sack": "1", "qb_hit": "0", "qb_scramble": "0"},
        # team B: clean 2 dropbacks → 0.0
        {"posteam": "KC", "pass_attempt": "1", "sack": "0", "qb_hit": "0", "qb_scramble": "0"},
        {"posteam": "KC", "pass_attempt": "1", "sack": "0", "qb_hit": "0", "qb_scramble": "0"},
    ]
    rates = bb.pressure_rates_from_rows(rows)
    assert abs(rates["DET"] - 0.5) < 1e-9
    assert abs(rates["KC"] - 0.0) < 1e-9
    ranks = bb.rank_teams_ascending(rates)
    assert ranks["KC"] == 1
    assert ranks["DET"] == 2


def test_stuff_rate_and_rank() -> None:
    rows = [
        {"posteam": "PHI", "rush_attempt": "1", "rushing_yards": "-1"},
        {"posteam": "PHI", "rush_attempt": "1", "rushing_yards": "5"},
        {"posteam": "SF", "rush_attempt": "1", "rushing_yards": "4"},
        {"posteam": "SF", "rush_attempt": "1", "rushing_yards": "3"},
    ]
    rates = bb.stuff_rates_from_rows(rows)
    assert abs(rates["PHI"] - 0.5) < 1e-9
    assert abs(rates["SF"] - 0.0) < 1e-9
    ranks = bb.rank_teams_ascending(rates)
    assert ranks["SF"] == 1
    assert ranks["PHI"] == 2


def test_dropback_includes_sack_without_pass_attempt() -> None:
    rows = [
        {"posteam": "CHI", "pass_attempt": "0", "sack": "1", "qb_hit": "0", "qb_scramble": "0"},
    ]
    rates = bb.pressure_rates_from_rows(rows)
    assert abs(rates["CHI"] - 1.0) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:\Code\sleeperMCP && .venv\Scripts\python -m pytest tests/test_ol_proxy.py -v`

Expected: FAIL (functions missing)

- [ ] **Step 3: Write minimal implementation**

In `tools/build_benchmarks.py`, after `neutral_pace_ranks`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:\Code\sleeperMCP && .venv\Scripts\python -m pytest tests/test_ol_proxy.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd c:\Code\sleeperMCP
git add tools/build_benchmarks.py tests/test_ol_proxy.py
git commit -m "$(cat <<'EOF'
feat: add nflverse pbp OL pass/run block rank proxies

EOF
)"
```

---

### Task 2: Wire OL + WR/TE pace; drop QB ceiling ADP

**Files:**
- Modify: `tools/build_benchmarks.py` (`FACTORS`, `COMPUTABLE`, `FACTOR_KIND`, `load_player_seasons` attach block)
- Modify: `tools/build_factors.py` (`TEAM_CONTEXT`)
- Create: `tests/test_pace_attach_receivers.py`

**Interfaces:**
- Consumes: `load_ol_proxy_season`, `load_qb_pbp_season` / `neutral_pace_ranks`
- Produces: season agg rows with `ol_pass_block_rank` / `ol_run_block_rank` / `neutral_pace_rank` on the right positions; `FACTORS["QB"]` without `adp`; TE includes `ol_pass_block_rank`; WR/TE include `neutral_pace_rank`; all three ids in `COMPUTABLE`; `FACTOR_KIND` rank entries for OL ids; `TEAM_CONTEXT` includes both OL ids

- [ ] **Step 1: Write the failing test**

Create `tests/test_pace_attach_receivers.py`:

```python
from __future__ import annotations

import build_benchmarks as bb


def test_qb_factors_exclude_adp_include_injury() -> None:
    ids = [fid for fid, _ in bb.FACTORS["QB"]]
    assert "adp" not in ids
    assert "injury_concern" in ids
    assert "ol_pass_block_rank" in ids


def test_wr_te_have_pace_and_te_has_ol_pass() -> None:
    wr = dict(bb.FACTORS["WR"])
    te = dict(bb.FACTORS["TE"])
    assert wr.get("neutral_pace_rank") == "nflverse:pbp"
    assert te.get("neutral_pace_rank") == "nflverse:pbp"
    assert te.get("ol_pass_block_rank") == "nflverse:pbp:proxy"
    assert wr.get("ol_pass_block_rank") == "nflverse:pbp:proxy"
    assert dict(bb.FACTORS["RB"]).get("ol_run_block_rank") == "nflverse:pbp:proxy"


def test_ol_and_pace_computable() -> None:
    assert "ol_pass_block_rank" in bb.COMPUTABLE
    assert "ol_run_block_rank" in bb.COMPUTABLE
    assert "neutral_pace_rank" in bb.COMPUTABLE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:\Code\sleeperMCP && .venv\Scripts\python -m pytest tests/test_pace_attach_receivers.py -v`

Expected: FAIL (source tags still `licensed:PFF` / missing keys)

- [ ] **Step 3: Update FACTORS / COMPUTABLE / FACTOR_KIND / TEAM_CONTEXT**

In `build_benchmarks.py` `FACTORS`:

```python
"QB": [
    ("pass_attempts", "nflverse"), ("passing_tds", "nflverse"),
    ("rush_attempts", "nflverse"), ("rushing_tds", "nflverse"),
    ("off_ppg_rank", "nflverse"), ("ol_pass_block_rank", "nflverse:pbp:proxy"),
    ("deep_ball_attempts", "nflverse:pbp"), ("qbr_rank", "nflverse:espn_qbr"),
    ("red_zone_attempts", "nflverse:pbp"),
    ("neutral_pace_rank", "nflverse:pbp"), ("pass_dvoa_rank", "licensed:FTN"),
    ("injury_concern", "nflverse:injuries"),
],
"RB": [
    # ... unchanged except:
    ("ol_run_block_rank", "nflverse:pbp:proxy"),
    # ...
],
"WR": [
    # ... after secondary_target / before or replacing ol source:
    ("ol_pass_block_rank", "nflverse:pbp:proxy"),
    # add:
    ("neutral_pace_rank", "nflverse:pbp"),
    # ...
],
"TE": [
    # ... after route_participation:
    ("ol_pass_block_rank", "nflverse:pbp:proxy"),
    ("neutral_pace_rank", "nflverse:pbp"),
    ("inline_pct", "licensed:PFF"),
    ("yprr_rank", "licensed:PFF"),
    ("injury_concern", "nflverse:injuries"),
],
```

Add to `COMPUTABLE`: `"ol_pass_block_rank", "ol_run_block_rank"`  
(`neutral_pace_rank` already present)

Add to `FACTOR_KIND`:
```python
"ol_pass_block_rank": "rank",
"ol_run_block_rank": "rank",
```

In `build_factors.py` `TEAM_CONTEXT`, add `"ol_pass_block_rank", "ol_run_block_rank"`.

- [ ] **Step 4: Attach in `load_player_seasons`**

After the existing QB pbp block (which already sets QB `neutral_pace_rank`), extend so WR/TE also get pace when `team_neutral` is non-empty:

```python
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
```

Note: `per_game` already passthroughs `FACTOR_KIND` keys present on `ps`, so ranks flow into cohort means automatically once on the season row.

- [ ] **Step 5: Run tests**

Run: `cd c:\Code\sleeperMCP && .venv\Scripts\python -m pytest tests/test_ol_proxy.py tests/test_pace_attach_receivers.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd c:\Code\sleeperMCP
git add tools/build_benchmarks.py tools/build_factors.py tests/test_pace_attach_receivers.py
git commit -m "$(cat <<'EOF'
feat: wire OL proxies and WR/TE pace; drop QB ceiling ADP

EOF
)"
```

---

### Task 3: Regenerate artifacts + sleeperMCP docs

**Files:**
- Modify: `artifacts/benchmarks.json`, `artifacts/player_factors.json` (generated)
- Modify: `docs/SAD.md`, `docs/PLAN.md`

**Interfaces:**
- Consumes: Task 2 pipeline
- Produces: cohort means for OL ranks + WR/TE pace; players with measured OL / pace; PLAN ITEM-006 → **In Review** here, **Done** after Worker (Task 6)

- [ ] **Step 1: Rebuild**

Run (long; pbp heavy):

```bash
cd c:\Code\sleeperMCP
.venv\Scripts\python tools/build_benchmarks.py
.venv\Scripts\python tools/build_factors.py
```

Expected: stderr shows `ol proxy 20XX: pass ranks=32 run ranks=32` for seasons with pbp; no crash.

- [ ] **Step 2: Spot-check artifact**

Assert for a WR and same-team teammate: identical `ol_pass_block_rank.value` and `neutral_pace_rank.value`, provenance `measured`.  
QB: no `adp` key under `factors`; `injury_concern` present; `ol_pass_block_rank` measured or missing only if no team.  
RB: `ol_run_block_rank` measured.  
TE: `ol_pass_block_rank` + `neutral_pace_rank` measured; `inline_pct` / `yprr_rank` still unsourced.

Record half-PPR cohort means from `artifacts/benchmarks.json` for DraftLab Task 4 (copy exact floats).

- [ ] **Step 3: Update SAD + PLAN**

`docs/SAD.md` — replace OL licensed bullets with proxy notes; keep FTN DVOA + TE PFF; note QB ADP removed from ceiling.

`docs/PLAN.md` — ITEM-006 status **In Review** with notes pointing at this plan + measured means.

- [ ] **Step 4: Commit**

```bash
cd c:\Code\sleeperMCP
git add artifacts/benchmarks.json artifacts/player_factors.json docs/SAD.md docs/PLAN.md
git commit -m "$(cat <<'EOF'
chore: regenerate artifacts for OL proxies and shared pace

EOF
)"
```

---

### Task 4: DraftLab factor lists, known counts, remove excludeAdp

**Files:**
- Modify: `packages/evaluation-engine/src/config/benchmarks.ts`
- Modify: `packages/evaluation-engine/src/config/grade-weights.ts`
- Modify: `packages/evaluation-engine/src/ceiling.ts`
- Create: `packages/evaluation-engine/src/__tests__/ol-proxy-shared-factors.test.ts`
- Modify: `packages/evaluation-engine/src/__tests__/spot-checks.test.ts` (drop QB `adp` factor input)
- Modify: `apps/api/src/data/seed-players.ts`, `apps/api/src/data/seed-depth.ts` (drop ceiling `adp` factor rows; add QB `injury_concern` categorical if seeds drive ceiling)

**Interfaces:**
- Consumes: cohort means from Task 3 `benchmarks.json`
- Produces: QB without `adp`, with `injury_concern`; WR/TE with `neutral_pace_rank`; TE with `ol_pass_block_rank`; proxy labels; `CEILING_KNOWN_FACTORS` QB=11 RB=16 TE=12 WR=17; `CeilingOptions` without `excludeAdp`

- [ ] **Step 1: Write the failing test**

Create `packages/evaluation-engine/src/__tests__/ol-proxy-shared-factors.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { BENCHMARKS_2025 } from '../config/benchmarks.js';
import { CEILING_RANGE } from '../config/grade-weights.js';
import { computeCeilingScore } from '../ceiling.js';

describe('ITEM-006 factor lists', () => {
  it('QB has injury_concern and no adp', () => {
    const ids = BENCHMARKS_2025.QB.factors.map((f) => f.id);
    expect(ids).toContain('injury_concern');
    expect(ids).not.toContain('adp');
    expect(ids).toContain('ol_pass_block_rank');
  });

  it('WR and TE have neutral_pace_rank; TE has ol_pass_block_rank', () => {
    expect(BENCHMARKS_2025.WR.factors.map((f) => f.id)).toContain('neutral_pace_rank');
    expect(BENCHMARKS_2025.TE.factors.map((f) => f.id)).toContain('neutral_pace_rank');
    expect(BENCHMARKS_2025.TE.factors.map((f) => f.id)).toContain('ol_pass_block_rank');
  });

  it('OL labels say proxy', () => {
    const qbOl = BENCHMARKS_2025.QB.factors.find((f) => f.id === 'ol_pass_block_rank')!;
    const rbOl = BENCHMARKS_2025.RB.factors.find((f) => f.id === 'ol_run_block_rank')!;
    expect(qbOl.label.toLowerCase()).toContain('proxy');
    expect(rbOl.label.toLowerCase()).toContain('proxy');
  });

  it('computeCeilingScore has no adp factor', () => {
    const result = computeCeilingScore('QB', []);
    expect(result.factors.find((f) => f.factorId === 'adp')).toBeUndefined();
  });

  it('known-factor ranges match ITEM-006 coverage', () => {
    expect(CEILING_RANGE.QB.max).toBe(11 * 5);
    expect(CEILING_RANGE.RB.max).toBe(16 * 5);
    expect(CEILING_RANGE.TE.max).toBe(12 * 5);
    expect(CEILING_RANGE.WR.max).toBe(17 * 5);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:\Code\fantasy-football-draft-optimizer\fantasy-football-draft-optimizer && npx vitest run packages/evaluation-engine/src/__tests__/ol-proxy-shared-factors.test.ts`

Expected: FAIL

- [ ] **Step 3: Update benchmarks.ts**

QB: remove `adp` block; add `injury_concern` categorical (same shape as WR). Update OL pass label to `'OL pass block rank (proxy)'` and set `benchmark` to Task 3 half-PPR cohort mean for QB (WR/TE each use their own means).

WR: add `neutral_pace_rank` (lowerBetter, situational) with WR cohort mean; update OL pass label + mean.

TE: add `ol_pass_block_rank` (proxy label + TE cohort mean) and `neutral_pace_rank` (TE cohort mean).

RB: update `ol_run_block_rank` label + cohort mean.

- [ ] **Step 4: Update grade-weights.ts + ceiling.ts**

```typescript
const CEILING_KNOWN_FACTORS: Record<Position, number> = {
  QB: 11,
  RB: 16,
  TE: 12,
  WR: 17,
};
```

Refresh the comment block (drop references to licensed OL / QB adp as known gaps).

In `ceiling.ts`, remove `excludeAdp?: boolean` and the `.filter((f) => !(options.excludeAdp && f.id === 'adp'))` line — factors list is used as-is.

- [ ] **Step 5: Fix seeds and spot-checks**

Remove `{ factorId: 'adp', ... }` from QB factor arrays in `seed-players.ts` / `seed-depth.ts` / `spot-checks.test.ts`. Add QB `injury_concern` categorical where a full green strip is expected. Keep `market.adpRoundPick`.

- [ ] **Step 6: Run tests**

Run:

```bash
cd c:\Code\fantasy-football-draft-optimizer\fantasy-football-draft-optimizer
npx vitest run packages/evaluation-engine
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd c:\Code\fantasy-football-draft-optimizer\fantasy-football-draft-optimizer
git add packages/evaluation-engine apps/api/src/data/seed-players.ts apps/api/src/data/seed-depth.ts
git commit -m "$(cat <<'EOF'
feat: OL proxy factors, shared pace/injury, drop QB ceiling ADP

EOF
)"
```

---

### Task 5: Live spot-check via artifact

**Files:**
- Optional helper under `sleeperMCP/.superpowers/sdd/` if useful

- [ ] **Step 1: Assert known counts from artifact**

For Amon-Ra St. Brown / Jahmyr Gibbs / an elite QB / Bowers: count factors with `provenance == 'measured'` (and categorical injury) vs list length; only expected unknowns: QB `pass_dvoa_rank`, TE `inline_pct` + `yprr_rank`.

- [ ] **Step 2: Commit any script/docs fixes if needed**

Only if Step 1 required a durable helper. Otherwise skip commit.

---

### Task 6: Publish R2 + deploy Worker + mark Done

**Files:**
- Modify: `docs/PLAN.md` (ITEM-006 → Done)

- [ ] **Step 1: Push sleeperMCP branch / PR and run publish-artifacts Action**

Follow existing ITEM-005 pattern: merge or workflow_dispatch publish to Drake R2. Confirm Action green.

- [ ] **Step 2: Deploy DraftLab Worker**

```bash
cd c:\Code\fantasy-football-draft-optimizer\fantasy-football-draft-optimizer
npm run build:packages
npm run deploy:worker
```

Redeploy **after** R2 publish completes (warm isolates cache artifacts).

- [ ] **Step 3: Live verify**

Hit Worker / board for ARSB, Gibbs, Josh Allen (or Mendoza): ceiling strip shows OL grades; no QB `adp` factor; WR/TE show pace; TE still `?` on inline/yprr_rank.

- [ ] **Step 4: Mark PLAN Done + commit**

```bash
cd c:\Code\sleeperMCP
# edit docs/PLAN.md ITEM-006 → Done with R2 run URL + known counts
git add docs/PLAN.md
git commit -m "$(cat <<'EOF'
docs: mark ITEM-006 Done after R2 publish and Worker deploy

EOF
)"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Pressure / stuff OL proxies | 1–2 |
| Attach OL to QB/WR/TE and RB | 2 |
| WR/TE `neutral_pace_rank` | 2 |
| DraftLab QB `injury_concern` | 4 |
| Remove QB ceiling `adp` / `excludeAdp` | 2, 4 |
| Top-3 cohort recalibration | 3–4 |
| SAD / PLAN | 3, 6 |
| R2 + Worker + acceptance spot-checks | 5–6 |
| Out of scope items left alone | Global Constraints |

## Self-review notes

- No opponent-adjusted rates; ties broken by team abbr (deterministic).
- Separate pbp fetch for OL is OK — `nflverse_csv` caches on disk; optional later merge into one pass is non-goal.
- `CEILING_KNOWN_FACTORS` uses confirmed coverage (QB still missing DVOA = 11).

# QBR, TE Route Participation, Injury Concern Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill DraftLab ceiling factors `qbr_rank`, `qb_qbr_rank`, `route_participation`, and `injury_concern` from public nflverse feeds, ship via existing R2 artifacts, and teach DraftLab to consume injury categoricals.

**Architecture:** Extend `build_benchmarks.py` / `build_factors.py` with three best-effort loaders (ESPN QBR, pbp participation, injuries), same pattern as TDD-001 RB PBP. Keep DraftLab factor ids. Update `load-artifact.ts` + `CEILING_KNOWN_FACTORS`. Publish through the existing `publish-artifacts` Action.

**Tech Stack:** Python 3.12, pytest, nflverse CSV via `sleeper_core.http.nflverse_csv`, TypeScript/Vitest in DraftLab, Cloudflare R2.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-11-qbr-route-injury-factors-design.md`
- Never fabricate numeric `0` for ranks or route % on loader failure — leave unset
- WR: no `qb_pff_rank` proxy and no WR `route_participation` in this ITEM
- Keep factor ids; change source tags only (`nflverse:espn_qbr`, `nflverse:participation`, `nflverse:injuries`)
- Injury window = **prior full measured season**; escalate if listed ≥3 distinct weeks
- `archetype` stays DraftLab-computed; only `injury_concern` categorical comes from the artifact
- Attribute FTN via nflverse for participation 2023+ (CC-BY-SA) in notes/docs where relevant
- TDD: failing test first for each loader; commit after each green task

## File map

| File | Responsibility |
|------|----------------|
| `tools/build_benchmarks.py` | `load_espn_qbr_season`, `load_te_route_participation`, wire into `load_player_seasons` / `COMPUTABLE` / `FACTORS` / `FACTOR_KIND` / `per_game` |
| `tools/build_factors.py` | `TEAM_CONTEXT` += `qb_qbr_rank`; `load_injury_concern_season`; emit `categorical` on injury factors |
| `tests/test_qbr_factors.py` | Unit tests for QBR loader + ranking (mocked CSV) |
| `tests/test_te_route_participation.py` | Unit tests for TE route % (mocked participation) |
| `tests/test_injury_concern.py` | Unit tests for injury severity mapping |
| `docs/PLAN.md`, `docs/SAD.md` | ITEM status + coverage table |
| DraftLab `apps/api/src/data/load-artifact.ts` | Pass through injury categorical |
| DraftLab `apps/api/src/data/__tests__/load-artifact.test.ts` | Assert injury retained |
| DraftLab `packages/evaluation-engine/src/config/grade-weights.ts` | Bump `CEILING_KNOWN_FACTORS` |

---

### Task 1: ESPN QBR loader + QB ranks

**Files:**
- Modify: `tools/build_benchmarks.py`
- Create: `tests/test_qbr_factors.py`

**Interfaces:**
- Produces: `load_espn_qbr_season(season: int) -> dict[str, dict]`  
  Keys = `_qb_name_key(name_display)`. Values = `{"qbr": float, "qb_plays": int, "team": str | None, "rank": int}` after ranking.  
  Empty dict on failure.
- Produces: season-wide rank map usable for QB `qbr_rank` and TE `qb_qbr_rank`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_qbr_factors.py
from __future__ import annotations
from unittest.mock import patch
import build_benchmarks as bb

def test_empty_qbr_fetch_is_best_effort() -> None:
    with patch.object(bb, "nflverse_csv", return_value=[]):
        assert bb.load_espn_qbr_season(2024) == {}

def test_qbr_ranks_qualified_qbs_lower_better() -> None:
    rows = [
        {"season": "2024", "name_display": "A.Allen", "qbr_total": "70.0",
         "qb_plays": "400", "team_abb": "BUF", "qualified": "True"},
        {"season": "2024", "name_display": "B.Backup", "qbr_total": "40.0",
         "qb_plays": "50", "team_abb": "BUF", "qualified": "True"},
        {"season": "2023", "name_display": "C.Other", "qbr_total": "99.0",
         "qb_plays": "400", "team_abb": "KC", "qualified": "True"},
    ]
    with patch.object(bb, "nflverse_csv", return_value=rows):
        out = bb.load_espn_qbr_season(2024)
    assert out[bb._qb_name_key("A.Allen")]["rank"] == 1
    assert out[bb._qb_name_key("B.Backup")]["rank"] == 2
    assert bb._qb_name_key("C.Other") not in out
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `python -m pytest tests/test_qbr_factors.py -v`  
Expected: FAIL (`load_espn_qbr_season` missing)

- [ ] **Step 3: Implement `load_espn_qbr_season`**

Add near other loaders in `tools/build_benchmarks.py`:

```python
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
```

Verify real column names once against a live download (`name_display`, `qbr_total`, `qualified`, `team_abb`) and adjust the reader — do not invent columns.

Also update FACTORS tags:

```python
("qbr_rank", "nflverse:espn_qbr"),
...
("qb_qbr_rank", "nflverse:espn_qbr"),
```

Add to `COMPUTABLE`: `"qbr_rank", "qb_qbr_rank"`.  
Add to `FACTOR_KIND`: `"qbr_rank": "rank", "qb_qbr_rank": "rank"`.

- [ ] **Step 4: Wire into `load_player_seasons`**

After QB PBP enrichment block, for each season:

```python
qbr = load_espn_qbr_season(season)
if qbr:
    # QB personal rank
    for a in agg.values():
        if a["position"] != "QB":
            continue
        hit = None
        for k in name_keys(a["name"]):
            if k in qbr:
                hit = qbr[k]
                break
            # also try _qb_name_key form
            kk = _qb_name_key(a["name"])
            if kk in qbr:
                hit = qbr[kk]
                break
        if hit:
            a["qbr_rank"] = hit["rank"]

    # Primary QB per team = max qb_plays among QBs with QBR on that team
    primary_by_team: dict[str, int] = {}
    candidates: dict[str, list] = defaultdict(list)
    for entry in qbr.values():
        if entry.get("team"):
            candidates[entry["team"]].append(entry)
    for team, ents in candidates.items():
        ents.sort(key=lambda e: e["qb_plays"], reverse=True)
        primary_by_team[team] = ents[0]["rank"]

    for a in agg.values():
        if a["position"] != "TE":
            continue
        team = a.get("team")
        if team and team in primary_by_team:
            a["qb_qbr_rank"] = primary_by_team[team]
```

Extend `per_game` passthrough so `qbr_rank` / `qb_qbr_rank` on `ps` appear in output (they are already in `FACTOR_KIND` as ranks — ensure `passthrough` includes them via `FACTOR_KIND`).

- [ ] **Step 5: Add `qb_qbr_rank` to `TEAM_CONTEXT` in `build_factors.py`**

```python
TEAM_CONTEXT = {..., "qb_qbr_rank"}
```

- [ ] **Step 6: Run tests — expect PASS**

Run: `python -m pytest tests/test_qbr_factors.py -v`  
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add tools/build_benchmarks.py tools/build_factors.py tests/test_qbr_factors.py
git commit -m "feat: compute qbr_rank and TE qb_qbr_rank from nflverse ESPN QBR"
```

---

### Task 2: TE route participation loader

**Files:**
- Modify: `tools/build_benchmarks.py`
- Create: `tests/test_te_route_participation.py`

**Interfaces:**
- Produces: `load_te_route_participation(season: int) -> dict[str, float]`  
  Keys = `_qb_name_key(player_name)`. Values = route participation percent `0–100`.  
  `{}` on failure.
- **Definition (verified against nflverse participation dictionary):** the `route` column is the *primary receiver's route type*, not per-player route flags. Use:

  `100 * (pass plays where TE gsis_id ∈ offense_players) / (team pass plays)`

  Join participation (`pbp_participation/pbp_participation_{season}.csv`) to pbp for `play_type == "pass"` and REG season. Map gsis_id → name via `stats_player` or roster rows already loaded. If join/columns unavailable → `{}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_te_route_participation.py
from __future__ import annotations
from unittest.mock import patch
import build_benchmarks as bb

def test_empty_participation_is_best_effort() -> None:
    with patch.object(bb, "nflverse_csv", return_value=[]):
        assert bb.load_te_route_participation(2024) == {}

def test_te_on_field_for_pass_counts_toward_rate() -> None:
    """Synthetic: TE on 2/2 team pass plays → 100%."""
    # Implementation detail: unit-test the pure helper that scores
    # pre-parsed (player_key, team, on_pass_play) events if the full
    # CSV join is awkward to mock — expose _route_rates_from_events.
    events = [
        {"player_key": "t.kelce", "team": "KC", "team_pass_plays": 2, "on_pass": 2},
        {"player_key": "n.gray", "team": "KC", "team_pass_plays": 2, "on_pass": 1},
    ]
    rates = bb._route_rates_from_events(events)
    assert rates["t.kelce"] == 100.0
    assert rates["n.gray"] == 50.0
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `python -m pytest tests/test_te_route_participation.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement helper + loader**

```python
def _route_rates_from_events(events: list[dict]) -> dict[str, float]:
    out = {}
    for e in events:
        denom = e["team_pass_plays"]
        if denom <= 0:
            continue
        out[e["player_key"]] = round(100.0 * e["on_pass"] / denom, 3)
    return out

def load_te_route_participation(season: int) -> dict[str, float]:
    """TE route participation % proxy from pbp_participation + pbp.

    See plan Task 2 definition. Best-effort: {} on failure.
    Attribution: FTN via nflverse for 2023+ (CC-BY-SA).
    """
    # 1) Load participation with row_filter keeping needed columns only
    # 2) Load pbp pass plays (reuse keep from load_qb_pbp_season style)
    # 3) Join on (game_id/nflverse_game_id, play_id)
    # 4) For each pass play, split offense_players on ';' / ' ' into gsis ids
    # 5) Build team_pass_plays[team], on_pass[gsis]
    # 6) Map gsis → display name via stats_player week file for that season
    # 7) Return {_qb_name_key(name): rate} for players with TE position in stats
    ...
```

Update FACTORS: `("route_participation", "nflverse:participation")`.  
Add `"route_participation"` to `COMPUTABLE` and `FACTOR_KIND` as `"rate"`.

Wire in `load_player_seasons` for `position == "TE"`: set `a["route_participation"]` from the map (name-key join). Ensure `per_game` returns it as a rate (already a percent — do **not** divide by games).

- [ ] **Step 4: Run tests — expect PASS**

Run: `python -m pytest tests/test_te_route_participation.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/build_benchmarks.py tests/test_te_route_participation.py
git commit -m "feat: compute TE route_participation from nflverse participation"
```

---

### Task 3: Injury concern (prior season)

**Files:**
- Modify: `tools/build_factors.py` (primary emission), optionally small pure helpers in same file or `build_benchmarks.py`
- Create: `tests/test_injury_concern.py`

**Interfaces:**
- Produces: `classify_injury_concern(week_statuses: list[str]) -> str`  
  Returns one of `minimal|some|concerned|serious`.
- Produces: `load_injury_concern_season(season: int) -> dict[str, str]`  
  Keys = name_key (use same `name_keys` / surname strategy as factors). Values = severity.

**Mapping (spec):**
- Base from worst status substring (case-insensitive):  
  `out` / `injured reserve` / `ir` → serious; `doubtful` → concerned; `questionable` → some; else minimal if any listing
- If distinct weeks listed ≥ 3 and base != serious → escalate one step (`minimal→some→concerned→serious`)
- No rows for player → `minimal`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_injury_concern.py
from __future__ import annotations
from unittest.mock import patch
import build_factors as bf

def test_out_is_serious() -> None:
    assert bf.classify_injury_concern(["Out", "Questionable"]) == "serious"

def test_three_questionable_weeks_escalates() -> None:
    # three distinct weeks of Questionable → some → concerned
    assert bf.classify_injury_concern(
        ["Questionable", "Questionable", "Questionable"]
    ) == "concerned"

def test_no_listings_is_minimal() -> None:
    assert bf.classify_injury_concern([]) == "minimal"

def test_empty_injury_file_returns_empty_map() -> None:
    with patch.object(bf, "nflverse_csv", return_value=[]):
        assert bf.load_injury_concern_season(2024) == {}
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `python -m pytest tests/test_injury_concern.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement classifier + loader**

```python
_SEVERITY_ORDER = ["minimal", "some", "concerned", "serious"]

def _status_base(status: str) -> str:
    s = (status or "").lower()
    if "out" in s or "injured reserve" in s or s.strip() == "ir" or " inactive" in f" {s}":
        return "serious"
    if "doubtful" in s:
        return "concerned"
    if "questionable" in s:
        return "some"
    return "minimal"

def classify_injury_concern(week_statuses: list[str]) -> str:
    if not week_statuses:
        return "minimal"
    base = "minimal"
    for st in week_statuses:
        b = _status_base(st)
        if _SEVERITY_ORDER.index(b) > _SEVERITY_ORDER.index(base):
            base = b
    if len(week_statuses) >= 3 and base != "serious":
        base = _SEVERITY_ORDER[min(_SEVERITY_ORDER.index(base) + 1, 3)]
    return base

def load_injury_concern_season(season: int) -> dict[str, str]:
    rows = nflverse_csv("injuries", f"injuries_{season}.csv", ttl=STATS_CACHE_TTL)
    if not rows:
        return {}
    by_player: dict[str, list[str]] = defaultdict(list)
    weeks_seen: dict[str, set] = defaultdict(set)
    for r in rows:
        name = r.get("full_name") or r.get("player_name") or ""
        week = str(r.get("week") or "")
        status = r.get("report_status") or r.get("practice_status") or ""
        if not name or not status:
            continue
        key = None
        for k in name_keys(name):
            key = k
            break
        if not key:
            continue
        if week in weeks_seen[key]:
            continue
        weeks_seen[key].add(week)
        by_player[key].append(status)
    return {k: classify_injury_concern(v) for k, v in by_player.items()}
```

- [ ] **Step 4: Emit categorical in `player_row` / factor loop**

For `injury_concern` specifically (all positions that list it in FACTORS):

- Treat as computable enough to not emit `unsourced` gap note.
- Set `value: 1`, `categorical: <severity>`, `provenance: measured` (or `measured` healthy default when player absent from map → `minimal`).
- Do **not** put injury into numeric `COMPUTABLE` cohort means — exclude from `cohort_means` fields (injury stays categorical in benchmarks.json with note, or omit from numeric factors array update). Simplest: leave benchmarks.ts categorical entry unchanged; do not add injury to `COMPUTABLE`.

Update FACTORS source tag to `"nflverse:injuries"` for injury_concern rows.

In the factor loop, special-case before the `COMPUTABLE` check:

```python
if fid == "injury_concern":
    severity = injury_map.get(match_key) or "minimal"
    # try all name_keys for the player
    factors[fid] = {
        "value": 1,
        "categorical": severity,
        "provenance": "measured",
        "note": None,
    }
    continue
```

Pass `injury_map = load_injury_concern_season(season)` into the build once per artifact build.

- [ ] **Step 5: Run tests — expect PASS**

Run: `python -m pytest tests/test_injury_concern.py -v`  
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tools/build_factors.py tests/test_injury_concern.py
git commit -m "feat: derive injury_concern categorical from prior-season nflverse injuries"
```

---

### Task 4: DraftLab artifact consumer + known-factor counts

**Files:**
- Modify: `fantasy-football-draft-optimizer/fantasy-football-draft-optimizer/apps/api/src/data/load-artifact.ts`
- Modify: `apps/api/src/data/__tests__/load-artifact.test.ts`
- Modify: `packages/evaluation-engine/src/config/grade-weights.ts`

**Interfaces:**
- Extends `ArtifactFactor` with optional `categorical?: string | null`
- `injury_concern` with non-null categorical is kept; still-null injury is omitted
- `archetype` still filtered and recomputed

- [ ] **Step 1: Write / update failing test**

In `load-artifact.test.ts`, add a player factor:

```ts
injury_concern: {
  value: 1,
  provenance: 'measured',
  note: null,
  categorical: 'concerned',
},
```

Assert:

```ts
expect(sp.factors.find((f) => f.factorId === 'injury_concern')).toEqual(
  expect.objectContaining({ categorical: 'concerned', value: 1 }),
);
```

Remove/update any assertion that `injury_concern` is always absent.

- [ ] **Step 2: Run test — expect FAIL**

Run (from DraftLab repo root):  
`npm test -w @draftlab/api -- load-artifact`  
(or the project's vitest filter for that file)

- [ ] **Step 3: Implement load-artifact changes**

```ts
interface ArtifactFactor {
  value: number | null;
  provenance: string;
  note: string | null;
  categorical?: string | null;
}

const factors: FactorInput[] = Object.entries(p.factors)
  .filter(([factorId, f]) => {
    if (factorId === 'archetype') return false;
    if (factorId === 'injury_concern') {
      return f.categorical != null && f.categorical !== '';
    }
    return true;
  })
  .map(([factorId, f]) => ({
    factorId,
    value: f.value,
    provenance: f.provenance,
    ...(f.categorical ? { categorical: f.categorical as FactorInput['categorical'] } : {}),
  }));
```

Update file header comment to match.

- [ ] **Step 4: Bump `CEILING_KNOWN_FACTORS`**

```ts
const CEILING_KNOWN_FACTORS: Record<Position, number> = {
  QB: 10, // was 8: +qbr_rank +injury_concern
  RB: 11, // was 10: +injury_concern
  TE: 10, // was 7: +qb_qbr_rank +route_participation +injury_concern
  WR: 7,  // was 6: +injury_concern
};
```

Fix any tests that hardcode old known counts / ceiling ranges.

- [ ] **Step 5: Run tests — expect PASS**

- [ ] **Step 6: Commit (DraftLab repo)**

```bash
git add apps/api/src/data/load-artifact.ts apps/api/src/data/__tests__/load-artifact.test.ts packages/evaluation-engine/src/config/grade-weights.ts
git commit -m "feat: ingest injury_concern from artifacts; bump ceiling known factors"
```

---

### Task 5: Docs, regenerate artifacts, publish

**Files:**
- Modify: `docs/PLAN.md`, `docs/SAD.md`
- Regenerate: `artifacts/player_factors.json`, `artifacts/benchmarks.json`
- Optional: `docs/tdd/TDD-002-qbr-route-injury-factors.md` (short Done note pointing at the design spec)

- [ ] **Step 1: Update PLAN + SAD**

Add ITEM-002 Done (or In Design→Done after publish) describing QBR / route / injury.  
Update SAD ceiling coverage table: remove those three from licensed blocked list; note injury now sourced; keep PFF/YPRR/DVOA/RP blocked.

- [ ] **Step 2: Regenerate artifacts**

```bash
cd c:\Code\sleeperMCP
python tools/build_benchmarks.py
python tools/build_factors.py
python tools/check_artifact_count.py --new artifacts/player_factors.json
```

Sanity-check a known QB has `qbr_rank` measured; a TE has `qb_qbr_rank` + `route_participation`; any player has `injury_concern.categorical`.

- [ ] **Step 3: Run full sleeperMCP unit tests**

```bash
python -m pytest tests/test_qbr_factors.py tests/test_te_route_participation.py tests/test_injury_concern.py tests/test_rb_pbp_factors.py -v
```

- [ ] **Step 4: Commit sleeperMCP docs + artifacts**

```bash
git add docs/PLAN.md docs/SAD.md artifacts/player_factors.json artifacts/benchmarks.json
git commit -m "docs+data: ship QBR, TE route participation, and injury concern factors"
```

- [ ] **Step 5: Publish to Drake R2**

```bash
gh workflow run publish-artifacts.yml -R jarrod6424/sleeperMCP
gh run watch --repo jarrod6424/sleeperMCP
```

Or local wrangler put with Drake `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID=247649…`.

- [ ] **Step 6: Redeploy draftlab-api if DraftLab changes merged; confirm logs**

Expect: `factors=cache` and spot-check TE/QB factors no longer `unknown` for the new ids.

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|------------------|------|
| `qbr_rank` from ESPN QBR | Task 1 |
| `qb_qbr_rank` TE team-context | Task 1 |
| `route_participation` TE | Task 2 |
| `injury_concern` prior season + escalate ≥3 weeks | Task 3 |
| No WR qb_pff / route | Global Constraints |
| Best-effort unset not zero | Tasks 1–2 tests |
| DraftLab load-artifact categorical | Task 4 |
| CEILING_KNOWN_FACTORS bumps | Task 4 |
| R2 publish | Task 5 |
| Explicit non-goals (PFF/YPRR/DVOA/RP) | Global Constraints / SAD |

No TBD placeholders remain; route definition explicitly accounts for participation dictionary limits.

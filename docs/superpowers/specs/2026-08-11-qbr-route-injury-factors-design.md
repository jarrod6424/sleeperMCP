# QBR, TE route participation, and injury concern factors

Approved 2026-08-11 (brainstorming). Approach: extend existing
`build_benchmarks.py` / `build_factors.py` → R2 artifacts → DraftLab.

## Problem

DraftLab ceiling factors still mark these as licensed/categorical gaps even
though public nflverse feeds can fill them:

| Factor | Position(s) | Was tagged |
|--------|-------------|------------|
| `qbr_rank` | QB | `licensed:ESPN` |
| `qb_qbr_rank` | TE | `licensed:ESPN` |
| `route_participation` | TE | `licensed:PFF` |
| `injury_concern` | QB/RB/WR/TE | categorical, never sourced |

WR `qb_pff_rank` / YPRR / OL PFF / DVOA / Reception Perception stay out of
scope (WR QB-quality proxy deferred to a later ITEM).

## Design

### Data flow

```text
nflverse (espn QBR, participation, injuries)
        │
        ▼
build_benchmarks.py  — cohort benchmarks for numeric factors
build_factors.py     — per-player values + injury categorical
        │
        ▼
artifacts/{benchmarks,player_factors}.json
        │
        ▼
GH Action publish-artifacts → Drake R2 → draftlab-api
```

Same ownership boundary as TDD-001: sleeperMCP measures; DraftLab grades.

### Factor definitions

**`qbr_rank` (QB)**  
Load nflverse ESPN season Total QBR for the measured season. Rank qualified
QBs by QBR descending → integer rank `1…N` (`lowerBetter`). Unqualified or
missing → factor unset (not `0`).

**`qb_qbr_rank` (TE only)**  
Team-context: attach the team primary QB’s QBR rank (same season). Primary
QB = highest pass attempts among that team’s QBs with QBR. Provenance on
trade / no team matches `off_ppg_rank` (`stale:team_changed`,
`missing:no_team_context`). Add to `TEAM_CONTEXT`.

**`route_participation` (TE only)**  
From nflverse participation (route flag when present):

`100 * (player route snaps) / (team offensive snaps in those games)`

Scale matches DraftLab benchmark (~79.8). Empty participation → unset, not
`0`. Implementer must verify the exact participation/route column names
against the nflverse dictionary (same discipline as TDD-001’s `goal_to_go`
check) rather than assume; if routes are unavailable for a season, leave
the factor unset for that season’s players.

**`injury_concern` (QB/RB/WR/TE)**  
- Window: **prior full measured season** injury reports (not current week).  
- Base from worst status: Out/IR → `serious`, Doubtful → `concerned`,
  Questionable → `some`, else `minimal`.  
- Escalate one step if listed in **≥3 distinct weeks** and not already
  `serious`.  
- No injury rows all year → `minimal` with provenance `measured` (healthy
  default), not unknown.

Artifact shape for injury: emit `value: 1` (placeholder matching DraftLab
benchmark) plus `categorical: minimal|some|concerned|serious` and provenance.
(Numeric ranks/route % keep `value` only.)

### Source / COMPUTABLE updates

In `build_benchmarks.py` `FACTORS`:

- `qbr_rank`, `qb_qbr_rank` → `nflverse:espn_qbr`
- `route_participation` → `nflverse:participation`
- `injury_concern` stays categorical but becomes computable via injuries feed
  (`nflverse:injuries`)

Add the numeric three + injury emission path to `COMPUTABLE` (or equivalent
emission path for categorical) so artifacts stop shipping gap notes.

### Failure behavior

- Any loader failure → leave that factor unset for affected players only
  (same best-effort pattern as `load_qb_pbp_season` / `load_rb_pbp_season`).
- Never fabricate numeric `0` for ranks or route %.
- Do not block the whole artifact build on one feed failing.

### DraftLab follow-through (required)

`apps/api/src/data/load-artifact.ts` currently **filters out**
`injury_concern` and never reads categoricals from the artifact. This ITEM
must:

1. Accept artifact `injury_concern` with `categorical` in
   `minimal|some|concerned|serious`.
2. Keep `archetype` DraftLab-computed via `classifyArchetype` (unchanged).
3. Bump `CEILING_KNOWN_FACTORS` in `grade-weights.ts`:
   - QB: 8 → 10 (`qbr_rank` + `injury_concern`)
   - TE: 7 → 10 (`qb_qbr_rank` + `route_participation` + `injury_concern`)
   - WR: 6 → 7 (`injury_concern` only)
   - RB: 10 → 11 (`injury_concern` only)
4. Recompute / merge cohort benchmarks for the three numeric factors from
   sleeperMCP `benchmarks.json` (existing R2 activate path). Injury stays
   categorical with benchmark `1`.

### Explicit non-goals

- WR `qb_pff_rank` QBR proxy  
- `yprr`, `inline_pct`, `ol_*_block_rank`, `pass_dvoa_rank`, Reception Perception  
- In-season injury overlay (trailing-N current reports) — future ITEM  
- New MCP conversational tools (existing injury/snap tools already cover chat)

## Test plan

1. QBR loader empty → `qbr_rank` / `qb_qbr_rank` absent, not `0`.
2. Known high-QBR QB → low rank; directional sanity only.
3. TE `qb_qbr_rank` matches team primary QB; traded TE → `stale:team_changed`.
4. TE route %: featured TE high vs blocking TE low; empty file → unset.
5. Injury: Out/IR → `serious`; ≥3 Questionable weeks escalate; none → `minimal`.
6. `benchmarks.json`: nonzero cohort means for the three numeric factors.
7. Golden fixtures extended after sanity; DraftLab `load-artifact` tests assert
   `injury_concern` categorical is retained.
8. After R2 publish: Worker log still `factors=cache`; spot-check TE/QB ceiling
   no longer `unknown` for the new ids.

## Decisions

- **Approach:** extend existing build pipeline (not MCP-only, not a second
  enrichment artifact).
- **Injury mapping:** status + multi-week escalate (≥3 weeks).
- **Injury window:** prior full measured season (stable for August drafts).
- **WR:** no new QB-quality or route factor in this ITEM.
- **Factor ids:** keep DraftLab ids; change provenance/source tags only.

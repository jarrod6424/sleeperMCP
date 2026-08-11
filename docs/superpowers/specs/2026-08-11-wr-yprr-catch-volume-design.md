# WR YPRR proxy, NGS catch %, and volume factors

Approved 2026-08-11 (brainstorming). Approach: extend existing
`build_benchmarks.py` / `build_factors.py` → R2 artifacts → DraftLab
(same pattern as ITEM-003 / ITEM-004).

## Problem

WR factor breakdown still shows `?` for `yprr` and `reception_perception`
(tagged licensed PFF / Reception Perception), while volume only grades
targets / receptions / TDs. Elite WRs (e.g. St. Brown) show **10/13**
confidence with thin volume and two avoidable unknowns. Only
`ol_pass_block_rank` must remain a licensed gap.

Public fills and adds:

| Factor | Action | Source |
|--------|--------|--------|
| `yprr` | Fill (proxy) | `receiving_yards / routes` where routes = participation `on_pass` |
| `reception_perception` | Fill (proxy) | nflverse NGS receiving `catch_percentage` |
| `yards_per_catch` | Add | `receiving_yards / receptions` |
| `yac_per_reception` | Add | `receiving_yards_after_catch / receptions` |
| `target_share` | Add | season mean of weekly nflverse `target_share` |
| `ol_pass_block_rank` | Leave | `licensed:PFF` |

`target_share` is not redundant with `targets` /g, `team_position_rank`, or
`secondary_target`: absolute volume vs share of the team's passing pie.

## Design

### Data flow

```text
nflverse (weekly stats, pbp_participation routes, nextgen_stats receiving)
        │
        ▼
build_benchmarks.py  — cohort means for the five factors
build_factors.py     — per-WR values
        │
        ▼
artifacts/{benchmarks,player_factors}.json
        │
        ▼
GH Action publish-artifacts → Drake R2 → draftlab-api
```

Ownership unchanged: sleeperMCP measures; DraftLab grades.

### Factor definitions

**`yprr` (keep id)**  
`receiving_yards / on_pass`, where `on_pass` is the same participation route
count already used for WR `route_participation`. Label in DraftLab:
“Yards per route run (proxy)”. Source tag: `licensed:PFF` →
`nflverse:participation`. Unset if routes = 0 or yards missing — never `0`.

**`reception_perception` (keep id)**  
Season NGS receiving `catch_percentage` (prefer week-0 / season summary row;
otherwise a documented season aggregate). Emit on a 0–100 scale matching
DraftLab grading (normalize if NGS ships 0–1). Label: “Catch % (NGS proxy)”.
Source tag: `licensed:RP` → `nflverse:ngs`. Unset when NGS has no row
(low-volume WRs often absent) — never fabricate.

**`yards_per_catch` (new)**  
`receiving_yards / receptions` from season aggregates. Unset if receptions = 0.
Category: volume. Direction: higherBetter.

**`yac_per_reception` (new)**  
Aggregate `receiving_yards_after_catch` in `load_player_seasons`, then
`yac / receptions`. Unset if receptions = 0. Category: volume.

**`target_share` (new)**  
Season mean of weekly `target_share` from nflverse stats (column already
known to `sleeper_core.stats.STAT_KEEP`). Category: volume. higherBetter.

### sleeperMCP changes

1. Aggregate `receiving_yards_after_catch` (and keep `receiving_yards`) in
   `load_player_seasons`.
2. Extend `_efficiency_yards` / `per_game` for WR rates:
   `yards_per_catch`, `yac_per_reception`.
3. Attach season-average `target_share` on player rows.
4. Extend the route-participation path (or a thin sibling) to emit `yprr`
   keyed like `route_participation`.
5. Add `load_ngs_catch_pct(season)` from nflverse `nextgen_stats` receiving;
   attach as `reception_perception`.
6. Update `FACTORS["WR"]` and `COMPUTABLE`:
   - retag `yprr`, `reception_perception`
   - add `yards_per_catch`, `yac_per_reception`, `target_share`
   - volume order: after `receptions` (or after `touchdowns`); keep
     `yprr` / catch % in situational slots

### DraftLab follow-through (required)

1. Add `yards_per_catch`, `yac_per_reception`, `target_share` to WR
   `benchmarks.ts` (volume, higherBetter; cohort means from sleeperMCP
   `benchmarks.json`).
2. Relabel `yprr` and `reception_perception` as proxies (above).
3. Replace stale static RP benchmark (`90`) and any PFF-era `yprr` mean with
   cohort means from the regenerated artifact.
4. Bump `CEILING_KNOWN_FACTORS.WR`: **10 → 15**.
5. Confirm artifact load passes through the new numeric factors (existing
   path should suffice).

### Failure behavior

- Loader / NGS / participation failure → leave that factor unset for
  affected players only.
- Never fabricate numeric `0` for rates, catch %, or YPRR.
- Do not block the whole artifact build on one feed failing.

### Explicit non-goals

- `ol_pass_block_rank` / PFF OL
- Renaming factor ids (`reception_perception` → `catch_percentage`, etc.)
- TE `yprr_rank` / contested-catch / separation composites
- Recalibrating existing WR TD / target green-yellow bands beyond cohort
  mean refresh for the new/filled factors

## Test plan

1. YPC / YAC/rec unset when receptions = 0; never invent `0`.
2. `yprr` = yards / `on_pass`; empty participation → unset.
3. NGS empty → `reception_perception` unset; known WR with NGS row →
   catch % present.
4. `target_share` season mean matches weekly fixture.
5. `benchmarks.json`: nonzero WR cohort means for all five factors.
6. Spot-check after R2 + Worker deploy (ARSB, Chase, Nacua, JSN): only
   `ol_pass_block_rank` is `?`; volume shows YPC, YAC/rec, target share;
   YPRR proxy and catch % graded; known count ≈ **15/16**.

## Decisions

- **Approach:** extend existing build pipeline (Approach 1).
- **Reception Perception proxy:** NGS `catch_percentage` (option A).
- **Volume scope:** B + `target_share` (`yards_per_catch`,
  `yac_per_reception`, `target_share`).
- **Ids kept:** `yprr`, `reception_perception` — honesty via labels + source tags.
- **OL pass block:** remains unsourced.

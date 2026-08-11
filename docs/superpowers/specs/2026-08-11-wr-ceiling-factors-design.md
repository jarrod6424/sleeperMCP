# WR ceiling factors: QBR proxy, route participation, secondary target, injury soft-cap

Approved 2026-08-11 (brainstorming). Approach: extend existing
`build_benchmarks.py` / `build_factors.py` → R2 artifacts → DraftLab
(same pattern as ITEM-002).

## Problem

WR ceiling still grades only ~7/12 factors. Elite WRs (Chase, Nacua, St. Brown,
Njigba) are judged mostly on prior-year volume + team pass attempts + injury,
while RB/TE get denser situation/usage signals. Licensed gaps that we can fill
from public nflverse:

| Factor | Position | Was tagged | Fill |
|--------|----------|------------|------|
| `qb_pff_rank` | WR | `licensed:PFF` | ESPN QBR rank of team primary QB (same feed as TE `qb_qbr_rank`) |
| `route_participation` | WR | **not on WR list** | Same participation proxy as TE, extended to WR |
| `secondary_target` | WR | categorical, never sourced | Same-team WR target competition → `less`/`same`/`more` |

Additionally, `injury_concern: serious` grades red (−3). On a 7-factor WR that
single Out week (e.g. Nacua) over-punishes ceiling. Soften **ceiling grading
only**; artifact severity stays truthful.

Out of scope: `yprr`, `ol_pass_block_rank`, `reception_perception`, WR TD /
pass-volume benchmark recalibration, in-season injury overlay.

## Design

### Data flow

```text
nflverse (espn QBR, participation, weekly WR targets)
        │
        ▼
build_benchmarks.py  — cohort means for qb_pff_rank (WR), route_participation (WR)
build_factors.py     — per-WR values + secondary_target categorical
        │
        ▼
artifacts/{benchmarks,player_factors}.json
        │
        ▼
GH Action publish-artifacts → Drake R2 → draftlab-api
```

Ownership unchanged: sleeperMCP measures; DraftLab grades / caps injury.

### Factor definitions

**`qb_pff_rank` (WR)**  
Reuse the TE team-context QBR rank: attach the team primary QB’s ESPN Total
QBR rank for the measured season (`lowerBetter`). Keep the DraftLab factor
**id** `qb_pff_rank` (do not rename to `qb_qbr_rank`); change source tag from
`licensed:PFF` → `nflverse:espn_qbr`. Provenance on trade / no team matches
`off_ppg_rank` (`stale:team_changed`, `missing:no_team_context`). Add to
`TEAM_CONTEXT`. Unqualified / missing → unset, never `0`.

**`route_participation` (WR)**  
Generalize `load_te_route_participation` to WR (or rename to a shared
`load_route_participation(season, position)`):

`100 * (team pass plays where WR GSIS id is in offense_players) /
 (team pass plays in games where that WR was active)`

Same active-game denominator fix as TE. Empty / failed load → unset, not `0`.
Add `route_participation` to WR `FACTORS` and `COMPUTABLE`. DraftLab must add
the factor to the WR benchmark config (new id on WR; TE already has it).

**`secondary_target` (WR)**  
Among same-team WRs in the measured season (weekly stats aggregated targets):

1. Let `player_targets` = this WR’s season targets.
2. Let `secondary_targets` = max season targets among other WRs on that team
   (the highest-targeted teammate). If no teammate WR → unset
   (`missing:no_secondary`).
3. Ratio `r = secondary_targets / player_targets` (if `player_targets == 0`,
   unset).
4. Categorical:
   - `less` if `r < 0.75` (clear WR1, soft competition)
   - `same` if `0.75 <= r < 1.00`
   - `more` if `r >= 1.00` (teammate at or above this WR — committee / WR2+)

Emit `categorical` plus `value: secondary_targets` (season total; matches
existing DraftLab seed shape). Provenance `measured`. Team-change handling:
same as other `TEAM_CONTEXT` factors if this is treated as team context; if
the player changed teams, prefer `stale:team_changed` / withhold rather than
impute current-roster competition from last year’s team.

Source tag: `nflverse` (was bare `categorical`).

### Injury soft-cap (DraftLab only)

In `gradeInjuryConcern` (or a thin wrapper used only by ceiling
`gradeFactor`):

- Artifact still emits `minimal|some|concerned|serious`.
- For **ceiling grade weights**, map `serious` → grade as `concerned`
  (orange / −1).
- Do not change sleeperMCP classification.
- Do not change risk / other consumers unless they already grade injury the
  same path — if they share `gradeInjuryConcern`, either (a) add an option
  `ceilingSoftCapSerious: true` used only from ceiling grading, or (b) cap
  only inside the injury branch when invoked from ceiling. Prefer (a) so
  behavior is explicit.

### Source / COMPUTABLE updates

In `build_benchmarks.py` `FACTORS["WR"]`:

- `qb_pff_rank` → `nflverse:espn_qbr`
- `secondary_target` → `nflverse` (computable categorical emission)
- add `("route_participation", "nflverse:participation")` to the WR list
  (place after `team_pass_attempts` / near situation factors; order should
  match DraftLab WR factor list once updated)

Add `qb_pff_rank` and `route_participation` to `COMPUTABLE`. Ensure
`secondary_target` has an emission path (categorical) so artifacts stop
shipping an unsourced gap note for WRs with teammates.

### Failure behavior

- Loader failure → leave that factor unset for affected players only.
- Never fabricate numeric `0` for ranks or route %.
- Do not block the whole artifact build on one feed failing.

### DraftLab follow-through (required)

1. Add `route_participation` to WR `benchmarks.ts` / artifact-driven config
   (direction `higherBetter`; cohort benchmark from sleeperMCP
   `benchmarks.json`).
2. Confirm `load-artifact.ts` passes through `qb_pff_rank`,
   `route_participation`, and `secondary_target` categorical (injury already
   wired in ITEM-002).
3. Bump `CEILING_KNOWN_FACTORS.WR`: **7 → 10**
   (`qb_pff_rank` + `route_participation` + `secondary_target`; injury and
   archetype already counted in the 7). WR factor **list** grows 12 → 13
   when `route_participation` is added; licensed gaps `yprr` /
   `ol_pass_block_rank` / `reception_perception` remain unknown.
4. Implement serious→concerned ceiling soft-cap as above.
5. Merge / activate new WR cohort benchmarks via existing R2 path.

### Explicit non-goals

- Renaming WR `qb_pff_rank` to `qb_qbr_rank`
- `yprr`, `ol_pass_block_rank`, `reception_perception`
- Recalibrating WR TD / `team_pass_attempts` green/yellow bands
- Changing injury **artifact** severity rules
- New MCP conversational tools

## Test plan

1. QBR empty → WR `qb_pff_rank` absent, not `0`.
2. Known WR on high-QBR team → low `qb_pff_rank`; traded WR →
   `stale:team_changed` / missing team context as appropriate.
3. WR route %: featured WR high vs low-snap WR; empty participation → unset.
4. `secondary_target`: clear WR1 with soft WR2 → `less`; near-equal pair →
   `same` or `more`; lone WR on team → unset.
5. `benchmarks.json`: nonzero WR cohort means for `qb_pff_rank` and
   `route_participation`.
6. DraftLab: `gradeInjuryConcern` / ceiling path maps `serious` → orange
   weight; artifact categorical still `serious` in fixtures.
7. Spot-check after R2: Chase, Nacua, St. Brown, Njigba have the three new
   signals graded; Nacua ceiling no longer injury-red; those WRs remain
   clearly above Taylor / Kyren / Kittle / Kelce on draftScore and look
   stronger on ceiling norm than pre-change.

## Decisions

- **Approach:** extend existing build pipeline (not DraftLab-only tweaks, not
  a second enrichment artifact).
- **Route:** add WR `route_participation` (do not skip).
- **QB quality:** keep id `qb_pff_rank`; fill with ESPN QBR proxy.
- **Secondary bands:** ratio thresholds 0.75 / 1.00 on
  `secondary_targets / player_targets`.
- **Injury:** ceiling soft-cap `serious` → grade as `concerned`; artifact
  unchanged.

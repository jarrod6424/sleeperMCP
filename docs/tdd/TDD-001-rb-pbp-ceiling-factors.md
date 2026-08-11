# TDD-001: RB play-by-play ceiling factors

**Item:** ITEM-001  **Status:** Done  **Date:** 2026-08-10
**Completed:** 2026-08-11

## Problem / motivation

`computeCeilingScore` grades RB against 12 factors. Three —
`rz_touch_share`, `gl_carry_share`, `neutral_run_rate` — previously had
`benchmark: 0` in DraftLab's `benchmarks.ts`, which made `gradeByRatio`
return `'unknown'` unconditionally (`if (benchmark === 0) return 'unknown'`),
for every RB, regardless of any data fed in. No video data exists for these
three; they were always meant to come from play-by-play (`benchmarks.ts`
comment, `build_benchmarks.py` `FACTORS["RB"]` tags all three
`nflverse:pbp`).

## Shipped outcomes (2026-08-11)

| Factor | half-PPR cohort benchmark | relative SE | DraftLab `benchmarks.ts` |
|--------|---------------------------|-------------|--------------------------|
| `rz_touch_share` | 0.400 | 2.7% | 0.4 |
| `gl_carry_share` | 0.664 | 3.5% | 0.664 |
| `neutral_run_rate` | 0.435 | 2.0% | 0.435 |

Per-player coverage in `player_factors.json` (2026 ADP universe): ~58/68 RBs
measured for RZ/GL share, 59/68 for neutral run rate. Directional sanity:
Bijan Robinson rz=0.485 / gl=0.500 vs Tyjae Spears rz=0.183 / gl=0.200.
`CEILING_KNOWN_FACTORS.RB` updated 7 → 10.

## Scope

**In scope:**
- A new `load_rb_pbp_season()` in `build_benchmarks.py`, mirroring
  `load_qb_pbp_season()` (line 349), computing per-player red-zone touches,
  goal-line carries, and per-team neutral-script run rate from one season's
  play-by-play.
- Wiring that output into `load_player_seasons()`'s `agg` rows, the same way
  `load_qb_pbp_season()`'s output is merged in today (lines 512–527) — this
  is the single change point, since `build_factors.py` imports
  `load_player_seasons` from `build_benchmarks.py` and gets the new values
  for free.
- Adding `rz_touch_share`, `gl_carry_share`, `neutral_run_rate` to
  `COMPUTABLE` in `build_benchmarks.py`.
- Adding the same three to `TEAM_CONTEXT` in `build_factors.py` — resolved,
  see Decisions.
- Computing real cohort benchmarks for the three (replacing `benchmark: 0`)
  using `build_benchmarks.py`'s own top-N-by-points cohort method — resolved,
  see Decisions; not FSE's video method.
- Updating DraftLab's `benchmarks.ts` RB block with the resulting numbers.
- Regenerating `artifacts/benchmarks.json` and `artifacts/player_factors.json`.

**Out of scope:**
- The 12 licensed-data gaps (PFF/ESPN/FTN/RP factors across all positions) —
  blocked on a license, not an engineering task. See `docs/SAD.md` Ceiling
  Factor Coverage.
- Any other position's factors.
- Changes to `evaluate.ts`, `draft-score.ts`, grading bands, or weights.
- ADP/market factors — separately sourced already.

## Data source: play-by-play definitions (resolved)

- **`rz_touch_share`** — player's (rush attempts + targets) with
  `yardline_100 <= 20`, divided by the **team's** total red-zone touches
  for the same games — every offensive position, not RB-only (an RB-only
  denominator is trivially ~100% for any team with one healthy back and
  wouldn't discriminate between players; the fantasy-relevant question is
  whether the offense features this back near the goal line at all).
  Team denominator = team red-zone rush attempts (all positions, including
  QB — see below) + team red-zone targets (all positions). Implementer
  note: target attribution needs the pbp receiver field (this file has no
  existing precedent for parsing it — `load_qb_pbp_season` only reads
  passer/rusher names — verify the exact nflverse column name, likely
  `receiver_player_name`, against the schema the same way `goal_to_go` was
  verified below, rather than assume).
- **`gl_carry_share`** — player's rush attempts where `goal_to_go == 1`,
  divided by the team's total rush attempts where `goal_to_go == 1` — same
  all-positions team denominator as above. **`goal_to_go` is a confirmed
  real column** in nflfastR's released pbp schema
  (`data-raw/pbp_datatypes.csv`, numeric) — the same file
  `nflverse_csv("pbp", f"play_by_play_{season}.csv", ...)` already fetches.
  Use it directly; no yardline threshold needed, and none should be
  invented now that the source's own flag is available.
- **`neutral_run_rate`** — **team-level**, not player-level: team run plays
  ÷ team total offensive plays, restricted to `_neutral_script(row)` plays
  (the exact predicate `load_qb_pbp_season` already uses for
  `team_neutral_plays`, line 396). Attached to the RB the same way
  `off_ppg_rank` is — describes the offense, not the individual.

**QB carries count in both team denominators above** — a QB goal-line sneak
or red-zone keeper is real competition for that touch, not a different
category of play. This is a deliberate choice, and it means a team with a
rushing QB will structurally show lower `gl_carry_share`/`rz_touch_share`
for its RB1 than an otherwise-identical team without one — documented
behavior, not a bug, should a future reader wonder why two similar backs
score differently.

## Provenance

All three follow the existing pattern: `measured` when the season's pbp
computes cleanly, factor left **unset** (not a fabricated `0`) on a
best-effort pbp fetch failure — matching `load_qb_pbp_season`'s own
documented behavior ("returns `({}, {})` on any failure ... leaves these
three factors unset rather than fabricate zeros"). Added to `TEAM_CONTEXT`,
so a traded RB gets `stale:team_changed` or `missing:no_team_context` for
all three, exactly as `off_ppg_rank` does today.

## Test plan

1. Best-effort failure: pbp fetch returns empty → all three factors absent
   from a player's `factors` dict, not present with value `0`.
2. Team-context correctness: a player flagged `team_changed: true` gets
   `stale:team_changed` on all three; a `recovered_from_season` player gets
   `missing:no_team_context` — same assertions the project already runs for
   `off_ppg_rank`, extended to these three.
3. `goal_to_go` sanity check: pull one known short-yardage back and confirm
   `gl_carry_share` reflects plays actually flagged `goal_to_go == 1` in the
   raw file, not an approximation.
4. A known high-volume RB's `rz_touch_share`/`gl_carry_share` come out
   directionally sane (majority share for a bell-cow back, visibly
   suppressed for a committee back or a rushing-QB offense) — sanity check,
   not a golden value, since no verified ground truth exists yet for these
   three (unlike QB's pbp factors, which had DraftLab's own published
   numbers to calibrate against).
5. `benchmarks.json`: RB's three pbp factors get real nonzero values:
   `provisional` stays `false`, and the RB block's `factors` array no longer
   contains a zero benchmark for these IDs.
6. Golden regression: extend `tests/test_golden.py` RB fixtures. Only
   recapture after step 4's sanity check passes — never to make an
   unexplained diff disappear.

## Decisions (2026-08-10)

**Cohort method: top-N-by-points, not FSE's "40 league winners" video
study.** Chosen because it's mechanically reproducible and lives in code
(`build_benchmarks.py`'s existing calibration path) — the FSE method,
whatever its original rigor, can't be independently regenerated or audited
from this repo. Known consequence: RB's benchmark set becomes
mixed-provenance — 9 factors FSE-video-sourced, 3 factors top-N-computed —
unless the other 9 are ever recalibrated to match. This is an accepted
tradeoff, not an oversight.

Lower-risk than it might sound: `benchmarks.json`'s own
`reference_comparison` already cross-validates 6 of RB's FSE-sourced
factors against this same top-N method, and they agree closely —
0.5%–8.8% error (touches 0.5%, rush_attempts 1.2%, targets 8.5%,
receptions 8.8%, touchdowns 4.6%, off_ppg_rank 4.6%). By contrast, QB's own
cross-validation on `off_ppg_rank` was 33.7% off. The two cohort
definitions have historically agreed well for RB specifically, on volume
stats. Situational rate stats (which these three are) weren't part of that
comparison and could behave differently — worth watching in step 4 of the
test plan, not assumed away.

**Team-share framing, all positions, QB carries included** — see Data
source section above; folded in rather than left as open questions.

**`goal_to_go` confirmed present** in nflfastR's released pbp schema
(`data-raw/pbp_datatypes.csv`) via direct check against the source, not
assumed from general nflverse familiarity.

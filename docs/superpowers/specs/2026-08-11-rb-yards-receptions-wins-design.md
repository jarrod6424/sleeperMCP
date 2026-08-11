# RB receptions, yards efficiency, and team wins factors

Approved 2026-08-11. Fill DraftLab RB ceiling gaps that were always
derivable from nflverse but never emitted.

## Problem

RB factor breakdown shows `?` for `receptions`, `yards_per_carry`,
`yards_per_touch`, and `team_wins` — only `ol_run_block_rank` remains a
licensed gap. `per_game()` already computed receptions; yardage and team
wins were never aggregated.

## Design

| Factor | Source | Computation |
|--------|--------|-------------|
| `receptions` | nflverse weekly stats | season receptions / games |
| `yards_per_carry` | nflverse | `rushing_yards / carries` (if carries > 0) |
| `yards_per_touch` | nflverse | `(rush_yd + rec_yd) / (carries + receptions)` |
| `team_wins` | nflverse `schedules/games.csv` | REG wins for player's team |

- Add to `FACTORS["RB"]`, `COMPUTABLE`, `TEAM_CONTEXT` (`team_wins` only)
- Never fabricate 0 on loader failure
- DraftLab `CEILING_KNOWN_FACTORS.RB`: 11 → 15

## Decisions

- Efficiency rates use season totals, not per-game division
- Ties / missing scores skipped in team wins
- `ol_pass_block_rank` / PFF OL still out of scope

# Changelog

## 2026-10-15 — Draft picks use FantasyCalc's own pick prices

### Fixed

- `analyze_trade` was pricing dynasty picks as the average FantasyCalc value of veteran players at the same startup-draft slot. On a 12-team superflex board that made a late 2027 1st the mean of overall ranks 4–8 (8364), times 0.82 (**6858**), and a late 2027 2nd the mean of ranks 16–20 (6037), times 0.82 (**4950**). FantasyCalc's own pick rows are **2560** (late 2027 1st, overall rank 86) and **1463** (late 2027 2nd, overall rank 146).
- The schedule model now uses the `position: PICK` row for that season, round, and early/mid/late tier. The quote is not multiplied by the slot factor again. A year FantasyCalc publishes as a single price (for example `2028 1st`) uses that price with `slot_matched: false`. No matching row falls back to the static Superflex/1QB table (`static_schedule`), which is still a heuristic.

### Not changed

- Player-only trades still use FantasyCalc player values.
- `pick_value_model=manual` still replaces listed tokens with `pick_overrides`.
- `grade_team` pick capital still uses the static mid-round table. Its note no longer claims that table is what `analyze_trade` uses.

## 2026-09-17 — Week usage from nflverse

### Added

- `get_week_usage` — one NFL week's offensive usage grouped by team: snap share, rushes, targets, target share, team dropback rate from play-by-play, and a route proxy (on-field for dropbacks from nflverse participation, not PFF). QBs get dropback share, designed vs scramble rushes, and starter/backup snap or dropback split headlines when two QBs both clear 15%. Headlines also surface patterns like `PHI: 2 WRs over 90% snap share`. Logic lives in `sleeper_core/usage.py`; `server.py` is a thin wrapper.

### Notes

- Rates are 0–100 percentages. A missing source row is `null` with `missing:not_recorded`, never a fabricated zero.
- Snap counts are joined to weekly stats via nflverse roster PFR/GSIS ids (snap "Zonovan Knight" is stats "Bam Knight", not a committee).
- PBP and participation are filtered during parse so the season files cannot OOM the host.

## 2026-09-03 — P2 richer start/sit

### Added

- `start_sit_advice` now attaches `reasons` and `reason_codes` to every suggested swap (`higher_projection`, `injury_risk`, `favorable_matchup`, `negative_game_script_risk`, plus `higher_floor` / `superflex_qb_slot` when they apply).
- `strategy` argument: `balanced` (default), `floor` (injury haircut + optional 10-PPR hit rate for close calls), `ceiling` (raw projection). Does not fan out `custom_score_player`.
- Empty weekly projections return a structured error with `fallback.guidance` (`get_player_stats` / `get_snap_counts` / `get_injuries`) instead of inventing a lineup.
- Superflex still fills `SUPER_FLEX` with a second QB when that is the greedy projection play; that swap is tagged `superflex_qb_slot`.

### Changed

- Player-only fields (`current_projected`, `consider_starting`, `consider_benching`, `optimal_lineup`, `potential_point_gain`) are unchanged in name. The advice envelope (`verdict`, `reasons`, `data_sources`, `limitations`, `as_of`, `subject`) is added alongside them.
- Matchup / game-script codes come from nflverse `games.csv` spreads when a row exists. No spread → those codes are omitted, not invented. No new paid APIs.

### Not in this release

- FantasyPros projection overlay (P3)
- Write actions

## 2026-09-03 — P0/P1 advice tools

### Added

- `waiver_advice` — ranked waiver/FAAB claims with roster-need scores, dynasty vs redraft weights, drop suggestions, and heuristic FAAB bands. Prefer this over `get_available_players` when you want a recommendation; the latter remains the raw wire.
- `grade_team` — contender / rebuilder classification, positional letter grades, and 1–3 next moves.
- Shared advice envelope (`verdict`, `reasons`, `data_sources`, `limitations`, `as_of`, `subject`) in `sleeper_core/advice.py`.

### Changed

- `analyze_trade` now prices draft-pick tokens (`2027 1st`, `2027 Round 1`, `2027 1st from TEAM`, `2027 1st (roster 11)`). Player-only field names (`give`, `get`, `give_total`, `verdict`, …) are unchanged. Picks use the in-repo schedule curve in `sleeper_core/picks.py` (static Superflex/1QB table; FantasyCalc rank-band means when the board is dense). Anything that cannot be parsed or priced lands in `unpriced_assets` — never invented.
- Yahoo `analyze_trade` accepts the same pick tokens but lists them in `unpriced_assets` with `yahoo_picks_unsupported` (no Sleeper-style pick feed).
- `get_available_players` is unchanged in output; implementation moved to `sleeper_core.league.list_free_agents`.

### Pick curve constants (schedule fallback)

Documented in `sleeper_core/picks.py`. Superflex mid 1st = 2800, mid 2nd = 1100. 1QB mid 1st = 2100. Slot multipliers: early 1.20 / mid 1.00 / late 0.82.

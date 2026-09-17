# Changelog

## 2026-09-17 — Week usage from nflverse

### Added

- `get_week_usage` — one NFL week's offensive usage grouped by team: snap share, rushes, targets, target share, team dropback rate from play-by-play, and a route proxy (on-field for dropbacks from nflverse participation, not PFF). Headlines surface patterns like `PHI: 2 WRs over 90% snap share`. Logic lives in `sleeper_core/usage.py`; `server.py` is a thin wrapper.

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

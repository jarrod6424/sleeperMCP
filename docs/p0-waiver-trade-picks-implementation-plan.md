# P0 — Waiver advice + pick-aware trades

**Status:** Implemented (P0 + P1 grade_team / envelope)  
**Date:** 2026-09-03  
**FRD:** Feature requirements doc (2026-09-03)  
**Scope this plan:** F1 `waiver_advice`, F2 pick-aware `analyze_trade`, plus P1 `grade_team` and shared `AdviceEnvelope`. F4b start/sit reasons shipped in the same PR (see CHANGELOG P2); F5 FantasyPros overlay remains out of scope.

## Decisions (FRD open items)

### Pick curve: hybrid (A) + (B)

- **(A) Static in-repo Superflex / 1QB tables** used when FantasyCalc has no usable rank band.
- **(B) Rank-band means from the league's FantasyCalc board** when enough players have `overallRank` (or value-rank fallback). Superflex vs 1QB is then encoded in the board itself (`numQbs` on the FantasyCalc request) — no extra SF multiplier on top of (B), which would double-count.
- **Not (C):** no KeepTradeCut scrape, no extra HTTP.

Fallback constants (FantasyCalc-like points, 12-team dynasty) are documented in `sleeper_core/picks.py`:

| Round | SF early / mid / late | 1QB early / mid / late |
| --- | --- | --- |
| 1 | 3360 / **2800** / 2296 | 2520 / **2100** / 1722 |
| 2 | 1344 / **1100** / 858 | 1008 / 825 / 644 |
| 3 | 560 / 420 / 336 | 420 / 315 / 252 |
| 4 | 252 / 196 / 140 | 189 / 147 / 105 |
| 5+ | decaying ~0.55× prior mid | same decay |

Mid-1st 2800 and mid-2nd 1100 match the FRD example tokens. Slot multipliers: early 1.20, mid 1.00, late 0.82. `slot_estimate=auto` maps standings thirds to early / mid / late when the pick's original roster is known; otherwise mid.

`manual` model: `pick_overrides` supplies values for those tokens only; everything else still uses `schedule`.

### Architecture

Logic lives in `sleeper_core` (no MCP imports). `server.py` stays a thin wrapper.

| Module | Role |
| --- | --- |
| `sleeper_core/advice.py` | Shared envelope (`verdict`, `reasons`, `data_sources`, `limitations`, `as_of`, …) |
| `sleeper_core/picks.py` | Token parse + schedule valuation |
| `sleeper_core/trade.py` | Pick-aware `analyze_trade` (existing field names preserved) |
| `sleeper_core/waiver.py` | `waiver_advice` scoring |
| `sleeper_core/grade.py` | `grade_team` classification |
| `sleeper_core/league.py` | `list_free_agents` extracted from `server.py` (same output) |
| `sleeper_core/start_sit.py` | P2 richer start/sit (reasons, strategy, projection fallback) |

Yahoo: best-effort waivers/grades via existing roster + FA + sleeper_id crosswalk. Yahoo pick pricing always returns `unpriced_assets` with `yahoo_picks_unsupported` — never invented numbers.

### Cache / performance

No new HTTP clients. Reuse:

- FantasyCalc disk cache (`FC_CACHE_TTL` = 6h)
- Projections disk cache (`PROJ_CACHE_TTL` = 6h)
- Player map (`PLAYER_CACHE_TTL` = 18h)
- In-process Sleeper JSON (`MEM_TTL` = 1h)

Waiver situation uses Sleeper player-map fields (`injury_status`, `depth_chart_order`, `team`) rather than per-player nflverse fetches, so a 12-team call stays inside one FC pull + one FA scan + one trending pull + one projections pull.

## Testable tasks

1. **Pick parser** — tokens in the FRD, plus invalid → unpriced, not crash.
2. **Pick curve** — Superflex 1st > identical 1QB 1st on both static fallback and FC-band paths.
3. **Trade regression** — player-only `give`/`get`/`give_total`/`verdict` still present; totals exclude nothing that was priced before.
4. **Trade + pick** — `give=["2027 1st"], get=["Marvin Harrison"]` produces numeric sides or explicit unpriced + non-zero player side.
5. **Waiver weights** — dynasty `trade_value` weight > `projection` weight; redraft flips that.
6. **Waiver safety** — never add a rostered player; drops prefer taxi/bench; elite drops flagged `risk: high`.
7. **Grade** — every team in a 12-team fixture gets a classification; `next_moves` length 1–3 with reasons.
8. **Yahoo picks** — unsupported → `unpriced_assets`, not fake values.

## Non-goals

- FlexFantasy / Scoreline
- Write actions
- FantasyPros overlay (P3)

P2 richer `start_sit_advice` (reason codes, `strategy`, projection-failure fallback) is implemented in `sleeper_core/start_sit.py`.

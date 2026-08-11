# sleeperMCP — Backlog

Tracked via the spec-driven lifecycle. Status moves
Backlog -> In Design -> In Review -> Done.

## Backlog

| ID | Title | Status | Notes |
|---|---|---|---|
| ITEM-001 | RB play-by-play ceiling factors | Done | `rz_touch_share` / `gl_carry_share` / `neutral_run_rate` from nflverse pbp; artifacts regenerated 2026-08-11; DraftLab `benchmarks.ts` half-PPR means 0.4 / 0.664 / 0.435; `CEILING_KNOWN_FACTORS.RB` 7→10 — see TDD-001 |
| ITEM-002 | QBR, TE route participation, and injury concern factors | Done | `qbr_rank` / `qb_qbr_rank` from nflverse ESPN QBR, TE `route_participation` from nflverse participation (FTN attribution), and `injury_concern` categorical from nflverse injuries; artifacts regenerated 2026-08-11 — see `docs/superpowers/specs/2026-08-11-qbr-route-injury-factors-design.md` |

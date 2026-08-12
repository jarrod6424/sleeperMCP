# sleeperMCP — Backlog

Tracked via the spec-driven lifecycle. Status moves
Backlog -> In Design -> In Review -> Done.

## Backlog

| ID | Title | Status | Notes |
|---|---|---|---|
| ITEM-001 | RB play-by-play ceiling factors | Done | `rz_touch_share` / `gl_carry_share` / `neutral_run_rate` from nflverse pbp; artifacts regenerated 2026-08-11; DraftLab `benchmarks.ts` half-PPR means 0.4 / 0.664 / 0.435; `CEILING_KNOWN_FACTORS.RB` 7→10 — see TDD-001 |
| ITEM-002 | QBR, TE route participation, and injury concern factors | Done | `qbr_rank` / `qb_qbr_rank` from nflverse ESPN QBR, TE `route_participation` from nflverse participation (FTN attribution), and `injury_concern` categorical from nflverse injuries; artifacts regenerated 2026-08-11 — see `docs/superpowers/specs/2026-08-11-qbr-route-injury-factors-design.md` |
| ITEM-003 | WR ceiling factors | Done | WR `qb_pff_rank` from nflverse ESPN QBR, `route_participation` from nflverse participation (FTN attribution), and `secondary_target` from nflverse receiving data; artifacts regenerated and published to R2 2026-08-11; DraftLab evaluation-engine spot-check passed with all WR factors graded and Puka Nacua injury ceiling orange/−1 — see `docs/superpowers/specs/2026-08-11-wr-ceiling-factors-design.md` |
| ITEM-004 | RB receptions, YPC, YPT, team wins | Done | `receptions`, `yards_per_carry`, `yards_per_touch` from nflverse weekly stats; `team_wins` from nflverse schedules; artifacts published to R2 2026-08-11; DraftLab `CEILING_KNOWN_FACTORS.RB` 11→15; Worker deployed — Gibbs spot-check 15/16 known (only `ol_run_block_rank` unsourced) — see `docs/superpowers/specs/2026-08-11-rb-yards-receptions-wins-design.md` |
| ITEM-005 | WR YPRR proxy, NGS catch %, volume | Done | `yards_per_catch`, `yac_per_reception`, `target_share` from nflverse weekly stats; `yprr` from nflverse participation (yards / on_pass); `reception_perception` from nflverse NGS catch %; artifacts published to R2 2026-08-11; DraftLab `CEILING_KNOWN_FACTORS.WR` 10→15; Worker deployed — ARSB spot-check 15/16 known (only `ol_pass_block_rank` unsourced), ceiling 41 — see `docs/superpowers/specs/2026-08-11-wr-yprr-catch-volume-design.md` |
| ITEM-006 | OL block-rank proxies, shared pace/injury, QB ADP out of ceiling | Done | nflverse pressure/stuff OL proxies; WR/TE `neutral_pace_rank`; DraftLab QB `injury_concern`, ceiling `adp` removed; half-PPR means QB OL 11.485 / RB OL 12.97 / WR OL+pace 13.697/15.727 / TE OL+pace 14.667/14.667; `CEILING_KNOWN_FACTORS` QB 11 RB 16 TE 12 WR 17; R2 run [31557985600](https://github.com/jarrod6424/sleeperMCP/actions/runs/31557985600); Worker `d343edce`; spot-check Allen 11/12 Gibbs 16/16 ARSB 17/17 Bowers 12/14 — see `docs/superpowers/specs/2026-08-11-ol-proxy-shared-factors-design.md` |

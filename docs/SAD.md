# sleeperMCP — System Architecture (live state)

Reflects what's on `main`. Update via `sad-maintainer` after every merge.

## Layout

server.py (36 @mcp.tool() wrappers) -> sleeper_core (data layer, no MCP
imports) -> tools/ (build_benchmarks.py: cohort benchmarks;
build_factors.py: per-player values) -> artifacts/*.json (consumed by
DraftLab).

## Ownership boundary with DraftLab

this side   what the numbers ARE   factors, benchmarks, the ID crosswalk
that side   what they MEAN         grading, archetypes, risk, scoring

## Ceiling factor coverage (verified 2026-08-11, against build_benchmarks.py
## FACTORS/COMPUTABLE and DraftLab's benchmarks.ts)

All volume factors (targets, receptions, touchdowns, carries, pass
attempts, touches) are COMPUTABLE for all four positions today.

RB play-by-play ceiling factors are also COMPUTABLE (ITEM-001 / TDD-001):
`rz_touch_share`, `gl_carry_share`, `neutral_run_rate` — cohort half-PPR
benchmarks 0.4 / 0.664 / 0.435 (relative SE 2.0–3.5%). DraftLab
`CEILING_KNOWN_FACTORS.RB` is 10/16.

Remaining gaps are blocked on licensed data, not an engineering gap:

**Blocked — licensed data:**
- QB: ol_pass_block_rank (PFF), qbr_rank (ESPN), pass_dvoa_rank (FTN)
- RB: ol_run_block_rank (PFF)
- WR: qb_pff_rank, ol_pass_block_rank, yprr (PFF); reception_perception (RP)
- TE: qb_qbr_rank (ESPN); route_participation, inline_pct, yprr_rank (PFF)

Every blocked factor already has a real DraftLab benchmark waiting; only
the per-player input is missing, and no pipeline work closes that without
a license.

Also still unsourced on the DraftLab side (not sleeperMCP buildables):
QB `adp` as a ceiling factor input, WR `secondary_target` categorical,
injury_concern, and DraftLab-only RB extras (receptions / YPC / YPT /
team_wins) that this repo never emits.

## MCP tool surface

36 tools in server.py — authoritative source, not duplicated here.

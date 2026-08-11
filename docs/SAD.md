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

ITEM-002 makes the following factors COMPUTABLE from public nflverse feeds:

| Position | Factor | Source |
|---|---|---|
| QB | `qbr_rank` | ESPN QBR via nflverse |
| TE | `qb_qbr_rank` | Primary team QB's ESPN QBR via nflverse |
| TE | `route_participation` | nflverse participation data (FTN attribution, CC-BY-SA) |
| QB/RB/WR/TE | `injury_concern` categorical | nflverse weekly injuries |

ITEM-003 adds public nflverse sources for WR ceiling factors:

| Position | Factor | Source |
|---|---|---|
| WR | `qb_pff_rank` | Primary team QB's ESPN QBR rank via nflverse |
| WR | `route_participation` | nflverse participation data (FTN attribution, CC-BY-SA) |
| WR | `secondary_target` categorical | nflverse receiving data |

Route participation is now sourced for both WR and TE.

ITEM-004 adds public nflverse sources for RB ceiling factors:

| Position | Factor | Source |
|---|---|---|
| RB | `receptions` | nflverse weekly stats |
| RB | `yards_per_carry` | nflverse (`rushing_yards / carries`) |
| RB | `yards_per_touch` | nflverse (total yards / touches) |
| RB | `team_wins` | nflverse `schedules/games.csv` |

Remaining gaps are blocked on licensed data, not an engineering gap:

**Blocked — licensed data:**
- QB: ol_pass_block_rank (PFF), pass_dvoa_rank (FTN)
- RB: ol_run_block_rank (PFF)
- WR: ol_pass_block_rank, yprr (PFF); reception_perception (RP)
- TE: inline_pct, yprr_rank (PFF)

Every blocked factor already has a real DraftLab benchmark waiting; only
the per-player input is missing, and no pipeline work closes that without
a license.

Also still unsourced on the DraftLab side (not sleeperMCP buildables):
QB `adp` as a ceiling factor input.

## MCP tool surface

36 tools in server.py — authoritative source, not duplicated here.

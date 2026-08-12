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

ITEM-005 adds public nflverse sources for WR ceiling factors:

| Position | Factor | Source |
|---|---|---|
| WR | `yards_per_catch` | nflverse weekly stats (`receiving_yards / receptions`) |
| WR | `yac_per_reception` | nflverse weekly stats (`receiving_yards_after_catch / receptions`) |
| WR | `target_share` | nflverse weekly stats (mean weekly target share) |
| WR | `yprr` | nflverse participation data (`receiving_yards / on_pass` routes; FTN attribution, CC-BY-SA) |
| WR | `reception_perception` | nflverse Next Gen Stats receiving (`catch_percentage`, week 0) |
| TE | `yprr` | nflverse participation data (`receiving_yards / on_pass` routes; FTN attribution, CC-BY-SA) |

ITEM-006 replaces the previously licensed OL rank gaps with public,
team-level nflverse play-by-play outcome proxies:

| Position | Factor | Source |
|---|---|---|
| QB/WR/TE | `ol_pass_block_rank` | Pressure-rate rank (lowest rate = 1), `nflverse:pbp:proxy` |
| RB | `ol_run_block_rank` | Stuff-rate rank (lowest rate = 1), `nflverse:pbp:proxy` |
| WR/TE | `neutral_pace_rank` | Team neutral-pace rank from nflverse play-by-play |
| QB | `pass_epa_rank` | Team pass EPA mean rank from nflverse play-by-play (`nflverse:pbp:proxy`) |

These are team outcome proxies, not PFF film grades. They are measured in
the generated artifacts and explicitly labelled as proxies downstream.

All ceiling factors are now COMPUTABLE from public nflverse sources; no
licensed ceiling gaps remain.

QB `adp` has been removed from ceiling factors to avoid double-counting the
market signal already used by ValueScore. The MCP `get_adp` tool and player
market fields remain unchanged.

## MCP tool surface

36 tools in server.py — authoritative source, not duplicated here.

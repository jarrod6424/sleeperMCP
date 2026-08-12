# OL block-rank proxies, shared situational factors, and QB ADP removal

Approved 2026-08-11 (brainstorming). Approach A (tight share) + OL
proxy approach 1 (pressure / stuff rates). Same ownership pattern as
ITEM-003–005: sleeperMCP measures → artifacts → R2 → DraftLab grades.

## Problem

QB / WR / RB ceiling strips still show `?` for licensed PFF OL ranks.
QB ceiling double-counts market via `adp` (also in ValueScore) while
other positions do not. High-signal team factors that already exist in
the pipeline are position-gated: `injury_concern` missing from DraftLab
QB list; `neutral_pace_rank` only on QB; TE has no OL pass factor.

## Scope (Approach A)

| Change | Positions | Action |
|--------|-----------|--------|
| Remove `adp` | QB | Drop from ceiling factors only; ValueScore / `get_adp` unchanged |
| `ol_pass_block_rank` | QB, WR, **+TE** | Fill via team pressure-rate rank proxy |
| `ol_run_block_rank` | RB | Fill via team stuff-rate rank proxy |
| `injury_concern` | **+QB** (DraftLab) | Already in sleeperMCP `FACTORS["QB"]`; add to DraftLab QB factor list |
| `neutral_pace_rank` | **+WR, +TE** | Same team pace rank already computed for QB |

**Out of scope:** `team_wins` on WR/TE; unifying WR `team_pass_attempts` /
TE `team_pass_att_rank`; PFF license; TE `inline_pct` / `yprr_rank`;
`pass_dvoa_rank` (FTN).

## Design

### Data flow

```text
nflverse play_by_play_{season}.csv
        │
        ▼
load_ol_proxy_season(season)   — preferably one pbp pass shared with
                                 existing QB/RB loaders when practical
  pass: pressure_rate by posteam → 1–32 rank (lowest rate = 1)
  run:  stuff_rate by posteam    → 1–32 rank (lowest rate = 1)
        │
        ▼
Attach team ranks (same pattern as off_ppg_rank / neutral_pace_rank)
  ol_pass_block_rank → every QB, WR, TE on that team
  ol_run_block_rank  → every RB on that team
  neutral_pace_rank  → WR, TE (already on QB)
        │
        ▼
build_benchmarks.py / build_factors.py
        │
        ▼
artifacts/{benchmarks,player_factors}.json → R2 → draftlab-api
```

### OL proxy definitions

**Pass (`ol_pass_block_rank`) — keep id**  
Regular-season only. For each `posteam`:

```text
dropbacks   = plays where pass_attempt == 1 OR sack == 1
pressured   = sack == 1 OR qb_hit == 1 OR qb_scramble == 1
pressure_rate = pressured / dropbacks
```

Rank teams by ascending pressure rate (best protection = 1).  
Source tag: `licensed:PFF` → `nflverse:pbp:proxy`.  
DraftLab label: “OL pass block rank (proxy)”.  
Unset if REG pbp unavailable — never fabricate `0`.

**Run (`ol_run_block_rank`) — keep id**  
Regular-season only. For each `posteam`:

```text
stuff_rate = count(rush_attempt == 1 AND rushing_yards <= 0)
             / count(rush_attempt == 1)
```

Rank teams by ascending stuff rate (best run blocking = 1).  
Same provenance and unset contract as pass proxy.  
Label: “OL run block rank (proxy)”.

These are **team-level outcome proxies**, not PFF film grades. Confounders
(QB hold time / mobility; RB talent; scheme) are accepted; label honesty
matches prior proxies (`qb_pff_rank` ← ESPN QBR, `yprr` ← participation).

### Shared attaches

**`injury_concern` on QB**  
Reuse existing `load_injury_concern_season` / categorical grading. No new
loader. Add to DraftLab `benchmarks.ts` QB factors; sleeperMCP already
lists it under `FACTORS["QB"]`.

**`neutral_pace_rank` on WR / TE**  
Reuse ranks from `neutral_pace_ranks(team_neutral_plays)` already produced
by `load_qb_pbp_season`. Attach by player team for WR/TE season rows.
Add to `FACTORS["WR"]` / `FACTORS["TE"]`, `COMPUTABLE`, and DraftLab lists.
Direction: `lowerBetter` (1 = fastest). Category: situational.

### ADP removal

- Remove `adp` from sleeperMCP `FACTORS["QB"]` and DraftLab QB
  `benchmarks.ts`.
- Remove `excludeAdp` option from `ceiling.ts` (dead once factor gone).
- ValueScore continues to use `adpRoundPick`; MCP `get_adp` unchanged.
- Update seeds / spot-check fixtures that inject QB `adp` as a ceiling
  factor.

### Benchmarks (top-N for all positions)

All numeric benchmarks — including new OL proxy ranks and WR/TE
`neutral_pace_rank` — use the existing `build_benchmarks.py` rule:
**top `cohort` (default 3) by fantasy points per position per season**,
then mean across seasons / format. Not RB-only; not FSE video means.

Replace PFF-era static OL means (QB 11.54, WR 10.75, RB 9.9) with
cohort-computed proxy means. Report relative SE in the artifact /
spread output. Categorical `injury_concern` is not cohort-benchmarked.

### DraftLab factor counts (target after ship)

| Pos | List change | Known / total |
|-----|-------------|-----------------|
| QB | −`adp`, +`injury_concern` (DraftLab), OL filled | **11/12** (`pass_dvoa_rank` still FTN) |
| WR | +`neutral_pace_rank`, OL filled | **17/17** |
| TE | +`ol_pass_block_rank`, +`neutral_pace_rank` | **12/14** (`inline_pct`, `yprr_rank` still PFF) |
| RB | OL filled | **16/16** |

Update `CEILING_KNOWN_FACTORS` accordingly once live coverage confirms.

### Docs

- `docs/PLAN.md` — ITEM-006 row (In Design → Done on ship)
- `docs/SAD.md` — remove OL ranks from licensed blocked list; note proxies;
  keep FTN DVOA + TE PFF gaps; note QB ADP no longer a ceiling factor

## Acceptance

1. Half-PPR cohort means exist for `ol_pass_block_rank` (QB/WR/TE),
   `ol_run_block_rank` (RB), and `neutral_pace_rank` (WR/TE) with SE
   reported.
2. Same-team skill players share identical OL pass and pace ranks for a
   given season.
3. Live QB ceiling strip has no `adp`; `injury_concern` grades; OL not `?`.
4. Spot-checks: elite QB / ARSB / Gibbs show full known except QB DVOA;
   TE still shows two licensed `?`s.
5. R2 publish + Worker redeploy; live Worker reflects new artifacts.

## Non-goals

- Opponent-adjusted pressure/stuff rates
- Sack-only or rush-EPA-only variants (unless acceptance shows proxy is
  unusable — then reopen)
- Sharing `team_wins` or unifying pass-attempt factor shapes
- Licensing PFF / FTN

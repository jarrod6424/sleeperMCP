# Ceiling factor proxies + board display

Approved intent 2026-08-12 (brainstorming). Related product fixes in one
spec:

1. **Fill or drop** the three always-blocked licensed ceiling factors.
2. **Fix ceiling UI** — stop showing a fake `/60` denominator; show raw score;
   green when top-5 at position.
3. **Show DraftScore** as an explicit column on the player board (today it only
   drives default sort / tiers / rank, so the composite is invisible).
4. **Summary hovers** on SCORE / CEILING / ARCHETYPE explaining how that
   player’s value was built (not a full player-detail dump).
5. **Column-header hovers** describing what each board column is for.
6. **CONF / factor-strip denominators** use the position’s real catalog size
   (not a hardcoded 12).

## Problem

### Licensed / empty factors

Three ceiling factors never fill in production:

| Factor | Position | Blocker |
|--------|----------|---------|
| `pass_dvoa_rank` | QB | FTN DVOA (not redistributable) |
| `inline_pct` | TE | PFF charting (not redistributable) |
| `yprr_rank` | TE | PFF YPRR rank (not redistributable) |

They permanently show as `?` / `unknown`, inflate catalog denominators, and
create fake “coverage gaps” in docs and UI (TE detail still shows an in-line
check that always reads `—`).

WR already solved the YPRR case with a participation proxy
(`receiving_yards / on_pass`). The same data path already loads TE route
details; only the writer is gated to WR.

Pass offense quality has a free nflverse stand-in (team pass EPA/play). True
TE in-line % does not.

### Misleading ceiling display

Board and player detail show raw `ceilingScore` next to a hardcoded `/60` and
paint “good” green when `score >= 30`. That `/60` is leftover from an old TE
all-green max (`12 × 5`); it is **not** `CEILING_RANGE` and is wrong for every
position after six-band weights and varying known-factor counts.

Theoretical %-of-green (`raw / (n×3)`) was considered and rejected: live elite
profiles only reach ~50–70% of all-green, so a percent reads as “mid” even for
board leaders.

### Invisible DraftScore

The board’s default sort and tier breaks use `evaluation.draftScore` (or
contextual recommendation score), but there is **no DraftScore column** — only
Ceiling / Conf / Archetype / Risk / Value / Proj. Users cannot see the
composite that orders the list without opening calibration docs or inferring
from rank alone.

## Decisions

| Decision | Choice |
|----------|--------|
| `pass_dvoa_rank` | **Replace** with team **pass EPA/play rank** proxy from nflverse PBP |
| Factor id for QB pass efficiency | **`pass_epa_rank`** (new id; retire `pass_dvoa_rank`) |
| Label | `Pass EPA rank (proxy)` — honesty in the label, same pattern as OL / YPRR |
| Direction / shape | **lowerBetter** team rank (1 = best), same as old DVOA rank |
| Benchmark | Cohort mean from rebuild (`build_benchmarks.py`), not the old FTN 7.01 |
| `yprr_rank` (TE) | **Replace** with rate **`yprr`** (same formula + tag as WR) |
| TE `yprr` label | `Yards per route run (proxy)`; higherBetter rate |
| TE `yprr` benchmark | Cohort mean from TE rebuild (do **not** reuse WR 2.739 blindly) |
| `inline_pct` | **Delete** — no honest free alignment proxy |
| TE detail UI | Remove the “In-line rate under 50%” check row |
| Licensed blocked list | Drop FTN DVOA + both TE PFF rows; no remaining licensed ceiling gaps |
| Ceiling board/detail number | **Raw `ceilingScore` only** — no `/60`, no `%`, no position-max denom |
| Ceiling green (“good”) styling | **Top 5 at the player’s position** by raw `ceilingScore` |
| Top-5 scope | Position-scoped (QB/WR/RB/TE separately), not overall board |
| Ties at cutoff | Include everyone tied at the 5th-place raw score |
| Provisional ceilings | Excluded from the top-5 set (do not go green solely on provisional) |
| DraftScore / `CEILING_RANGE` | **Unchanged** math — normalization stays on ±5n |
| Rookie / low-known ceiling inflation | **Out of scope** — leave blank-slate ≈ neutral behavior for now |
| Player board DraftScore column | **Add** — show the composite number that already drives sort/tiers |
| Board hovers (SCORE / CEILING / ARCH) | **Summary tooltips (option A)** — weighted parts / top factor grades / rule+EV+rates |
| Board column-header hovers | **Purpose blurbs** for every labeled column (# through FACTORS) |
| CONF denominator + factor strip | **Per-position catalog size** — stop hardcoding `12` |

## Classification of work

### 1. QB: `pass_dvoa_rank` → `pass_epa_rank` (proxy)

**Source:** nflverse play-by-play, regular season, pass plays with EPA.

```text
team_pass_epa = mean(epa | pass plays for posteam)
pass_epa_rank = rank teams by team_pass_epa descending (1 = highest EPA)
```

- Attach to every QB on that team for the measurement season (same attachment
  pattern as `ol_pass_block_rank` / `neutral_pace_rank`).
- Source tag: `nflverse:pbp:proxy` (or `nflverse:pbp` with proxy in the label —
  pick one and use it consistently with OL).
- Best-effort: empty PBP → leave unset; never fabricate `0` / rank 16.
- **Not DVOA:** no opponent adjustment, no FTN weights. Docs must say so.

### 2. TE: `yprr_rank` → `yprr` (proxy)

Reuse `compute_yprr(receiving_yards, on_pass)` already used for WR.

- In `load_player_seasons` participation loop, emit `yprr` for **TE and WR**
  (drop the `pos == "WR"` gate).
- TE `FACTORS` / DraftLab catalog: remove `yprr_rank`; add `yprr` as
  situational rate, higherBetter, label includes `(proxy)`.
- Source tag: `nflverse:participation`.
- Seeds / spot-checks / player-detail rank-format lists: swap id.

### 3. TE: delete `inline_pct`

- Remove from sleeperMCP `FACTORS["TE"]`, DraftLab `benchmarks.ts`, bootstrap
  JSON, seeds, synthetic depth factors, tests.
- Remove TE player-detail check keyed on `inline_pct`.
- Eval model / SAD: stop listing in-line as a configured TE factor; keep the
  *historical study note* that league winners were often &lt;50% in-line as
  prose only (not a live factor).

### 4. Ceiling score display (web)

**Surfaces:** cheat-sheet / board ceiling cell; player-detail weighted ceiling
header (and any other UI that still shows `/60` or `>= 30` green).

**Number**

- Render `ceilingScore` as an integer (or `—` when null).
- Remove `/60` (and do not replace with `/55`, `/85`, `%`, or green-max).

**Green styling**

```text
For each position P among currently shown / board players:
  eligible = non-provisional rows at P with a numeric ceilingScore
  cutoff  = 5th-highest distinct? → use score at rank 5 in eligible
            sorted by ceilingScore descending
  green   = score >= cutoff (so ties at the cutoff all green)
```

- Prefer computing the top-5 set once per board load (or in the API) rather
  than O(n²) in every cell.
- Player detail: green if that player’s id is in the position top-5 set for
  the current board/universe (same rule as the board). If detail is opened
  without a board context, either pass the set from the parent or recompute
  from the same player list the app already has.
- Do **not** use hardcoded `score >= 30`.

**Out of scope for display:** changing how `ceilingScore` is summed, how
`normaliseCeiling` / DraftScore works, or confidence (`knownFactors`) chips.

### 5. DraftScore column (player board)

**Surface:** `board.component` column header + row cell (cheat-sheet board).

**Value**

```text
displayScore = row.recommendation?.contextualScore ?? row.evaluation.draftScore
```

Same source as today’s default “Sort: Draft score” and rank helper — do not
invent a second formula. Round for display (integer is fine; match draft-room
`Math.round` if already used there).

**Layout**

- Add a compact mono column, header `SCORE` or `DRAFT` (prefer **`SCORE`** —
  short; tooltip/title can say “Draft score”).
- Place it **after `#` / before or after ADP** — recommended: immediately
  after **ADP**, before **CEILING**, so market vs model sit together.
- Include in the sticky col-head grid and CSS column template (`c-score`).

**Styling**

- No new green threshold required for v1 (rank/tiers already encode “how
  good”). Optional later: top-N overall by DraftScore — **not** in this spec.
- Drafted rows: still show the score (muted with the row), same as other
  numeric columns.

**Non-goals for this column:** player-detail hero rewrite; changing weights;
  putting the four-way breakdown *inside* the cell (hover only — §6).

### 6. Summary hovers (SCORE / CEILING / ARCHETYPE)

**Depth:** option A — short recipe, not every factor line / full rate table.

**Chrome:** compact hover panel (or equivalent accessible description), not a
one-line native `title` only — multi-line content must stay readable. Keyboard
focus should expose the same text (`title`/`aria-describedby` acceptable if the
panel pattern already used elsewhere matches).

#### SCORE

Show how DraftScore was assembled for this row:

```text
Draft score 76
  Ceiling   62 × 0.40
  Archetype 71 × 0.25
  Value     55 × 0.20
  Risk      80 × 0.15   (shown as 100 − riskProfile contribution)
→ weighted blend (same weights as evaluation.weights / defaults)
```

- Use `evaluation.weights` when present; otherwise `DEFAULT_WEIGHTS`.
- Ensure board payload includes `weights` (and keep risk/value/ceiling/arch
  scores already on the row) so the client can recompute the four normalized
  inputs with the same helpers as `computeDraftScore` — or have the API attach
  a small `draftScoreParts` object. Prefer **client recompute from existing
  fields + weights** to avoid a second formula source.
- If `contextualScore` differs from `draftScore`, note “contextual” on the
  first line.

#### CEILING

```text
Ceiling 35 · 15/17 known
  +5 targets (elite)
  +5 receptions (elite)
  +3 route participation (green)
  … up to ~5 largest |weight| contributors …
  unknown × k omitted from sum
```

- Raw total + known/configured.
- Top contributors by absolute weight among graded factors (skip `unknown`).
- If top-5 green applies, optional one-liner: “Top 5 WR ceiling.”

#### ARCHETYPE

```text
Elite · EV 0.82
  Why: yr 5, top-8 in 5/5 seasons (over half) → rule 4
  Boom 34% · Bust 13% · Injury 11% · Return 54% · Fine 23%
```

- Label + EV.
- **Why:** short rule phrase from bio already on the player (`age`,
  `seasonsInLeague`, top-5/8/12 counts). Add a pure helper
  `explainArchetype(player) → string` next to classifiers (no new server
  field required unless easier).
- Rates: boom / bust / injury / return / fine when present on the evaluation.

**Non-goals for hovers:** full factor strip duplication; risk/value column
*cell* hovers in this pass; mobile long-press polish beyond “works if hover/focus
exists.”

### 7. Column-header purpose hovers

Every labeled header in the board `col-head` row gets a short **purpose**
tooltip (what the column means in DraftLab — not a per-player recipe).

| Header | Purpose blurb (ship copy; tweak for voice) |
|--------|---------------------------------------------|
| `#` | Rank on this board (recommendation rank when present, else sort order). |
| `POS` | Player’s fantasy position. |
| `PLAYER` | Name, NFL team, age, and seasons in the league. |
| `ADP` | Average draft position (round.pick) from the league’s ADP source. |
| `SCORE` | DraftScore — weighted blend of ceiling, archetype, value, and risk. |
| `CEILING` | Raw sum of graded ceiling-factor weights. Green = top 5 at this position. |
| `CONF` | How many ceiling factors are known vs configured for **this player’s position**. |
| `ARCHETYPE` | Career-stage bucket from finish history (and age/year gates). |
| `RISK` | Injury / availability risk profile (higher = more games expected missed). |
| `VALUE` | Market mispricing vs blended rank (positive = undervalued). |
| `PROJ` | Season-long projected fantasy points when available. |
| `FACTORS` | Per-factor grade strip that feeds Ceiling (length = position catalog). |

**Chrome**

- Native `title` is enough for one-sentence headers (unlike §6 multi-line
  recipes). Same hover panel pattern as §6 is fine if already shared.
- Remove `aria-hidden="true"` from `col-head` (or expose an accessible name per
  header) so header help is not hidden from assistive tech.
- Flag / target column: optional “Pin as a draft target” — include if the
  control is labeled only by icon.

**Non-goals:** long docs in the header; linking out to the eval model from the
tooltip.

### 8. CONF denominator + factor strip (position-accurate)

Today the board hardcodes `FACTOR_SLOTS = 12` for:

- CONF cell: `knownFactors / 12`
- Factor grade strip padding/slice
- Header label `12 FACTORS`

That is wrong for WR (17), RB (16), and TE after this change (13). QB happens
to stay 12.

**Rule**

```text
configuredFactors(row) =
  row.evaluation.ceiling.factors.length
  if factors[] is present and non-empty
  else POSITION_CATALOG_COUNT[row.player.position]  // fallback map
```

- CONF: `{{ knownFactors }}/{{ configuredFactors(row) }}`
- Grade strip: slice/pad to `configuredFactors(row)` (not a global 12).
- Header: label **`FACTORS`** (no fixed number). Header purpose blurb (§7)
  already says the count matches the position catalog.
- `confidenceScore` from the engine already uses `factors.length` as denom —
  UI must match that, not invent a second constant.
- After §1–3 ship, expected counts: QB **12**, RB **16**, TE **13**, WR **17**
  (keep the fallback map in sync with `benchmarks.ts` / `CEILING_KNOWN` catalog
  lengths — catalog length, not “currently sourced” known-max, for the
  denominator of CONF).

**Note:** `CEILING_KNOWN_FACTORS` is for DraftScore normalization range (how
many factors are *sourceable*). CONF’s denominator is **configured catalog
slots** on the evaluation (including any still-unknown grades). After this
spec, those two should match per position because licensed gaps are gone /
proxied — but the UI should still prefer `factors.length` from the payload.

## Coverage after (factors)

| Position | Catalog factors | `CEILING_KNOWN_FACTORS` |
|----------|----------------:|------------------------:|
| QB | 12 (swap id, still 12) | **12** (was 11 — DVOA gap closed) |
| TE | 14 → **13** (−`inline_pct`, `yprr_rank`→`yprr`) | **13** (was 12) |
| WR | unchanged | unchanged |

Update `CEILING_KNOWN_FACTORS` and any spot-check known-counts to match.

## Touch surfaces

**sleeperMCP**

- `tools/build_benchmarks.py` — FACTORS, COMPUTABLE, loader, cohort means
- `tools/build_factors.py` — emit new ids; stop emitting deleted ones
- Tests for pass-EPA rank + TE yprr
- Regenerate `artifacts/player_factors.json` + `artifacts/benchmarks.json`
- `docs/SAD.md` / PLAN notes as needed

**DraftLab**

- `packages/evaluation-engine/src/config/benchmarks.ts` (+ activated JSON)
- `apps/api/data/benchmarks.json` + `player_factors.json` bootstrap copy
- Seeds (`seed-players.ts`, `seed-depth.ts`), spot-checks
- Web: `board.component.*` (ceiling + SCORE + §6–§8 display fixes),
  `player-detail.component.*` (ceiling + TE checks)
- Optional helpers: `isTopNCeiling`, `explainArchetype`, draft-score part
  labels for tooltips
- Ensure board JSON exposes `evaluation.weights` + archetype `rates`
- `docs/01-player-evaluation-model.md` §1.2 / §1.4 (+ short display note)
- R2 upload + Worker/web redeploy

## Non-goals

- True FTN DVOA or PFF in-line / YPRR license
- Opponent-adjusted EPA (optional future knob)
- Personnel-group “fake in-line” proxies
- Changing TE target-share / TD-rank gates
- Renaming WR `yprr` or changing its formula
- %-of-green or position-max denominator on the board
- Changing DraftScore ceiling normalization (`CEILING_RANGE` ±5n)
- Overall-board (cross-position) top-N green
- Rookie / low-confidence ceiling dampening or provisional gates
- Full factor-by-factor / full-rate-table hovers (option B)
- Risk / Value / Proj **cell** summary hovers in this pass (headers still get
  purpose blurbs in §7)

## Acceptance

1. No factor in live artifacts has `note` containing
   `not freely redistributable (FTN|PFF)` for ceiling factors.
2. Elite QB (e.g. Allen): `pass_epa_rank` measured/graded; no `pass_dvoa_rank`
   row; knownFactors reflects full QB catalog.
3. Elite TE (e.g. Bowers / McBride): `yprr` graded as rate proxy; no
   `yprr_rank` / `inline_pct` rows; TE detail has no in-line check.
4. Benchmarks artifact includes cohort means for `pass_epa_rank` (QB) and
   `yprr` (TE).
5. Docs / SAD blocked list no longer lists these three licensed gaps.
6. Board and player detail show raw ceiling with **no** `/60` (or any denom).
7. Green ceiling styling applies to the top 5 raw scores **at that position**
   (ties at cutoff included); `score >= 30` hardcoded rule is gone.
8. A mid-board WR with raw 20 is not green solely because 20 &lt; 30 was the old
   bar — only position rank matters.
9. Player board shows a **SCORE** (DraftScore) column using
   `contextualScore ?? draftScore`; default sort order matches the visible
   numbers when sorted by Draft score.
10. Hovering SCORE / CEILING / ARCHETYPE **cells** shows the §6 summary
    (weighted parts / top factor grades / rule+EV+rates) for that player.
11. Hovering each labeled **column header** shows a one-line purpose blurb
    (§7).
12. CONF shows `known / configured` with **position-accurate** configured
    count (WR ≠ 12); factor strip length matches; header is `FACTORS` not
    `12 FACTORS`.

## Open notes

- Whether to keep a short “retired: pass_dvoa_rank / inline_pct / yprr_rank”
  footnote in SAD for archaeology — optional.
- If pass-EPA ranks correlate poorly with old study intuition in spot-checks,
  reopen opponent adjustment — out of scope for v1.
- If the draft board filters by position, top-5 is still among the **full
  available universe at that position**, not only the filtered visible rows
  (avoids greening everyone when the filter is “QB only” and five QBs show).
  Confirm in implementation if the board’s data source is already the full
  pool.
- Rookie blank-slate vs dinged-prime ranking can be revisited later once
  DraftScore is visible and easier to diagnose.

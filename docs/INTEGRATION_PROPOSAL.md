# DraftLab + Sleeper MCP — integration proposal

Written after reading both codebases. The goal is the best app, not the most
code from either side.

---

## 1. Honest assessment

**DraftLab has a real model and no data.**

The evaluation engine is the more considered piece of work. Twelve
position-specific factors graded against benchmarks, archetype expected value
built from *measured* historical outcome rates, risk with position-specific age
curves, value as ADP-minus-projection. There are verified fixtures proving the
arithmetic matches the source spreadsheets. Ten Figma screens and six design
documents sit behind it. It is a product.

What it does not have is a single real input. Twelve hand-authored players with
factor values back-solved from the grade they were supposed to receive
(`value: 10.7 * 1.15`). The only external host in the monorepo is
`api.sleeper.app`.

**Sleeper MCP has real data and a thin model.**

Thirty-six tools across five upstreams, deployed, with 62 golden regression
cases. It solves problems that took real effort: format-matched trade values,
ADP with a 217/217 name join across four ID systems, the post-2024 nflverse
schema change, a 50 MB file that OOMs a container.

Its `custom_score_player` is a reasonable weighted blend, but next to DraftLab's model
it is thin. No benchmarks, no archetypes, no age curves. Thirty percent of it is
literally "what does FantasyCalc say this player is worth" — it substantially
measures the market rather than evaluating a player.

**Verdict: DraftLab's model wins, Sleeper MCP's data wins.** Not a compromise —
each side is clearly better at one thing and clearly worse at the other.

---

## 2. What that means concretely

`DraftScore` owns the number. `custom_score_player` stops being a scorer.

This is the right call even setting aside code quality, because two 0-100 scores
that disagree is a product defect. Users will ask which one to trust, and the
answer "it depends" is not an answer.

The MCP server becomes **the data layer that makes DraftLab's model executable
on more than twelve players.**

---

## 3. Architecture: batch, not live

The instinct is to have `apps/api` call the MCP endpoint. That is wrong, and the
reason is worth stating clearly.

**Factor data is static during a draft.** Targets per game, YPRR, snap share,
ADP — none of it changes while the clock runs. The only thing that changes is
who has been picked, which DraftLab already gets by polling Sleeper directly.

So the factor pipeline does not need to be live. It needs to have run recently.

That has a large consequence: **the MCP server is not in the draft-day critical
path at all.** No Horizon cold start during the one hour a year that matters, no
OAuth in a request path, no shared rate-limit budget against Sleeper and
FantasyCalc while eleven other managers are drafting.

```
  nightly / on-demand                    draft day
  ───────────────────                    ─────────
  Python job                             apps/api
    └─ imports sleeper_core                └─ reads Postgres (already populated)
    └─ writes factor rows        ──────>   └─ polls Sleeper for picks
    └─ emits JSON artifact                 └─ engines score from memory
```

This also fits DraftLab's existing mental model exactly. The plan already says
the starting point is *"a manually maintained seasonal import."* This proposal
does not change the architecture — it automates the import.

### Why Python, not TypeScript

`sleeper_core` is importable Python with no MCP dependency. A job that imports
it directly skips JSON-RPC, session handshakes, auth, and the network entirely.
It also means the MCP server and the batch job share one codebase, so a fix to
the nflverse schema handling benefits both.

If a TypeScript job is strongly preferred, calling the MCP endpoint with the API
key works — it is just slower and adds an auth dependency for no benefit in a
batch context.

---

## 3b. The ownership boundary — decided 2026-08-07

The scoring engine stays in DraftLab. It is a pure function over data, it needs
app state (who has been picked, your roster, the active strategy), and putting a
network hop in front of it would make draft night depend on a Horizon
deployment nobody can fix at 8pm on a Sunday. It is also his model.

```
  this side   what the numbers ARE      factors, benchmarks, the ID crosswalk
  that side   what the numbers MEAN     grading, archetypes, risk, scoring, strategy
```

**Benchmarks moved to this side** as a consequence. They are data — derived
from nflverse, recomputed every season, produced by the same pipeline as every
other factor value. `tools/build_benchmarks.py` emits
`artifacts/benchmarks.json`; DraftLab imports it the same way it imports factor
rows. When 2026 finishes, rerunning the script updates every benchmark without
anyone touching engine code, which is the whole reason RB sat provisional for a
season.

### What the cohort turned out to be

Reverse-engineered from DraftLab's own published QB/WR/TE numbers. Nine of ten
factors reproduce within 2% at a cohort of the **top 1-3 players per season**:

| | DraftLab | computed | at |
|---|---:|---:|---|
| WR targets | 10.70 | 10.68 | top-1 |
| TE targets | 8.10 | 8.15 | top-2 |
| QB pass attempts | 33.91 | 33.91 | top-5 |
| WR receptions | 7.21 | 7.10 | top-3 |
| TE receptions | 5.71 | 5.73 | top-3 |

So the benchmark is **what the best player at the position does**, not what a
good one does — which the name was telling us all along. Green (>= 1.05x) means
"better than the league's best", so it is rare by design, and a genuinely strong
player grading yellow is correct rather than a calibration error.

### The window is eleven seasons, not five

Testing 2015-2025 against 2021-2025 settled it:

| | 5 seasons | 11 seasons |
|---|---:|---:|
| QB rushing TDs | 30.9% off | **2.5%** |
| WR targets | 6.5% | 3.7% |
| TE targets | 3.7% | 3.0% |
| within 5% | 5 of 10 | **7 of 10** |

QB rushing TDs collapsing from 31% to 2.5% is the confirmation — mobile
quarterbacks inflate that stat in recent years, and the long window dilutes
them. So his source used the longer history, and RB computed the same way sits
on the same scale as his other three positions.

**A correction worth recording.** On the five-year numbers it looked like his
positions were graded at inconsistent strictness — WR against a top-1 bar, QB
rushing TDs against a top-6 bar — which would have biased cross-position
comparison in DraftScore. That was an artifact of the wrong window. At eleven
seasons, seven of ten factors reproduce at a *single* cohort of 3. His set is
internally consistent, and there is no need to replace QB/WR/TE. Add RB, leave
the rest alone.

Two stragglers remain, both touchdown stats: QB `passing_tds` (18.5%) and TE
`touchdowns` (15.0%). Touchdowns are the lowest-count and noisiest factor, most
likely rounded or transcribed off a chart. Worth one question rather than more
reverse-engineering.

The calibration block is embedded in every artifact. Its job is not to grade
the method against his numbers — it is to detect when *our* pipeline changes.
If a future nflverse schema shift moves these deltas, that is a broken
pipeline, not a difference of opinion.

---

## 4. The contract

One table, already in `db/schema.sql`, unchanged:

```sql
player_factor_inputs (player_id, season, factor_id, value, categorical)
```

The job produces rows. DraftLab consumes them. Neither side needs to know how
the other works.

An intermediate JSON artifact is worth having, so the job can be run and
inspected without a database:

```json
{
  "generated_at": "2026-08-07T02:14:00Z",
  "season": 2025,
  "source_versions": { "nflverse": "...", "fantasycalc": "...", "ffc": "..." },
  "players": [
    {
      "sleeper_id": "9493",
      "gsis_id": "00-0038543",
      "name": "Puka Nacua",
      "position": "WR",
      "factors": [
        { "factor_id": "targets", "value": 12.4, "source": "nflverse" },
        { "factor_id": "receptions", "value": 8.1, "source": "nflverse" },
        { "factor_id": "yprr", "value": null, "source": "unavailable" }
      ],
      "market": { "adp_round_pick": "1.06", "fse_rank": null }
    }
  ]
}
```

`source` per factor matters. When a grade looks wrong, the first question is
always where the number came from.

### The ID crosswalk is a deliverable in itself

DraftLab's `players` table already has `sleeper_id` and `gsis_id` columns,
currently unused. Four ID systems need reconciling — Sleeper numeric,
nflverse `gsis_id`, FantasyCalc `sleeperId`, FantasyFootballCalculator's own —
and the name-matching to bridge them is already built and tested to 217/217.

Nobody else has that crosswalk. It is arguably more valuable than any single
factor, because every future data source plugs into it.

---

## 5. What each side gives up

**Sleeper MCP gives up:**

- `custom_score_player` as a scorer. Retire it, or relabel it a "market score" that is
  explicitly a second opinion. Its components become inputs.
- `playcaller_tiers.json`. It is hand-rated opinion; DraftLab's situational
  factors (offensive PPG rank, OL rank, pace, DVOA) are measured. Measured beats
  opinion. The tiers can stay for conversational use in Claude, but they should
  not feed DraftScore.
- Being the interface. The app is the product; the MCP server is plumbing plus a
  Claude interface for ad-hoc questions.

**DraftLab gives up:**

- "Manually maintained seasonal import" as the plan of record, for roughly half
  the factors.
- The assumption that licensed metrics are the binding constraint. They are the
  constraint on *some* factors, not on the model being usable.
- Nothing else. The model, benchmarks, grading, archetypes, risk, strategy
  engine, UI and draft flow are unchanged.

**Neither side merges repos.** Two people, two languages, one contract. The
boundary is the point.

---

## 6. Sequencing, highest value first

### Step 1 — RB benchmarks

RB `ceilingScore` currently returns `null`. Every benchmark is zero, so 40% of
DraftScore redistributes into archetype and risk, and RB — where drafts are
usually won or lost — is the least informed position in the model.

This is fixable with data neither side is missing. The RB factor list wants
touches/g, rush attempts/g, targets/g, TDs/g, snap share, red-zone touch share,
goal-line carry share, neutral run rate. nflverse supplies the first five today;
the last three come from play-by-play, which is a straightforward addition.

Compute the top-24 RB cohort averages across several seasons and you have real
benchmarks in the same shape as the QB/WR/TE ones.

**This is the highest-value thing either of you can do, and it does not touch
his model at all — it completes it.**

### Step 2 — Volume factors, all positions

Targets, receptions, touchdowns, carries, pass attempts, passing TDs. Direct
from `get_player_stats`, high confidence, no derivation. Roughly 4 of 12 factors
per position, immediately.

Watch the divisor — see §7.

### Step 3 — Market

`adp_round_pick` from FantasyFootballCalculator, already format-matched to
superflex dynasty with a fallback chain. This fills `player_market.adp_round_pick`
and the QB `adp` factor.

`fse_rank` and `espn_projection_rank` stay manual — no free source.

### Step 4 — Derived situational

Offensive PPG rank, team pass attempts, team target rank, receiving TD rank,
secondary-target status. All computable by aggregating nflverse across a team's
players. `get_team_offense_crowding` already computes team target rank.

### Step 5 — Leave licensed alone

PFF grades, DVOA, Reception Perception, QBR. Manual seasonal import, as planned.
By this point they are 3-4 factors out of 12 rather than the whole model, and
`confidenceScore` reports the gap honestly.

---

## 7. Risks and open questions

**Benchmark definitions must match, and this is the biggest risk.**

The WR targets benchmark is 10.7. Per game *played*, or per team game? Regular
season only, or including playoffs? Which cohort — top 12, top 24, positional
average? If the benchmark came from one definition and the fed values use
another, every grade shifts systematically and nobody notices, because the
output still looks plausible.

Before feeding anything: take three players with known spreadsheet grades,
compute their factor values from nflverse, and confirm the grades come out the
same. If they do not, the definitions differ and that must be resolved first.
This is the same discipline as the golden harness — verify the pipeline against
a known-good answer before trusting it.

**Grain mismatch.** `player_factor_inputs` is keyed by season; nflverse data is
weekly. Aggregation is a modeling decision — full season, trailing eight games,
weighted recent — and it belongs to DraftLab, not to the job. The job should
probably emit both a seasonal roll-up and retain weekly detail for later, since
adding a `week` column later is cheap and re-deriving history is not.

**Sleeper rate limits are shared.** The plan already flags the ~1000 calls/min
budget as load-bearing and concentrated into a few weeks a year. Two systems
polling Sleeper — the MCP server and `apps/api` — draw on the same budget from
different IPs. During a live draft, `apps/api` should be the only one polling.

**Whose Sleeper client wins?** `packages/integrations/src/sleeper/client.ts`
already exists and `apps/api` has a draft poller. Keep them. The MCP server
should not be in the live-draft loop. Raw league reads via TypeScript, enrichment
via the batch job.

**This is his project.** He has a plan, a design system, and a phase sequence
with "no open decisions blocking the start of that work." Arriving with a
solved data problem can read as a takeover. The framing that works is a
contract and an offer: here is a JSON file that fills your existing table, take
it or leave it.

---

## 8. What to ask him

1. Was "manually maintained seasonal import" a considered choice, or the only
   option visible at the time? If the latter, roughly half those factors stop
   being manual.
2. Where exactly did the benchmarks come from — which cohort, which seasons,
   which definition of "per game"? This determines whether fed data is
   comparable.
3. Why are the RB benchmarks provisional — cropped source images, or a decision
   pending? If it is the former, that is solvable this week.
4. Is `archetypeEv`'s injury penalty (−1.5) versus bust penalty (−1) deliberate?
   It says an injured player costs more than one who underperforms, which is
   defensible but unusual and worth confirming it was intentional.
5. Does he want a second score surfaced at all, or should `custom_score_player` be
   retired entirely?

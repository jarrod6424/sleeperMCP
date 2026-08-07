# Two scoring models, side by side

DraftLab's `evaluation-engine` and the Sleeper MCP `custom_score_player` tool both emit
a 0-100 number for a player. They are **not** measuring the same thing, and the
numbers are not comparable.

Written to settle which one is authoritative before the app ships two answers
to the same question.

> The tool was renamed `score_player` → `custom_score_player` on 2026-08-07.
> The old name implied an authoritative rating; it is one opinionated
> weighting, and the name should say so before anyone builds on it.

---

## The one-line difference

**DraftLab asks: how good is this player going to be?**
Twelve position-specific performance factors versus league benchmarks, plus
historical archetype outcome rates, plus injury risk, plus draft-cost value.
It's a projection model.

**custom_score_player asks: how good is this player for *my* team, right now?**
Market trade value, past scoring floor, availability, usage within his offense,
and whether he fills a hole on your roster. It's a market-and-context model.

The single biggest tell: DraftLab has **no market-value input except ADP**,
while `custom_score_player` puts **30% on FantasyCalc trade value**. One is trying to
beat the market, the other is largely measuring it.

---

## DraftLab — `DraftScore`

Weighted blend, each component normalised to 0-100 first:

| Component | Weight | What it is |
|---|---:|---|
| CeilingScore | 40% | 12 graded factors vs positional benchmarks |
| ArchetypeEV | 25% | Historical boom/bust/return rates for the player's archetype |
| ValueScore | 20% | ADP minus projected rank — is he falling past his worth |
| RiskProfile | 15% | Inverted; injury and age risk |

### CeilingScore (40%)

Every factor is graded against a positional benchmark and scored:

```
green  ≥ 1.05× benchmark   +5
yellow ≥ 0.90×             +3
orange ≥ 0.75×             −1
red    below               −3
unknown                     0
```

Twelve factors per position, summed. Range **−36 to +60**, normalised to 0-100.
Also reports `confidenceScore` = known factors / 12, so a thin profile is
visibly thin.

Factors are genuinely position-specific — this is the most opinionated part of
the model:

- **QB** — pass att/g, pass TD/g, rush att/g, rush TD/g, offensive PPG rank,
  OL pass-block rank, deep-ball att/g, QBR rank, red-zone attempts, ADP,
  neutral pace rank, pass DVOA rank
- **WR** — targets/g, receptions/g, TD/g, offensive PPG rank, QB PFF rank,
  team pass attempts, secondary-target status, OL pass-block rank, YPRR,
  Reception Perception percentile, archetype, injury concern
- **TE** — targets/g, receptions/g, TD/g, offensive PPG rank, QB QBR rank,
  team pass-attempt rank, **team target rank**, receiving TD rank, route
  participation %, in-line %, YPRR rank, injury concern
- **RB** — **provisional**. Benchmarks are all zero, so `ceilingScore` returns
  `null` and the 40% weight redistributes 60/40 into archetype and risk.

Two details worth knowing:

**The TE gate.** A tight end ranked worse than 2nd in team targets sets
`failsTargetShareGate`. A hard qualifier, not a smooth penalty — correct for
the position, since TE production is close to binary on target share.

**`excludeAdp` variant.** An 11-factor mode that drops ADP, giving a pure
talent-and-situation read with no market contamination. That's the version
worth comparing against `custom_score_player`, since otherwise both models have market
data baked in at different depths.

### ArchetypeEV (25%)

Empirical outcome rates per archetype, per position. Not opinion — measured
frequencies:

| Archetype | Return | Boom | Bust | Injury |
|---|---:|---:|---:|---:|
| WR PRIME_WR1 | 53.5% | 33.8% | 12.7% | 11.3% |
| WR PRIME_WR2 | 37.9% | 31.0% | 31.0% | 13.8% |
| WR BREAKOUT | 27.3% | 18.2% | 29.6% | 15.9% |
| WR TRUSTY_VET | 27.8% | 8.3% | 16.7% | 30.6% |
| RB IN_THEIR_PRIME | 46.2% | 27.9% | 20.2% | 15.4% |
| RB BREAKOUT | 42.9% | 19.6% | 19.6% | 17.9% |
| RB TRUSTY_VET | 33.3% | 20.0% | 16.7% | 21.7% |

This has no equivalent anywhere in `custom_score_player`. It is the most distinctive
thing DraftLab does.

### ValueScore (20%)

```
(ADP overall pick − blended projection rank) × 1.5, clamped to ±100
blended rank = 0.6 × FSE rank + 0.4 × ESPN projection rank
```

Positive means he's available later than he should be.

### RiskProfile (15%, inverted)

```
100 × (0.40 career games-missed rate
     + 0.25 archetype injury rate
     + 0.20 age-curve penalty
     + 0.15 recent serious injury)
```

Age curves start at **RB 26, WR 28, TE 30, QB 34**. The code notes the WR
penalty ramps earlier and harder than conventional wisdom.

---

## Sleeper MCP — `custom_score_player`

| Component | Weight | What it is |
|---|---:|---|
| Trade value | 30% | FantasyCalc value, format-matched |
| Floor consistency | 20% | % of games above 10 PPG + avg PPG |
| Availability | 15% | Games played as % of season |
| Usage | 15% | Target share / WOPR / snap rate |
| Team fit | 10% | Does he fill *your* roster's weakest position |
| Offensive context | 10% | Play-caller tier + usage crowding (HHI) |

All from live data — FantasyCalc, nflverse, Sleeper — with no hand-maintained
benchmarks except `playcaller_tiers.json`.

Two components have no DraftLab counterpart:

**Team fit (10%)** is roster-relative. It values every player you own by
position, finds your weakest, and rewards players who fill it. The same player
scores differently for you than for darknegan — which is why this component
needed the `username` parameter.

**Offensive context (10%)** combines a hand-rated play-caller tier with a
Herfindahl index of how concentrated that offense's targets are. A 25% target
share on a spread-it-around offense means something different from 25% on a
team funneling to one player.

---

## Where they overlap, and where they genuinely disagree

| Concept | DraftLab | custom_score_player |
|---|---|---|
| Market value | ADP only, inside ValueScore | 30% FantasyCalc trade value |
| Usage | Inside CeilingScore as raw volume vs benchmark | 15%, as share and consistency |
| Injury / age | 15% RiskProfile, explicit age curves | 15% availability, backward-looking only |
| Offense quality | Off PPG rank, OL rank, DVOA, pace | Play-caller tier + target HHI |
| Archetype outcomes | 25%, empirical rates | **absent** |
| Roster fit | **absent** | 10% |
| Consistency | **absent** | 20% floor |
| Position benchmarks | Hand-tuned per position, per season | **absent** |

**They disagree most on RBs.** DraftLab's RB ceiling is provisional, so 40% of
its weight gets redistributed — an RB score is structurally less informed than a
WR score. `custom_score_player` treats every position identically. Any RB comparison
across the two models is apples to oranges until those benchmarks land.

**They disagree on what "good" means.** A player the market loves scores well in
`custom_score_player` almost by construction — 30% is literally market price. DraftLab
would call that same player fairly valued and score him on whether the
underlying factors justify it. When they diverge on a player, DraftLab is
usually saying "the market is wrong" and `custom_score_player` is saying "the market
says this."

---

## Recommendation

**Don't ship both as a score.** Two 0-100 numbers that disagree is a support
burden and erodes trust in the one that's right.

DraftLab's `DraftScore` should own the number. It's purpose-built for drafting,
has verified fixtures, and its factors are the ones that actually predict.

`custom_score_player` is more useful decomposed than aggregated. Its components are
things DraftLab currently has no source for:

- **Trade value** — FantasyCalc, format-matched to superflex dynasty. DraftLab
  has no market-value input beyond ADP. This is the obvious gap to fill.
- **Offensive context** — play-caller tier and target HHI could feed
  CeilingScore's situational factors directly.
- **Usage / target share / WOPR** — already computed from nflverse; DraftLab's
  factors want exactly this and would otherwise need its own nflverse pipeline.
- **Availability** — feeds RiskProfile's `careerGamesMissedRate` input, which
  currently falls back to a hardcoded prior.

So: **MCP as a data source for DraftLab's factors, not as a competing scorer.**
Call `get_trade_values`, `get_player_stats`, `get_team_offense_crowding` and
`get_adp` from `apps/api`; let `evaluation-engine` own the verdict.

If you do want a second opinion surfaced in the UI, label it as one — "market
score" versus "DraftLab score" — so the difference is the point rather than a
bug report.

### First concrete step

The RB benchmarks are the biggest hole in DraftLab, and `custom_score_player` can't
fill it — but the underlying nflverse data can. `get_player_stats` and
`get_snap_counts` already return touches, snap share and target share per week,
which is most of what the provisional RB factor list is asking for.

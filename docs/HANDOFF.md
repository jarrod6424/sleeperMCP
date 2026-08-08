# Handoff — 2026-08-08

State at the end of the Cowork session that built the benchmark and factor
pipelines. Written to be read by a fresh Claude Code session.

---

## Where things stand

**Done and committed** (8 commits, `3693a62`..`ac96361`, **not yet pushed**):

- `tools/build_benchmarks.py` — CeilingScore benchmarks for QB/RB/WR/TE from 11
  seasons of nflverse. `--spread` reports dispersion and z-scores against
  DraftLab's published values. Schema v2 ships per-factor `dispersion`.
- `tools/build_factors.py` — per-player factor values for the draftable
  universe. 173/187 matched, 187/187 resolved to Sleeper IDs.
- `tools/check_contract.py` — detects drift between our factor ids and
  DraftLab's. **Last run: contract holds, 48/48 ids present.**
- `CLAUDE.md` — read it first.

**Uncommitted:** `artifacts/benchmarks.json` (modified, schema v2) and
`artifacts/player_factors.json` (new). Both need regenerating and committing.

## Do these first

```bash
python tools\build_benchmarks.py --spread
python tools\build_factors.py
pytest tests/test_golden.py -q
git add artifacts/ && git commit -m "artifacts: regenerate at schema v2"
git push
```

Expected in the factors run: `missing:not_recorded 3`, `missing:no_team_context
8`, 5 players recovered from an earlier season (Watson, Brooks, Aiyuk, Dell
from 2024; Travis Hunter from 2025, filed under CB). 14 unmatched, all genuine
2026 rookies.

---

## What we learned about DraftLab

Read `docs/INTEGRATION_PROPOSAL.md` for the full reasoning. The short version:

- **No contract drift.** All 48 factor ids intact after his Phase 6-7 changes.
- **He built breadth, not data.** Dynasty, auction, calibration, strategy,
  live-draft polling engines. But `apps/api/src/services/store.ts` still imports
  `SEED_PLAYERS` at 16+ call sites — **12 hardcoded players** with values
  back-solved from target scores (`passing_tds: 2.63 * 0.7`).
- **`db/schema.sql` exists but nothing populates it.** `player_factor_inputs
  (player_id, season, factor_id, value, categorical)` matches our export shape
  almost exactly. `players` carries both `sleeper_id` and `gsis_id`.
- **RB is still `provisional: true` with every benchmark 0.** In
  `grade-factor.ts`, `if (benchmark === 0) return 'unknown'` — so the entire 40%
  CeilingScore weight is dead today. Our RB benchmarks fix exactly this.
- **His `calibration-engine` does not overlap ours.** It tunes grading bands and
  DraftScore weights from observed draft outcomes. Different problem.
- **`FactorInput` is `{factorId, value, categorical?}` — no provenance field.**
  This is the blocking integration gap. 55 `stale:team_changed` plus 17
  recovered values would land as ordinary numbers, silently asserting ~72 things
  we know aren't true.
- Good news: `grade-factor.ts` maps null to grade `'unknown'` with its own
  weight, so honest gaps degrade correctly with no changes.

## Decided

- **JSON artifacts, not a generated seed file.** A seed file cannot carry
  provenance (his type has no field for it), goes stale immediately in August
  when ADP moves daily, and couples our repo to his internal TypeScript types.
- **No Python in his repo.** TS cannot import Python, only call it. An HTTP
  service already exists (the deployed MCP endpoint). Factor data is static
  during a draft, so a live dependency buys nothing and risks a cold start.
- **Batch, not live.** The MCP endpoint is not in the draft-day critical path.

## Open

- **Merge into a single repo?** Under active consideration. Fixes contract
  drift and handoff friction; fixes none of the remaining work, which is
  identical either way. Argument against: the MCP server has a life outside
  DraftLab (Claude mobile league analysis), and a merge subordinates it. Timing
  argument: large refactor, ~2 weeks before a draft, over a test suite that
  demonstrably does not catch real bugs.
- **Provenance policy** — his call, but needs making explicitly: what should the
  engine do with `stale:team_changed` (34 players) and `missing:no_team_context`
  (5 players, scoring on 7-11 factors instead of 12)? Impute, penalise, or
  exclude. `missing:no_team_context` must **not** be imputed.
- **`build_factors.py` has no sanity check on ADP.** It errors on an empty list,
  but a truncated or stale response would quietly regenerate a smaller
  artifact. Add a previous-artifact comparison that refuses to write on a sharp
  player-count drop — required before this goes on a schedule.
- **Universe is only 187 players.** FFC returned 217 total; that is the whole
  list, not a `--limit` truncation. Fine for a 12-team/15-round redraft (180
  picks), thin for anything deeper.
- **Nightly GitHub Action** to regenerate artifacts. nflverse and FFC are
  public, so no secrets needed.

---

## Prompts to start with

**Resuming the pipeline work:**

> Read CLAUDE.md and docs/HANDOFF.md. Regenerate both artifacts, run the golden
> suite, and commit. Then add the ADP sanity check described under Open — compare
> against the previous artifact and refuse to write if the player count drops
> more than 15%.

**Building the DraftLab side (needs the merge/PR decision first):**

> Read CLAUDE.md, docs/HANDOFF.md and docs/INTEGRATION_PROPOSAL.md. In
> C:\Code\fantasy-football-draft-optimizer\fantasy-football-draft-optimizer,
> write a loader that reads artifacts/player_factors.json and
> artifacts/benchmarks.json into the shape his engines expect. Add a provenance
> field to FactorInput and a provenance column to player_factor_inputs. Replace
> the RB zeros in benchmarks.ts with our computed values. Open it as a PR — his
> history is entirely PR-merged, and darknegan has not been consulted yet.

**The merge decision:**

> Read CLAUDE.md and docs/HANDOFF.md, then both repos. I'm considering merging
> sleeperMCP into the DraftLab monorepo. Walk me through what actually breaks:
> the Horizon deploy entrypoint, sleeper_core being shared between server.py and
> tools/, and whether the MCP server stays first-class. Don't just agree with me.

**Automation:**

> Read CLAUDE.md. Add a GitHub Action that runs build_benchmarks.py and
> build_factors.py nightly and commits the artifacts if they changed. Both
> upstreams are public so it needs no secrets. Include the ADP sanity check so a
> bad fetch cannot commit a degraded artifact.

---

## Watch for

The failure mode described in `CLAUDE.md` is not theoretical — six instances
this session. After any change that produces numbers, read the numbers and
reconcile the counts. The bug that surfaced last was found only because
`missing:not_recorded` went 2 → 11 and the arithmetic did not work out.

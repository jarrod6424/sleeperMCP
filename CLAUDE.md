# Sleeper MCP — working notes for Claude

Read-only MCP server exposing a Sleeper fantasy football league, plus a data
pipeline that feeds DraftLab (a separate draft/trade app). 37 tools, five
upstreams, deployed on Prefect Horizon at `jlg-sleeper.fastmcp.app/mcp`.

## Environment

- Python 3.10+, venv at `.venv`. Activate: `.venv\Scripts\Activate.ps1`
- **Needs real network access.** nflverse, Sleeper, FantasyCalc and FFC are all
  live fetches. Sandboxed environments that block `raw.githubusercontent.com`
  will fail every golden test and silently degrade `off_ppg_rank` to a
  circular proxy. If tests fail wholesale with `.__error__: key added`, that is
  the network, not a regression.
- Windows. Paths in docs use backslashes.

## Layout

```
server.py          37 @mcp.tool() definitions — thin wrappers, no logic
sleeper_core/      the data layer. No MCP imports anywhere, by design
tools/             artifact generation (CLI + importable rebuild APIs)
data_api/          optional local HTTP rebuild (NOT production; NOT Horizon)
.github/workflows/ publish-artifacts.yml → Cloudflare R2 for DraftLab
artifacts/         generated JSON (CLI + Action build outputs)
tests/             golden-output regression harness
docs/              BUILD_NOTES, INTEGRATION_PROPOSAL, GO_LIVE_ACTIONS_R2, HANDOFF
```

`sleeper_core` is importable without MCP — no JSON-RPC, no auth, every upstream
is a public read. Production handoff is **GitHub Actions → R2**. DraftLab never
calls Horizon MCP.

## Commands

```bash
pytest tests/test_golden.py -q          # after any change
pytest tests/test_check_artifact_count.py tests/test_data_api.py -q
python tests/capture_golden.py          # ONLY when output changed on purpose
python tools/build_benchmarks.py --spread   # -> artifacts/benchmarks.json
python tools/build_factors.py               # -> artifacts/player_factors.json
python tools/check_artifact_count.py --new artifacts/player_factors.json
python tools/auction_budget.py --league <id>  # FantasyCalc -> auction $ targets
python tools/check_contract.py ../fantasy-football-draft-optimizer
# Production publish: GitHub Actions → publish-artifacts (or workflow_dispatch)
MCP_HTTP=1 python server.py             # local HTTP; then python tests/smoke_http.py
```


## The one thing to internalise

**Every real bug in this project produced plausible output instead of an
error.** Not one was caught by the test suite. The golden harness faithfully
preserves whatever behaviour it captured, including broken behaviour.

Actual examples: `LAR` vs `LA` silently defaulted every Rams player to a
neutral 50; `maybeAdp` was always null so ADP never worked; `off_ppg_rank` ran
on a circular fantasy-points proxy; a miss-classifier flagged three players as
recoverable because they shared a common surname; `missing:no_team_context` was
unreachable dead code behind an earlier `raw is None` check.

All of them surfaced from reading output and asking whether the numbers made
sense. So: after any change that produces numbers, look at the numbers.
Reconcile counts. Ask whether a total adds up. Do not trust a green test run to
mean the output is right — it only means the output did not change.

## Conventions that matter

**Explicit gaps beat missing keys.** A factor we cannot source is emitted as
`null` with a reason, never omitted. A missing key is invisible; a null with a
note is a decision someone can act on.

**Provenance is per field.** `measured`, `measured:<year>`, `stale:team_changed`,
`missing:no_team_context`, `missing:no_prior_season`, `unsourced`. These are not
cosmetic — they mean different things to a consumer. `missing:not_recorded`
says the source lacks it (safe to impute); `missing:no_team_context` says we
withheld it deliberately (must NOT be imputed).

**Never re-capture goldens to make a failure go away.** A failing diff is the
harness working. Re-capture only when output changed on purpose.

**Say which file you want to delete before deleting it.** The user cannot see
the target in the permission prompt otherwise.

## Ownership boundary with DraftLab

```
this side   what the numbers ARE     factors, benchmarks, the ID crosswalk
that side   what they MEAN           grading, archetypes, risk, scoring, strategy
```

Nothing in `tools/` grades, scores or ranks. It reports measurements and says
where each came from. DraftLab's repo lives at
`C:\Code\fantasy-football-draft-optimizer\fantasy-football-draft-optimizer`.

**DraftLab's published benchmarks are a reference, not ground truth.** They
were transcribed by hand from screenshots of a video whose method is unknown.
A gap is two inferences disagreeing, not an error to fix. Do not tune
`build_benchmarks.py` to close one. Judge gaps by `--spread` z-scores, never by
percent — relative SE is 10-16% on rank factors and 2-3% on volume factors, so
percent inverts the answer.

## Identity

League `1312218810614300672`, user `JarrodLee`, team "Pine Bluff Escapees".
All are env-var defaults; every tool also takes explicit arguments.

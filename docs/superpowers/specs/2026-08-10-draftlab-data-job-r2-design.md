# DraftLab ← sleeperMCP artifacts via GitHub Actions → R2

Approved 2026-08-10; revised same day to drop Fly.

## Problem

Regenerating JSON in sleeperMCP and manually committing copies into DraftLab
(plus hand-syncing ceilings in `benchmarks.ts`) is a dual source of truth and a
bad workflow.

## Design

- **GitHub Action** in sleeperMCP builds `player_factors` + `benchmarks` and
  uploads them to Cloudflare R2 (`draftlab-artifacts`).
- Schedule: Mondays 12:00 UTC + `workflow_dispatch` for manual refresh.
- DraftLab reads R2 (Worker) or a local FS cache (Node). **7-day** freshness
  check: fresh → use; stale → still serve, log that an Action refresh is due;
  missing → bundled bootstrap under `apps/api/data/`.
- Numeric ceilings leave `benchmarks.ts`; static factor metadata stays in
  DraftLab and merges at load time.
- **No Fly.** Horizon MCP stays conversational only. DraftLab never calls MCP
  or a live Python rebuild in the request path.

```text
  GH Actions (weekly / manual)          DraftLab
  ───────────────────────────           ────────
  build_benchmarks.py                   Worker / Node
  build_factors.py                        └─ read R2 (or FS cache)
  check_artifact_count.py                 └─ else bootstrap JSON
       │
       └─ wrangler r2 object put ──► R2
```

## Sanity check

Before upload, `tools/check_artifact_count.py` fails the job if the new
`counts.players` is below 85% of the previous R2 object (or committed
`artifacts/player_factors.json` on first run).

## Local uvicorn (`data_api/`)

Optional for hacking. Not part of production go-live. `fly.toml` is deprecated.

## DraftLab env

- Worker: R2 binding `ARTIFACTS` → bucket `draftlab-artifacts`
- Node: `DRAFTLAB_ARTIFACT_CACHE_DIR` (optional); offline
  `SLEEPER_MCP_ARTIFACT_PATH`
- No `SLEEPER_DATA_API_URL` / `DRAFTLAB_DATA_TOKEN` in production

## Non-goals

- Fly or Cloudflare Containers for the Python job
- Synchronous rebuild inside DraftLab request path
- Horizon MCP on the DraftLab path
- Postgres `player_factor_inputs`
- Removing bootstrap files

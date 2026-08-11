# Go-live: GitHub Actions → R2

Publish sleeperMCP artifacts to Cloudflare R2; DraftLab reads them. No Fly.

## Secrets (sleeperMCP GitHub repo)

| Secret | Purpose |
| --- | --- |
| `CLOUDFLARE_API_TOKEN` | API token with **Account → Workers R2 Storage → Edit** (or Object Read & Write on `draftlab-artifacts`) |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account id (dashboard sidebar) |

Create the token at https://dash.cloudflare.com/profile/api-tokens  
(use “Custom token” → Account → Workers R2 Storage → Edit).

## One-time Cloudflare setup

0. **Enable R2** once in the dashboard (required before any bucket API works):
   https://dash.cloudflare.com/?to=/:account/r2  
   Accept the R2 terms if prompted (error `10042` = R2 not enabled yet).

1. Create R2 bucket **`draftlab-artifacts`** (name must match the workflow and DraftLab `wrangler.jsonc`).

   From DraftLab — always use the npm scripts (they run
   `node --use-system-ca …/wrangler.js` so Avast HTTPS scanning does not break
   TLS). Do **not** run bare `npx wrangler` on this machine:

   ```powershell
   cd fantasy-football-draft-optimizer\fantasy-football-draft-optimizer\apps\worker
   npm run login          # browser OAuth once
   npm run r2:create      # creates draftlab-artifacts
   npm run r2:list        # confirm
   ```

2. Ensure the DraftLab Worker binding exists:
   - `apps/worker/wrangler.jsonc` → `"bucket_name": "draftlab-artifacts"`, binding `ARTIFACTS`.

## First publish

1. Push the workflow (`.github/workflows/publish-artifacts.yml`) to `main` (or your default branch).
2. GitHub → **Actions** → **publish-artifacts** → **Run workflow**.
3. Wait for green. Typical warm run is tens of seconds to a few minutes.
4. Confirm objects in the R2 dashboard (or):
   ```bash
   npx wrangler r2 object get draftlab-artifacts/artifacts/player_factors.json --file=- --remote
   npx wrangler r2 object get draftlab-artifacts/artifacts/benchmarks.json --file=- --remote
   ```
   (set `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` in the shell.)

## DraftLab Worker deploy

```bash
cd fantasy-football-draft-optimizer/fantasy-football-draft-optimizer/apps/worker
npx wrangler deploy
```

No data-job secrets. R2 binding only.

## Verification checklist

- [ ] Manual `workflow_dispatch` succeeds
- [ ] R2 has `artifacts/player_factors.json` and `artifacts/benchmarks.json`
- [ ] Worker `/api/players` returns a full board
- [ ] Worker logs show `factors=cache` (or `stale_cache`) after R2 is populated — not only bootstrap
- [ ] Empty/new Worker before first Action still serves **bootstrap** (draftable)
- [ ] Sharp player-count drop fails the Action before upload (`check_artifact_count.py`)

## Ongoing

- Cron: Mondays 12:00 UTC
- Manual: Actions → **Run workflow** when ADP moves mid-week
- Local Node cache: copy from R2 into `apps/api/.cache/artifacts/artifacts/*.json` or set `SLEEPER_MCP_ARTIFACT_PATH`

## Not used in production

- Fly (`fly.toml` deprecated)
- Horizon MCP for DraftLab data
- `SLEEPER_DATA_API_URL` / live `data_api` rebuild in the Worker path

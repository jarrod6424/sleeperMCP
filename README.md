# Sleeper MCP (read-only)

A [Model Context Protocol](https://modelcontextprotocol.io) server exposing a
Sleeper fantasy football league to any MCP client — Claude on web, desktop and
mobile, Claude Code, Cursor.

It is **read-only by construction**. The Sleeper API
(https://docs.sleeper.com) only supports `GET`, has no authentication and no
write paths, and this server only ever issues those reads. It cannot set
lineups, make trades, drop players, or change anything in your league.

39 tools across five data sources (`waiver_advice` and `grade_team` added
2026-09-03; `analyze_trade` now prices dynasty picks). See
[docs/BUILD_NOTES.md](docs/BUILD_NOTES.md)
for architecture, [CHANGELOG.md](CHANGELOG.md) for the advice-tool release, and
[docs/p0-waiver-trade-picks-implementation-plan.md](docs/p0-waiver-trade-picks-implementation-plan.md)
for pick-curve constants.

## Data sources

| Source | Provides | Notes |
| --- | --- | --- |
| [Sleeper API](https://docs.sleeper.com) | league, rosters, drafts, transactions | Documented, stable |
| `api.sleeper.com` | weekly projections | **Undocumented.** May change without notice |
| [FantasyCalc](https://fantasycalc.com) | trade values | Third party, format-aware |
| [FantasyFootballCalculator](https://fantasyfootballcalculator.com) | ADP | Third party |
| [nflverse](https://github.com/nflverse) | stats, snaps, depth charts, injuries | MIT licensed |

**Yahoo Fantasy:** League tools accept `platform="yahoo"` — including
`list_my_leagues`, `scout_team`, `get_matchups`, `get_managers`, transactions,
drafts, `get_available_players`, `start_sit_advice`, `get_projections`,
`get_trade_values`, `get_auction_budgets`, `value_my_roster`, `analyze_trade`,
`get_adp`, `get_dynasty_tiers`, `waiver_advice`, `grade_team`, and
`get_playoff_bracket` (Yahoo returns playoff-week scoreboards; no Sleeper-style
bracket). Yahoo pick tokens in `analyze_trade` are listed in `unpriced_assets`
rather than invented. Yahoo roster/draft/FA
rows also attach a `sleeper_id` via `resolve_player_crosswalk` when the join
is known. Reception scoring is read from Yahoo `stat_modifiers` when present
(else `YAHOO_SCORING_FORMAT`). Set `YAHOO_AUCTION_BUDGET` for auction $
targets when Yahoo does not expose a budget. Run `python tools/yahoo_auth.py`
once to obtain tokens, then set `YAHOO_LEAGUE_KEY` and `YAHOO_TEAM_NAME` in
`.env`. Sleeper remains the default when `platform` is omitted.

Each source has its own HTTP client so a failure in one cannot take down tools
that do not depend on it. Tools drawing on unofficial sources are marked
`[UNOFFICIAL]` in their descriptions.

## Tools

### League

| Tool | What it returns |
| --- | --- |
| `get_nfl_state` | Current NFL week, season, season type |
| `list_my_leagues` | Leagues across Sleeper and/or Yahoo (portfolio view) |
| `get_league` | Settings, scoring, roster slots, team count |
| `get_league_full` | Unfiltered league data including internal Sleeper fields |
| `get_managers` | Managers with team names and commissioner flag |
| `get_rosters` | Rosters joined with managers, records, named players |
| `get_standings` | Standings sorted by wins then points for |
| `get_matchups` | Weekly matchups paired by opponent with scores |
| `get_playoff_bracket` | Winners or losers bracket |

### Your team

| Tool | What it returns |
| --- | --- |
| `get_my_team` | Your roster, record, rank, this week's and next week's matchup |
| `get_my_roster_id` | Resolves you to a roster_id and team name |
| `scout_team` | The same report for any team, by team name or manager |

### Transactions and picks

| Tool | What it returns |
| --- | --- |
| `get_transactions` | Trades, waivers and free-agent moves for one week |
| `recent_moves` | The last N weeks combined, newest first |
| `get_traded_picks` | Traded draft picks, including future picks |
| `get_drafts` / `get_draft` | Drafts for the league; order and slot mapping |
| `get_draft_picks` | Every pick in order, with names resolved |
| `get_draft_traded_picks` | Picks traded within a specific draft |

### Players

| Tool | What it returns |
| --- | --- |
| `search_player` | Find players by partial name |
| `get_player` | Full detail for one player_id |
| `get_available_players` | Free agents not on any roster (raw wire; use `waiver_advice` for ranked claims) |
| `get_trending_players` | Most added or dropped league-wide |
| `resolve_player_crosswalk` | Map Sleeper ↔ Yahoo player IDs |
| `player_crosswalk_stats` | Coverage of yahoo_id joins on the player map |
| `get_user` | Look up a Sleeper user |

### Analysis

| Tool | What it returns |
| --- | --- |
| `get_projections` | Weekly projections in your league's scoring format |
| `start_sit_advice` | Optimal lineup versus current, with the point gap |
| `get_trade_values` | FantasyCalc values for your exact format |
| `get_auction_budgets` | FantasyCalc values scaled into auction $ fair/max bids |
| `value_my_roster` | Total roster value, each player valued and ranked |
| `analyze_trade` | Compare two sides of a trade, including dynasty pick tokens (`2027 1st`, `2027 Round 1`, `2027 1st from TEAM`). Picks are heuristic schedule values, not FantasyCalc quotes; unpriceable assets go in `unpriced_assets`. |
| `waiver_advice` | Ranked waiver/FAAB claims for a roster (dynasty weights trade value above weekly projection), with drop suggestions. Prefer this over `get_available_players` when you want a recommendation. |
| `grade_team` | Contender vs rebuilder classification (`championship_contender` / `playoff_hopeful` / `mid_pack` / `rebuilder` / `tank`), positional grades, and up to three next moves. |
| `get_adp` | ADP joined to trade value — where the market drafts a player versus what it thinks he is worth |
| `get_dynasty_tiers` | Dynasty values grouped into market tiers |
| `custom_score_player` | A custom, opinionated 0-100 score with a component breakdown. Not a projection — see [docs/SCORING_COMPARISON.md](docs/SCORING_COMPARISON.md) |

### NFL context

| Tool | What it returns |
| --- | --- |
| `get_player_stats` | Weekly stats: targets, target share, WOPR, air yards, fantasy points |
| `get_snap_counts` | Snap counts and participation percentages by week |
| `get_depth_chart` | Team depth chart with personnel grouping |
| `get_injuries` | Injury report with practice participation |
| `get_team_offense_crowding` | How a team distributes touches, with a concentration index |

Player IDs are resolved to names automatically. The full player map is ~5 MB,
cached on disk and refreshed at most once every 18 hours, matching Sleeper's
guidance.

## Layout

```
server.py          @mcp.tool() definitions — thin wrappers, no logic
sleeper_core/      the data layer. No MCP imports anywhere, by design
  config.py        endpoints, identity defaults, cache paths, TTLs
  http.py          HTTP clients, caching, conditional GET
  players.py       player map and name enrichment
  league.py        rosters, standings, identity, matchups, transactions, free agents
  projections.py   projections and lineup optimization
  values.py        FantasyCalc trade values
  picks.py         draft-pick token parse + schedule heuristic
  trade.py         pick-aware analyze_trade
  waiver.py        waiver_advice scoring
  grade.py         grade_team classification
  advice.py        shared advice envelope
  auction.py       FantasyCalc → auction $ fair/max bid targets
  adp.py           FantasyFootballCalculator ADP
  stats.py         nflverse stats and depth charts
  offense.py       usage concentration and OC tiers
tools/             artifact generation + auction_budget.py CLI
tests/             golden-output regression harness
```

`sleeper_core` is importable on its own:

```python
from sleeper_core import league, values

rosters = league.compute_rosters("1312218810614300672", include_players=True)
fmt     = values.league_format("1312218810614300672")
```

That path skips MCP entirely — no JSON-RPC, no session handshake, no auth,
since every upstream is a public read. Use it for anything latency-sensitive.

## Install and connect

Three ways to run this, in order of how much setup they need:

1. **Use the deployed remote server** — nothing to install, just add a
   connector. What most clients should do.
2. **Run it locally over stdio** — for Claude Desktop, when you want your own
   league config or you're developing against it.
3. **Run it locally over HTTP** — for Claude Code, Cursor, or anything that
   speaks Streamable HTTP, pointed at your own machine instead of the deploy.

All three run the exact same 37 tools against the exact same read-only
upstreams — the difference is only where the process lives and how a client
reaches it.

### 1. Deployed remote server (no install)

Already running on [Prefect Horizon](https://gofastmcp.com/deployment/prefect-horizon)
at **`https://jlg-sleeper.fastmcp.app/mcp`**, entrypoint `server.py:mcp`, behind
Horizon's built-in OAuth. One connector registration covers Claude web,
desktop **and** mobile — Anthropic's infrastructure makes the request, not
your device, which is also why a `localhost` URL can never work as a custom
connector (see option 3 for that case).

**Claude (web / desktop / mobile).** Settings → Connectors → Add custom
connector → paste the URL above → Connect, then complete the OAuth prompt in
the popup that opens. Configured once, available in every conversation on
every device signed into that account.

**Claude Code.**

```bash
claude mcp add --transport http sleeper https://jlg-sleeper.fastmcp.app/mcp
```

Follow the OAuth prompt on first use. `claude mcp list` confirms it connected;
`get_nfl_state` is a good first call since it takes no arguments.

**Cursor.** Add to `.cursor/mcp.json` (project) or `~/.cursor/mcp.json`
(global):

```json
{
  "mcpServers": {
    "sleeper": {
      "url": "https://jlg-sleeper.fastmcp.app/mcp"
    }
  }
}
```

Cursor prompts for the OAuth flow the first time the server is used.

**Access is not open.** Horizon's OAuth restricts connections to the deploying
account's org — this is not "share the URL and anyone can connect." Adding a
second person means adding them on the Horizon/FastMCP Cloud side first;
confirm that's available on the current plan before promising someone else
access. See [Custom connectors via remote MCP](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
and [Connector authentication](https://claude.com/docs/connectors/building/authentication)
for how the OAuth handshake itself works.

### 2. Local install (stdio or HTTP)

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
```

Copy `.env.example` to `.env` and adjust league/identity values if this isn't
your league — see [Configuration](#configuration) below for what each
variable does.

### 3a. Claude Desktop, local (stdio)

Edit `claude_desktop_config.json` (`%APPDATA%\Claude\` on Windows,
`~/Library/Application Support/Claude/` on macOS):

```json
{
  "mcpServers": {
    "sleeper": {
      "command": "C:\\path\\to\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\server.py"],
      "env": {
        "SLEEPER_LEAGUE_ID": "1312218810614300672",
        "SLEEPER_USERNAME": "JarrodLee",
        "SLEEPER_TEAM_NAME": "Pine Bluff Escapees"
      }
    }
  }
}
```

No `MCP_HTTP` here — leaving it unset is what makes the server speak stdio.
Restart Claude Desktop after editing.

### 3b. Claude Code / Cursor, local (HTTP)

Start the server:

```bash
MCP_HTTP=1 python server.py        # Streamable HTTP on :8000
python tests/smoke_http.py         # verify: handshake, tool list, live call
```

Then point a client at `http://localhost:8000/mcp` the same way you would the
deployed URL in option 1, minus OAuth — a local server has none:

```bash
claude mcp add --transport http sleeper-local http://localhost:8000/mcp
```

```json
// .cursor/mcp.json
{ "mcpServers": { "sleeper-local": { "url": "http://localhost:8000/mcp" } } }
```

Useful for testing a change before it's deployed, since the remote instance
runs whatever was last pushed to `main` and redeployed on Horizon — a local
HTTP server reflects your working tree instead.

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `SLEEPER_LEAGUE_ID` | `1312218810614300672` | Default league; every tool also takes `league_id` |
| `SLEEPER_USERNAME` | `JarrodLee` | Resolves "my team" |
| `SLEEPER_TEAM_NAME` | `Pine Bluff Escapees` | Fallback for matching your team |
| `SLEEPER_SPORT` | `nfl` | Sport |
| `SLEEPER_CACHE_DIR` | `~/.cache/sleeper-mcp` | Cache location. **Set explicitly on any host with a non-writable home** |
| `SLEEPER_PLAYCALLER_TIERS_FILE` | `./playcaller_tiers.json` | Override the bundled play-caller tier file |
| `MCP_HTTP` | unset | Set for Streamable HTTP instead of stdio |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | HTTP bind address |
| `MCP_WARM` | unset | Warm the player map at import. **Set on a managed host**, where the `__main__` block never runs |
| `USE_OS_TRUSTSTORE` | unset | Route TLS through the OS trust store. Needed behind a corporate inspecting proxy, not on a cloud host |

See `.env.example`.

## Artifacts

```bash
python tools/build_benchmarks.py            # -> artifacts/benchmarks.json
python tools/build_factors.py               # -> artifacts/player_factors.json
```

CLI builders still write JSON under `artifacts/`. DraftLab's happy path is
**GitHub Actions → Cloudflare R2** (not Horizon MCP, not Fly):

- Workflow: [`.github/workflows/publish-artifacts.yml`](.github/workflows/publish-artifacts.yml)
- Go-live: [`docs/GO_LIVE_ACTIONS_R2.md`](docs/GO_LIVE_ACTIONS_R2.md)
- Design: [`docs/superpowers/specs/2026-08-10-draftlab-data-job-r2-design.md`](docs/superpowers/specs/2026-08-10-draftlab-data-job-r2-design.md)

Optional local HTTP (`data_api/` + uvicorn) remains for hacking only.

The split with DraftLab: this repo owns **what the numbers are** — factors,
benchmarks, the player-ID crosswalk. DraftLab owns **what they mean** — grading,
archetypes, risk, scoring, strategy. See
[docs/INTEGRATION_PROPOSAL.md](docs/INTEGRATION_PROPOSAL.md).

Benchmarks are the top-1-to-3 seasonal average per position — a *ceiling*, not a
typical value. Every artifact embeds a calibration block checking that the method
still reproduces DraftLab's published numbers, so a broken pipeline is visible
rather than silent. Factors that cannot be sourced are emitted as explicit nulls
with a reason instead of being omitted.

## Tests

```bash
pytest tests/test_golden.py -q     # after any change
python tests/capture_golden.py     # only when output changes on purpose
```

63 golden cases covering all 37 tools, captured against live APIs and compared
on every run. Two modes: exact equality where output is stable once week and
season are pinned, and structural comparison where numbers legitimately drift
(projections get revised, trade values move daily).

Most cases target the *previous* completed season. It is frozen, so it never
produces false failures, and it has real rosters, a real draft and a real
transaction history — unlike a league sitting in `pre_draft`.

**Re-capture only when you meant to change output.** A failing diff is the
harness doing its job.

## Notes

- Sleeper asks clients to stay under ~1000 calls/minute. Caching keeps normal
  use far below that.
- Usernames can change; the API and this server prefer stable numeric IDs.
- Identity is currently process-wide, so a second user asking about "my team"
  gets the configured user's roster. `league.resolve_my_roster` already accepts
  explicit `username` and `team_name`; the tool signatures have not been
  threaded through yet.

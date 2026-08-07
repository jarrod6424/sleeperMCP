# Sleeper MCP (read-only)

A [Model Context Protocol](https://modelcontextprotocol.io) server exposing a
Sleeper fantasy football league to any MCP client — Claude on web, desktop and
mobile, Claude Code, Cursor.

It is **read-only by construction**. The Sleeper API
(https://docs.sleeper.com) only supports `GET`, has no authentication and no
write paths, and this server only ever issues those reads. It cannot set
lineups, make trades, drop players, or change anything in your league.

36 tools across five data sources. See [docs/BUILD_NOTES.md](docs/BUILD_NOTES.md)
for architecture, deployment details, and a record of what broke while building
it.

## Data sources

| Source | Provides | Notes |
| --- | --- | --- |
| [Sleeper API](https://docs.sleeper.com) | league, rosters, drafts, transactions | Documented, stable |
| `api.sleeper.com` | weekly projections | **Undocumented.** May change without notice |
| [FantasyCalc](https://fantasycalc.com) | trade values | Third party, format-aware |
| [FantasyFootballCalculator](https://fantasyfootballcalculator.com) | ADP | Third party |
| [nflverse](https://github.com/nflverse) | stats, snaps, depth charts, injuries | MIT licensed |

Each source has its own HTTP client so a failure in one cannot take down tools
that do not depend on it. Tools drawing on unofficial sources are marked
`[UNOFFICIAL]` in their descriptions.

## Tools

### League

| Tool | What it returns |
| --- | --- |
| `get_nfl_state` | Current NFL week, season, season type |
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
| `get_available_players` | Free agents not on any roster |
| `get_trending_players` | Most added or dropped league-wide |
| `get_user` | Look up a Sleeper user |

### Analysis

| Tool | What it returns |
| --- | --- |
| `get_projections` | Weekly projections in your league's scoring format |
| `start_sit_advice` | Optimal lineup versus current, with the point gap |
| `get_trade_values` | FantasyCalc values for your exact format |
| `value_my_roster` | Total roster value, each player valued and ranked |
| `analyze_trade` | Compare two sides of a trade |
| `get_adp` | ADP joined to trade value — where the market drafts a player versus what it thinks he is worth |
| `get_dynasty_tiers` | Dynasty values grouped into market tiers |
| `score_player` | Composite 0-100 dynasty score with a component breakdown |

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
server.py          36 @mcp.tool() definitions — thin wrappers, no logic
sleeper_core/      the data layer. No MCP imports anywhere, by design
  config.py        endpoints, identity defaults, cache paths, TTLs
  http.py          HTTP clients, caching, conditional GET
  players.py       player map and name enrichment
  league.py        rosters, standings, identity, matchups, transactions
  projections.py   projections and lineup optimization
  values.py        FantasyCalc trade values
  adp.py           FantasyFootballCalculator ADP
  stats.py         nflverse stats and depth charts
  offense.py       usage concentration and OC tiers
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

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
```

### Claude Desktop (stdio)

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

### Remote (HTTP)

```bash
MCP_HTTP=1 python server.py        # Streamable HTTP on :8000
python tests/smoke_http.py         # verify: handshake, tool list, live call
```

Deployed on [Prefect Horizon](https://gofastmcp.com/deployment/prefect-horizon)
with entrypoint `server.py:mcp` and OAuth enabled. One connector registration
covers every Claude client including mobile — Anthropic's infrastructure makes
the request, not your device, which is why a localhost URL will not work as a
custom connector.

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `SLEEPER_LEAGUE_ID` | `1312218810614300672` | Default league; every tool also takes `league_id` |
| `SLEEPER_USERNAME` | `JarrodLee` | Resolves "my team" |
| `SLEEPER_TEAM_NAME` | `Pine Bluff Escapees` | Fallback for matching your team |
| `SLEEPER_SPORT` | `nfl` | Sport |
| `SLEEPER_CACHE_DIR` | `~/.cache/sleeper-mcp` | Cache location. **Set explicitly on any host with a non-writable home** |
| `SLEEPER_OC_TIERS_FILE` | `./oc_tiers.json` | Override the bundled OC tier file |
| `MCP_HTTP` | unset | Set for Streamable HTTP instead of stdio |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | HTTP bind address |
| `MCP_WARM` | unset | Warm the player map at import. **Set on a managed host**, where the `__main__` block never runs |
| `USE_OS_TRUSTSTORE` | unset | Route TLS through the OS trust store. Needed behind a corporate inspecting proxy, not on a cloud host |

See `.env.example`.

## Tests

```bash
pytest tests/test_golden.py -q     # after any change
python tests/capture_golden.py     # only when output changes on purpose
```

62 golden cases covering all 36 tools, captured against live APIs and compared
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

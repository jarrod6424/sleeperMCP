# Sleeper MCP (read-only)

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes
your Sleeper fantasy football league to an MCP client (Claude Desktop, Claude
Code, Cursor, etc.).

It is **read-only by construction**: the underlying Sleeper API
(https://docs.sleeper.com) only supports `GET` requests and has no
authentication or write paths, and this server only ever issues those reads. It
cannot set lineups, make trades, drop players, or change anything in your
league.

Your league is preconfigured to `1312218810614300672`. Every tool also accepts
an optional `league_id` if you want to query another league.

## Tools

| Tool | What it returns |
| --- | --- |
| `get_nfl_state` | Current week, season, and season type |
| `get_league` | League settings, scoring, roster slots |
| `get_managers` | Managers with team names and commissioner flag |
| `get_rosters` | Rosters joined with managers, records, and named players |
| `get_standings` | Standings sorted by wins then points for |
| `get_my_team` | Your roster, record, standings rank, and this week's matchup |
| `get_my_roster_id` | Resolves you to your roster_id and team name |
| `get_matchups` | Weekly matchups paired by opponent with scores |
| `get_transactions` | Trades, waivers, and free-agent moves with named adds/drops |
| `get_traded_picks` | Traded draft picks, including future picks |
| `get_playoff_bracket` | Winners or losers bracket |
| `get_drafts` | Drafts for the league |
| `get_draft` | A specific draft's order and slots |
| `get_draft_picks` | Every pick with resolved player names |
| `search_player` | Find a player_id by partial name |
| `get_player` | Full detail for one player_id |
| `get_trending_players` | Most added or dropped players league-wide |
| `get_user` | Look up a Sleeper user by username or id |

Player IDs (e.g. `4034`) are resolved to names automatically. The full player
map is roughly 5MB, so it is cached on disk and refreshed at most once every 18
hours, which matches Sleeper's guidance.

## Setup

Requires Python 3.10 or newer.

```bash
cd sleeper-mcp
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Quick check that it runs (Ctrl-C to stop):

```bash
python server.py
```

## Connect to Claude Desktop

Edit your `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Add this entry, using the **absolute** path to the Python inside your venv and
to `server.py`:

```json
{
  "mcpServers": {
    "sleeper": {
      "command": "/absolute/path/to/sleeper-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/sleeper-mcp/server.py"],
      "env": {
        "SLEEPER_LEAGUE_ID": "1312218810614300672",
        "SLEEPER_USERNAME": "JarrodLee",
        "SLEEPER_TEAM_NAME": "Pine Bluff Escapees"
      }
    }
  }
}
```

On Windows the command path ends in `.venv\\Scripts\\python.exe`. Restart Claude
Desktop, and the Sleeper tools will appear.

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `SLEEPER_LEAGUE_ID` | `1312218810614300672` | Default league for league tools |
| `SLEEPER_USERNAME` | `JarrodLee` | Your Sleeper username, used to resolve "my team" |
| `SLEEPER_TEAM_NAME` | `Pine Bluff Escapees` | Fallback for matching your team |
| `SLEEPER_SPORT` | `nfl` | Sport (Sleeper currently supports `nfl`) |
| `SLEEPER_CACHE_DIR` | `~/.cache/sleeper-mcp` | Where the player map is cached |

## Notes

- Sleeper asks clients to stay under ~1000 calls/minute. Normal chat use is far
  below that; the player map and per-session caching keep calls low.
- Usernames can change, so the API and this server prefer stable numeric IDs.

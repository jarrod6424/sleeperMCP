# Sleeper MCP — build record and next steps

**Status: deployed and working.** `https://jlg-sleeper.fastmcp.app/mcp`, OAuth, reachable from every Claude client including mobile. 37 tools, four upstreams, one 15-commit session on 2026-08-06.

This started as a plan for making a local stdio server reachable from a phone. It's now a record of what was actually built, what broke, and what's left — written for the version of you that comes back to this in three weeks, and for your friend who wasn't here.

---

## 1. What exists

```
server.py              1367   37 @mcp.tool() definitions, nothing else of substance
sleeper_core/
  config.py             110   endpoints, identity defaults, cache paths, TTLs
  http.py               276   5 clients, caches, conditional GET, gzip preference
  players.py            104   the 5 MB player map and name enrichment
  league.py             337   rosters, standings, identity, matchups, transactions
  projections.py        143   undocumented Sleeper endpoint, lineup optimizer
  values.py             101   FantasyCalc trade values
  adp.py                318   FantasyFootballCalculator ADP
  stats.py              250   nflverse stats and depth charts
  offense.py            153   usage concentration, OC tiers
tests/
  cases.py              467   62 golden cases, comparison logic
  test_golden.py         97   pytest runner
  capture_golden.py     103   baseline capture
  smoke_http.py         117   live MCP handshake against any URL
```

`server.py` began at 2,102 lines containing everything. `sleeper_core` has no MCP imports anywhere — that's a hard rule, and it's what lets the draft app import it directly.

### Data sources

| Source | Used for | Character |
|---|---|---|
| `api.sleeper.app/v1` | league, rosters, players, drafts | Documented, stable, no auth |
| `api.sleeper.com` | projections | **Undocumented.** Can vanish without notice |
| `api.fantasycalc.com` | trade values | Semi-official, no published rate limits |
| `fantasyfootballcalculator.com` | ADP | Added this session; FantasyCalc has no ADP |
| `github.com/nflverse` | stats, snaps, depth charts, injuries | MIT, large files |

Each gets its own httpx client so one failure doesn't cascade. That discipline earned its keep — the 50 MB depth chart problem stayed contained to one tool.

---

## 2. Deployment

Prefect Horizon (FastMCP Cloud), free tier, deploys from `github.com/jarrod6424/sleeperMCP` (private).

- **Entrypoint:** `server.py:mcp`
- **Requirements:** `requirements.txt`
- **Auth:** Horizon's built-in OAuth. Skips Claude's beta request-header path, which has open bugs.

Environment variables:

```
SLEEPER_LEAGUE_ID=1312218810614300672
SLEEPER_USERNAME=JarrodLee
SLEEPER_TEAM_NAME=Pine Bluff Escapees
SLEEPER_CACHE_DIR=/tmp/sleeper-mcp
MCP_WARM=1
```

`SLEEPER_CACHE_DIR` is not optional — the default is `~/.cache`, and if the container's home isn't writable, `mkdir` raises and every tool needing the player map fails.

`MCP_WARM=1` is what makes cache warming fire. See §4.

Locally, `USE_OS_TRUSTSTORE=1` is needed on a corporate network doing TLS inspection. Not on the host — there's no inspecting proxy in a datacenter, and that question is now settled.

---

## 3. The golden harness

63 cases covering all 37 tools. Run `pytest tests/test_golden.py -q` after any change.

Two modes, because not everything is stable:

- **STRICT** (43 cases) — deep equality after stripping volatile fields. Used where output is fixed once week and season are pinned.
- **SHAPE** (19 cases) — key names and types only. Used where numbers legitimately drift: projections get revised, trade values move daily, ADP is a rolling aggregate.

**The most useful design decision was pinning to the previous season.** The 2026 league is `pre_draft` — empty rosters, zero standings, no transactions. Cases against it confirm almost nothing, because the enrichment helpers barely execute. The 2025 league is complete and frozen: 252 real draft picks, 12 full rosters, real transaction history. Those `prev_*` cases carry the actual verification weight, and they'll never drift.

Volatile fields are masked in both modes. That took two passes to get right — see §4.

### What the harness was worth

It caught **nothing** during the six extraction commits. Empty diff every time. That's the boring result you want.

Its actual value was making deliberate changes safe. Three bugs got fixed this session, each intentionally changing output — and because everything else stayed green, "I fixed this" was distinguishable from "I broke something." Without that, each fix would have been a leap.

---

## 4. What broke, and what it taught

Recorded honestly, including the things I got wrong.

### `get_depth_chart` had never worked

nflverse changed data source after the 2024 season and renamed every column the code read:

```
2025+    dt   team       player_name  pos_abb   pos_rank    pos_grp
<=2024   -    club_code  full_name    position  depth_team  formation
```

It filtered on `club_code` against files that no longer have that column, matching nothing, every time. Now supports both layouts, detected by column presence, because 2024 queries still hit the old files.

**I then over-generalized.** Seeing `LDE`/`LDT` on the defensive line, I assumed receivers were `LWR`/`RWR`/`SWR` and built alias expansion for it. Real data uses plain `WR`. The aliases are harmless and still earn their keep for `RB` → `HB`/`FB`, but the inference was wrong. Only the defensive line uses alignment codes.

### The 50 MB file that killed the container

Replacing `week` with a `dt` load timestamp turned `depth_charts_2025.csv` from one snapshot per week into every snapshot ever taken: **50.5 MB, up from 3.2 MB in 2024**. `nflverse_csv` materialized all of it as a list of dicts — hundreds of megabytes of Python objects. Fine on a 32 GB desktop, fatal in a small container. The worker died and the tool returned an opaque error.

Diagnosis came from isolation: `get_player_stats` and `get_injuries` both worked remotely, so nflverse and the datacenter IP were fine, and only the depth chart file was implicated.

Fixed three ways:

1. **Filter during parse.** `nflverse_csv` takes a predicate; only the requested team's rows become objects. Also bypasses the row cache, which was itself holding 50 MB for six hours.
2. **Prefer gzip.** nflverse publishes `.csv.gz` at 10.2 MB versus 50.5 MB. The code already handled `.gz` — the branch existed and was unused. Pre-2024 seasons have no gzip variant, so a 404 falls back.
3. **Conditional GET.** GitHub serves ETags. `depth_charts_2026.csv` hadn't changed in two months, so a 6h TTL was re-downloading an identical file four times a day. Now an unchanged file costs a 304 with no body.

Parquet would be 2.46 MB — 20x — but needs pyarrow, too heavy a dependency for one dataset.

### `get_adp` had never returned data

It read `maybeAdp` from FantasyCalc's `/values/current`. That field exists in the response and is `null` for every player, with or without an `includeAdp` parameter. Every row was skipped; the list came back empty from day one. The docstring described behavior that couldn't happen.

FantasyCalc's own API walkthrough gets ADP from FantasyFootballCalculator instead. So that's now a fifth source.

**Format selection is a chain, not a mapping**, because the truest label isn't always the useful one:

```
2qb      2026   217 players   2,464 drafts
dynasty  2026     0 players      51 drafts   <- correct label, no data
ppr      2026   249 players   4,767 drafts
```

The league is dynasty superflex. "Dynasty" describes it best and answers nothing. Superflex and 2QB draft near-identically, so `2qb` is both a fair proxy and where the sample is. The chain takes the first format with a real sample, falling back a year if needed.

**The name join took four passes**, and each failure was a different kind:

| | Matched | Fix |
|---|---|---|
| 1 | 183/217 | exact normalized name |
| 2 | 198/217 | key on `(name, position)` — Josh Allen the Bills QB was losing to Josh Allen the Jaguars linebacker, so the value join silently found nothing |
| 3 | 216/217 | strip generational suffixes — Sleeper stores "Michael Pittman", FFC sends "Michael Pittman Jr." |
| 4 | 217/217 | NFKD accent folding — a bare `[^a-z0-9]` filter turned "Piñeiro" into "pieiro" by deleting the letter along with its tilde |

The collision case is the instructive one. It didn't error; it produced a row with no trade value, which reads as missing data rather than a wrong join. Adding `unmatched_sample` to the output is what made passes 3 and 4 findable at all.

### Warming never fired

`_warm_caches()` lived in `if __name__ == "__main__"`. A managed host is given `server.py:mcp` and **imports** the module to get the object — it never executes it as a script. Warming now happens at import behind `MCP_WARM`, which is the only hook Horizon triggers.

Warming is opt-in rather than automatic so test imports stay cheap and stdio launches don't put a 5 MB fetch in front of every Claude Desktop restart.

### The harness had its own bug

`VOLATILE_KEYS` only fed `strip_volatile`, which runs in STRICT mode. But SHAPE mode records *type names*, and a nullable field's type flips with the sample — `injury_status` reads `"str"` in a slice containing an injured player and `"null"` in one that doesn't. The baseline already held `available_players="str"` next to `available_rb="null"` for exactly this reason, captured seconds apart.

`mask_volatile` now blanks those markers on both sides at compare time, so existing baselines stay valid and a missing key still fails.

---

## 5. What's left

### Multi-user identity — do this before your friend connects

`SLEEPER_USERNAME` and `SLEEPER_TEAM_NAME` are process-wide. Your friend asking "what's my team?" gets Pine Bluff Escapees. Not an error, just quietly wrong, which is worse.

The seam is already built: `league.resolve_my_roster(lid, username=None, team_name=None)` takes explicit identity and falls back to config. What's left is threading it through `get_my_team`, `get_my_roster_id`, and `scout_team`.

Alternatives if you'd rather not: deploy twice with different identity variables, or have him use `scout_team` with his manager name. Note Horizon's OAuth restricts connections to your org — verify he can be added on the free tier before designing around per-user login.

### Alias cleanup

`server.py` still has ~46 shims like `_get = _http.get_json`. Deliberate — it kept the extraction diffs pure. Purely cosmetic now.

### The app

`sleeper_core` is importable today. No MCP, no JSON-RPC, no auth — Sleeper, FantasyCalc, FFC and nflverse are all public reads.

Draft-day notes that still hold:

- **Load the world once at draft start.** Player map, ADP, values, depth charts for teams you care about. Then run off memory. `players.warm()` exists for this.
- **Only `get_draft_picks` needs to be live.** Poll 2-5s inside your window, back off between. Sleeper has no pick webhook.
- **All 37 tools are synchronous.** Under HTTP, FastMCP runs them in a threadpool, so each in-flight call holds a thread blocked on I/O. Irrelevant for one person on a phone, a real ceiling for a polling app with two users. In-process you control your own concurrency — another reason the app should import rather than call over MCP.
- **Auction and rookie drafts need different math.** Auction wants budget-aware nomination; rookie drafts want pick-value curves rather than ADP. `values.league_format` is the right foundation; `draft.type` and `draft.settings` extend it.

### Known data gaps

- `playcaller_tiers.json` is hand-maintained and has no external validation. It rates
  whoever actually calls plays — on 15 of 32 teams that's the head coach, not the titled
  coordinator, so rating the coordinator would frequently rate someone with no say in the
  design. A departed play-caller produces a plausible-looking tier rather than an error,
  which is why the file carries an `_updated` date and both tools that read it warn when
  it's older than 200 days. Coaching cycles run January to March; re-check every offseason.
- Sleeper's projections endpoint is undocumented and unversioned. If projections break, nothing else should.

---

## 6. Reference

```powershell
# local dev
.\.venv\Scripts\Activate.ps1
$env:USE_OS_TRUSTSTORE=1

pytest tests\test_golden.py -q              # after any change
python tests\capture_golden.py              # only when output changes on purpose
python tests\smoke_http.py <url>            # live MCP handshake, local or deployed

$env:MCP_HTTP=1; python server.py           # HTTP locally on :8000
python server.py                            # stdio, for Claude Desktop
```

**Re-capture the baseline only when you meant to change output.** A failing diff is the harness working. The one thing that will produce false failures is a roster move mid-refactor, since the current-league cases are STRICT against a live league — the `prev_*` cases are frozen and unaffected.

### Cache TTLs

| Dataset | TTL | Why |
|---|---|---|
| Player map | 18h | Sleeper asks for at most one fetch/day |
| Depth charts | 24h | Teams file weekly; unchanged for months in the offseason |
| nflverse stats | 24h | Only changes once games complete |
| Injuries | 1h | The one that genuinely needs freshness |
| Projections | 6h | Revised through the week as news breaks |
| FantasyCalc | 6h | Recomputed daily |
| ADP | 24h | 30-day rolling aggregate, moves slowly |

With conditional GET these are "how often to check", not "how often to download".

### Sources

- [Prefect Horizon](https://gofastmcp.com/deployment/prefect-horizon) · [FastMCP HTTP deployment](https://gofastmcp.com/deployment/http)
- [Custom connectors via remote MCP](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) · [Connector authentication](https://claude.com/docs/connectors/building/authentication)
- [MCP connector — Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)
- [nflverse depth chart dictionary](https://nflreadr.nflverse.com/articles/dictionary_depth_charts.html) — the post-2024 schema change
- [FantasyCalc API walkthrough](https://www.fantasydatapros.com/fantasyfootball/blog/fantasycalc/1) — shows ADP coming from FFC, not FantasyCalc

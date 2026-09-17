# Week-usage tool (nflverse)

A start/sit usage report for one NFL week, grouped by team. The question
it answers is "who actually played, and how did this offense split work
this week?" — the thing people screenshot from Twitter, without the post.

This side reports measurements. It does not grade, rank, or recommend a
start. Route % is a participation proxy, not PFF.

## Tool

`get_week_usage(week, season=None, team=None, min_snap_share=None)`

Thin wrapper in `server.py`. Logic in `sleeper_core/usage.py`.

| Arg | Default | Notes |
|---|---|---|
| `week` | current Sleeper NFL week | Pin in tests. Never infer from max(stats.week) silently. |
| `season` | current Sleeper season | Completed seasons are frozen. |
| `team` | all teams | Sleeper `LAR` → nflverse `LA`. |
| `min_snap_share` | `10` if `team` omitted, else `0` | 0–100. Applied after headlines, so a 91% WR is never dropped from the scan layer. |

## Sources (all nflverse, MIT / FTN participation CC-BY-SA)

| Field | File | Definition |
|---|---|---|
| snap share | `snap_counts_{season}.csv` | `offense_pct`, normalized to 0–100 |
| rushes, targets, target share | `stats_player_week_{season}.csv` | `carries`, `targets`, `target_share` (0–1 → 0–100) |
| team dropback rate | `play_by_play_{season}.csv` | dropbacks / (dropbacks + designed runs), 0–100 |
| route proxy | `pbp_participation_{season}.csv` ⨝ dropbacks | player GSIS in `offense_players` / team dropbacks with a participation join, 0–100 |

PBP and participation are filtered **during parse**. The season PBP file is
hundreds of MB; materializing it is how this host OOMs.

## Definitions

**Dropback.** `qb_dropback` truthy when the column exists; otherwise
`pass_attempt` OR `sack` OR `qb_scramble`. Sacks and scrambles count.
Kneels and spikes (`play_type` not in `pass`/`run`) do not.

**Designed run.** `play_type == run` and not a dropback.

**Route proxy.** Not PFF routes. The participation `route` column is the
primary receiver's route type, so we do not use it. This is "was this
offensive player on the field on a dropback." Close enough for start/sit;
label it as a proxy in `limitations`.

Denominator is dropbacks that joined to a participation row. If a team
has dropbacks but zero participation joins, every `route_proxy` is
`null` / `missing:not_recorded` — never a fabricated 0.

**Snap join.** Prefer `pfr_player_id` → nflverse roster `pfr_id` → `gsis_id`
(so snap-count "Zonovan Knight" joins weekly-stats "Bam Knight"). Fall back
to `(normalized full name, team, week)`. Not substring, not first-initial+last
(that collides DJ Moore / David Moore). A colliding key leaves `snap_share`
unset (`missing:not_recorded`) rather than mixing two players.

**Who appears.** Skill positions `QB`/`RB`/`WR`/`TE` plus `FB`/`HB`
folded into RB for headlines. A player with neither offensive snaps nor
targets/carries nor a dropback appearance is omitted.

## Output

Rates are **0–100 percentages** (nflverse `offense_pct` and `target_share`
arrive as 0–1; leaving them mixed with the 0–100 route participation
already in this repo is how a "two WRs over 90%" headline would miss
0.91). Counts stay integers.

Per field: a number or `null`, plus `*_provenance`. `measured` if we
read it; `missing:not_recorded` if the source lacks the row; never omit
the key. A true zero (zero carries, on zero dropbacks) is `measured`.

Team `notes` and top-level `headlines` are the scan layer:

```
PHI: 2 WRs over 90% snap share
PHI: 2 WRs over 90% route proxy
```

Same pattern for RB/WR/TE, threshold 90 after rounding to one
decimal. QBs are excluded from that 90% scan — a starter at 100% snaps
is not news.

**QB lens (same PBP + snaps).** On QB rows only:

| Field | Definition |
|---|---|
| `dropback_share` | this QB's dropbacks / team dropbacks, 0–100 |
| `qb_dropbacks` | dropbacks attributed to `passer_player_id`, or `rusher_player_id` on scrambles |
| `designed_rushes` | run plays that are not dropbacks, rusher = this QB |
| `scramble_rushes` | `qb_scramble` plays |
| `rushes` | unchanged box-score `carries` (designed + scrambles) |

Headlines when two QBs both clear 15% snaps or dropback share:

```
NYG: QB snap split: Jaxson Dart 71%, Russell Wilson 29%
NYG: QB dropback split: Jaxson Dart 74%, Russell Wilson 26%
```

A single 100% starter is not a headline. `route_proxy` on a QB is still
on-field for dropbacks (~100% for the starter) and is not dropback share.

Bye / unpublished week, when a team is requested: structured
`{"error": "no usage data", ...}` with a hint. When all teams are
requested, omit silent byes.

## Out of scope

- Grading, start/sit advice, or DraftLab artifact rebuilds
- Official PFF route participation
- Season-long crowding (`get_team_offense_crowding` already exists)
- A separate QB-only tool — this is the same `get_week_usage` payload

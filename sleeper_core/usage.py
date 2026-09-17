"""One week's offensive usage, grouped by team.

Snap share, rushes, targets, target share, team dropback rate, and a
route proxy (on-field for dropbacks). QBs reuse those sources as
dropback share, designed vs scramble rushes, and a starter/backup split.
Measurements only — no start/sit verdict. Route % is not PFF.

PBP and participation files are filtered during parse. Loading a full
season of play-by-play as Python dicts will OOM a small host.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .config import NFLVERSE_SOURCE, SPORT, STATS_CACHE_TTL
from .http import get_json, nflverse_csv
from .stats import current_season, to_nflverse_team

MEASURED = "measured"
MISSING_NOT_RECORDED = "missing:not_recorded"

SKILL_POSITIONS = {"QB", "RB", "WR", "TE", "FB", "HB", "TB"}
HEADLINE_POS = {"FB": "RB", "HB": "RB", "TB": "RB"}
POS_LABEL = {"QB": "QBs", "RB": "RBs", "WR": "WRs", "TE": "TEs"}
POS_ORDER = ["QB", "RB", "FB", "HB", "TB", "WR", "TE"]
HEADLINE_THRESHOLD = 90.0
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_MISSING = ("", "NA", "NULL", "None")

ROUTE_LIMITATION = (
    "route_proxy is offensive players on the field on dropbacks from "
    "nflverse participation (not official PFF routes)"
)
QB_LIMITATION = (
    "QB dropback_share is the passer/scrambler share of team dropbacks, "
    "not on-field route_proxy; designed_rushes exclude scrambles"
)
UNITS = "rates are 0-100 percentages"
NO_DATA_HINT = "Bye week or nflverse has not published this week yet."


def as_pct(raw: Any) -> float | None:
    """Normalize a 0–1 fraction or 0–100 value to a 0–100 percentage."""
    if raw in (None, *_MISSING):
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val <= 1.0:
        val *= 100.0
    return round(val, 1)


def as_int(raw: Any) -> int | None:
    if raw in (None, *_MISSING):
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def is_truthy(v: Any) -> bool:
    if v in (None, ""):
        return False
    try:
        return float(v) != 0.0
    except (TypeError, ValueError):
        return str(v).strip().lower() in {"true", "t", "yes", "y"}


def is_dropback(row: dict) -> bool:
    if "qb_dropback" in row and row.get("qb_dropback") not in (None, ""):
        return is_truthy(row.get("qb_dropback"))
    return (
        is_truthy(row.get("pass_attempt"))
        or is_truthy(row.get("sack"))
        or is_truthy(row.get("qb_scramble"))
    )


def is_designed_run(row: dict) -> bool:
    return (row.get("play_type") or "") == "run" and not is_dropback(row)


def week_matches(raw: Any, week: int) -> bool:
    try:
        return int(float(raw)) == int(week)
    except (TypeError, ValueError):
        return False


def name_key(name: str) -> str:
    """Full-name key: punctuation stripped, suffixes dropped.

    Not a substring, not first-initial+last — those collide (DJ Moore /
    David Moore) and this project has already shipped that bug once.
    """
    chars = [ch if ch.isalnum() or ch.isspace() else "" for ch in (name or "").lower()]
    parts = [p for p in "".join(chars).split() if p not in _NAME_SUFFIXES]
    return " ".join(parts)


def parse_gsis_ids(raw: Any) -> list[str]:
    if not raw:
        return []
    return [tok for tok in str(raw).replace(";", " ").split() if tok]


def _pos_sort_key(pos: str) -> tuple[int, str]:
    up = (pos or "").upper()
    try:
        return (POS_ORDER.index(up), up)
    except ValueError:
        return (len(POS_ORDER), up)


def team_dropback_stats(pbp_rows: list[dict]) -> dict[str, dict]:
    """Per-team dropback counts, rate (0–100), and (game_id, play_id) keys."""
    out: dict[str, dict] = {}
    for row in pbp_rows:
        team = to_nflverse_team(row.get("posteam"))
        if not team:
            continue
        bucket = out.setdefault(
            team,
            {"dropbacks": 0, "designed_runs": 0, "dropback_plays": set()},
        )
        game_id = row.get("game_id") or ""
        play_id = "" if row.get("play_id") in (None, "") else str(row.get("play_id"))
        if is_dropback(row):
            bucket["dropbacks"] += 1
            if game_id and play_id:
                bucket["dropback_plays"].add((game_id, play_id))
        elif is_designed_run(row):
            bucket["designed_runs"] += 1
    for bucket in out.values():
        total = bucket["dropbacks"] + bucket["designed_runs"]
        if total:
            bucket["dropback_rate"] = round(100.0 * bucket["dropbacks"] / total, 1)
        else:
            bucket["dropback_rate"] = None
    return out


def route_on_dropbacks(
    participation_rows: list[dict],
    dropback_plays_by_team: dict[str, set[tuple[str, str]]],
) -> dict[str, dict]:
    """GSIS → {on_dropbacks, team} for players on the field on dropbacks."""
    play_team = {
        key: team
        for team, keys in dropback_plays_by_team.items()
        for key in keys
    }
    counts: dict[str, dict] = {}
    for row in participation_rows:
        game_id = row.get("nflverse_game_id") or ""
        play_id = "" if row.get("play_id") in (None, "") else str(row.get("play_id"))
        team = play_team.get((game_id, play_id))
        if not team:
            continue
        for gsis in parse_gsis_ids(row.get("offense_players")):
            rec = counts.setdefault(gsis, {"on_dropbacks": 0, "team": team})
            rec["on_dropbacks"] += 1
            rec["team"] = team
    return counts


def _headline_pos(pos: str) -> str:
    up = (pos or "").upper()
    return HEADLINE_POS.get(up, up)


def position_notes(players: list[dict]) -> list[str]:
    """Factual concentration notes. No start/sit verbs."""
    notes: list[str] = []
    by_pos: dict[str, list[dict]] = defaultdict(list)
    for player in players:
        pos = _headline_pos(player.get("position") or "")
        if pos in POS_LABEL:
            by_pos[pos].append(player)
    for pos in ("RB", "WR", "TE"):
        group = by_pos.get(pos) or []
        label = POS_LABEL[pos]
        for attr, metric in (("snap_share", "snap share"), ("route_proxy", "route proxy")):
            n = sum(
                1
                for p in group
                if isinstance(p.get(attr), (int, float)) and p[attr] >= HEADLINE_THRESHOLD
            )
            if n >= 2:
                notes.append(f"{n} {label} over 90% {metric}")
    return notes


QB_SPLIT_THRESHOLD = 15.0


def _fmt_pct(value: float) -> str:
    return f"{int(round(value))}%"


def qb_notes(players: list[dict]) -> list[str]:
    """Starter vs backup split. A single 100% QB is not news."""
    qbs = [p for p in players if (p.get("position") or "").upper() == "QB"]
    notes: list[str] = []
    snap_split = [
        p for p in qbs
        if isinstance(p.get("snap_share"), (int, float)) and p["snap_share"] >= QB_SPLIT_THRESHOLD
    ]
    if len(snap_split) >= 2:
        snap_split.sort(key=lambda p: (-p["snap_share"], p["name"]))
        parts = ", ".join(f"{p['name']} {_fmt_pct(p['snap_share'])}" for p in snap_split)
        notes.append(f"QB snap split: {parts}")
    db_split = [
        p for p in qbs
        if isinstance(p.get("dropback_share"), (int, float)) and p["dropback_share"] >= QB_SPLIT_THRESHOLD
    ]
    if len(db_split) >= 2:
        db_split.sort(key=lambda p: (-p["dropback_share"], p["name"]))
        parts = ", ".join(f"{p['name']} {_fmt_pct(p['dropback_share'])}" for p in db_split)
        notes.append(f"QB dropback split: {parts}")
    return notes


def headlines_for_team(team: str, players: list[dict]) -> list[str]:
    notes = position_notes(players) + qb_notes(players)
    return [f"{team}: {note}" for note in notes]


def _pbp_id(row: dict, *keys: str) -> str:
    for key in keys:
        val = (row.get(key) or "").strip()
        if val:
            return val
    return ""


def dropback_qb_id(row: dict) -> str:
    if not is_dropback(row):
        return ""
    if is_truthy(row.get("qb_scramble")):
        return _pbp_id(row, "rusher_player_id", "rusher_id")
    return _pbp_id(row, "passer_player_id", "passer_id")


def qb_play_stats(pbp_rows: list[dict]) -> dict[str, dict]:
    """Per-GSIS dropbacks, designed rushes, and scrambles from pbp.

    Dropbacks attach to passer_player_id, except scrambles which attach to
    rusher_player_id. Designed rushes are run plays that are not dropbacks.
    """
    out: dict[str, dict] = {}

    def bucket(gsis: str, team: str) -> dict:
        rec = out.setdefault(
            gsis,
            {"dropbacks": 0, "designed_rushes": 0, "scramble_rushes": 0, "team": team},
        )
        rec["team"] = team
        return rec

    for row in pbp_rows:
        team = to_nflverse_team(row.get("posteam"))
        qid = dropback_qb_id(row)
        if qid:
            rec = bucket(qid, team)
            rec["dropbacks"] += 1
            if is_truthy(row.get("qb_scramble")):
                rec["scramble_rushes"] += 1
        elif is_designed_run(row):
            rid = _pbp_id(row, "rusher_player_id", "rusher_id")
            if rid:
                bucket(rid, team)["designed_rushes"] += 1
    return out


def apply_qb_usage(
    player: dict,
    gsis: str,
    qb_stats: dict[str, dict],
    team_dropbacks: int,
    *,
    has_pbp: bool,
) -> None:
    if (player.get("position") or "").upper() != "QB":
        return
    if not has_pbp:
        for field in ("qb_dropbacks", "dropback_share", "designed_rushes", "scramble_rushes"):
            player[field] = None
            player[f"{field}_provenance"] = MISSING_NOT_RECORDED
        return
    rec = qb_stats.get(gsis) or {}
    db = int(rec.get("dropbacks") or 0)
    player["qb_dropbacks"] = db
    player["qb_dropbacks_provenance"] = MEASURED
    if team_dropbacks:
        player["dropback_share"] = round(100.0 * db / team_dropbacks, 1)
        player["dropback_share_provenance"] = MEASURED
    else:
        player["dropback_share"] = None
        player["dropback_share_provenance"] = MISSING_NOT_RECORDED
    player["designed_rushes"] = int(rec.get("designed_rushes") or 0)
    player["designed_rushes_provenance"] = MEASURED
    player["scramble_rushes"] = int(rec.get("scramble_rushes") or 0)
    player["scramble_rushes_provenance"] = MEASURED


def _filter_week_team(
    rows: list[dict],
    week: int,
    team: str | None,
    team_col: str,
) -> list[dict]:
    out = []
    for row in rows:
        if not week_matches(row.get("week"), week):
            continue
        if team and to_nflverse_team(row.get(team_col)) != team:
            continue
        out.append(row)
    return out


def _measured(value: Any) -> tuple[Any, str]:
    if value is None:
        return None, MISSING_NOT_RECORDED
    return value, MEASURED


def _empty_error(season: str, week: int, team: str | None) -> dict:
    out: dict[str, Any] = {
        "error": "no usage data",
        "season": season,
        "week": week,
        "hint": NO_DATA_HINT,
    }
    if team:
        out["team"] = team
    return out


def roster_identity_maps(roster_rows: list[dict] | None) -> dict[str, dict]:
    """PFR / alias maps so snap 'Zonovan Knight' joins stats 'Bam Knight'."""
    pfr_to_gsis: dict[str, str] = {}
    name_team_candidates: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in roster_rows or []:
        gsis = (row.get("gsis_id") or "").strip()
        if not gsis:
            continue
        team = to_nflverse_team(row.get("team"))
        pfr = (row.get("pfr_id") or row.get("pfr_player_id") or "").strip()
        if pfr:
            pfr_to_gsis[pfr] = gsis
        names = [
            row.get("full_name"),
            " ".join(part for part in (row.get("first_name"), row.get("last_name")) if part),
            " ".join(part for part in (row.get("football_name"), row.get("last_name")) if part),
        ]
        for n in names:
            key = name_key(n or "")
            if key and team:
                name_team_candidates[(key, team)].add(gsis)
    return {
        "pfr_to_gsis": pfr_to_gsis,
        "name_team_to_gsis": {
            k: next(iter(ids))
            for k, ids in name_team_candidates.items()
            if len(ids) == 1
        },
    }


def resolve_snap_gsis(snap: dict, maps: dict[str, dict]) -> str:
    pfr = (snap.get("pfr_player_id") or "").strip()
    if pfr and pfr in maps["pfr_to_gsis"]:
        return maps["pfr_to_gsis"][pfr]
    key = (name_key(snap.get("player") or ""), to_nflverse_team(snap.get("team")))
    return maps["name_team_to_gsis"].get(key) or ""


def _attach_snap(snap: dict | None) -> tuple[float | None, int | None, str]:
    if not snap:
        return None, None, MISSING_NOT_RECORDED
    snap_share = as_pct(snap.get("offense_pct"))
    offense_snaps = as_int(snap.get("offense_snaps"))
    prov = MEASURED if snap_share is not None else MISSING_NOT_RECORDED
    return snap_share, offense_snaps, prov


def assemble_week_usage(
    *,
    season: str,
    week: int,
    team: str | None,
    stats_rows: list[dict],
    snap_rows: list[dict],
    pbp_rows: list[dict],
    participation_rows: list[dict],
    roster_rows: list[dict] | None = None,
    min_snap_share: float = 10.0,
) -> dict:
    """Pure assembly from already-loaded rows. No HTTP."""
    team_up = to_nflverse_team(team) if team else None
    week = int(week)
    stats_rows = _filter_week_team(stats_rows, week, team_up, "team")
    snap_rows = _filter_week_team(snap_rows, week, team_up, "team")
    pbp_rows = _filter_week_team(pbp_rows, week, team_up, "posteam")
    id_maps = roster_identity_maps(roster_rows)

    drop_stats = team_dropback_stats(pbp_rows)
    drop_plays = {t: s["dropback_plays"] for t, s in drop_stats.items()}
    route_counts = route_on_dropbacks(participation_rows, drop_plays)
    qb_stats = qb_play_stats(pbp_rows)

    joined_plays: dict[str, set[tuple[str, str]]] = defaultdict(set)
    play_team = {key: t for t, keys in drop_plays.items() for key in keys}
    for row in participation_rows:
        key = (
            row.get("nflverse_game_id") or "",
            "" if row.get("play_id") in (None, "") else str(row.get("play_id")),
        )
        t = play_team.get(key)
        if t:
            joined_plays[t].add(key)

    snaps_by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    snaps_by_gsis: dict[str, list[dict]] = defaultdict(list)
    snap_identity: list[tuple[dict, str, tuple[str, str]]] = []
    for row in snap_rows:
        pos = (row.get("position") or "").upper()
        if pos not in SKILL_POSITIONS:
            continue
        key = (name_key(row.get("player") or ""), to_nflverse_team(row.get("team")))
        if not key[0] or not key[1]:
            continue
        snaps_by_key[key].append(row)
        gsis = resolve_snap_gsis(row, id_maps)
        if gsis:
            snaps_by_gsis[gsis].append(row)
        snap_identity.append((row, gsis, key))
    snap_colliding = {k for k, rows in snaps_by_key.items() if len(rows) > 1}
    gsis_snap_colliding = {g for g, rows in snaps_by_gsis.items() if len(rows) > 1}

    stats_skill: list[dict] = []
    stats_name_counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in stats_rows:
        pos = (row.get("position") or "").upper()
        if pos not in SKILL_POSITIONS:
            continue
        key = (
            name_key(row.get("player_display_name") or row.get("player_name") or ""),
            to_nflverse_team(row.get("team")),
        )
        stats_skill.append(row)
        if key[0] and key[1]:
            stats_name_counts[key] += 1
    name_colliding = {k for k, n in stats_name_counts.items() if n > 1}

    players_by_team: dict[str, list[dict]] = defaultdict(list)
    used_snap_keys: set[tuple[str, str]] = set()
    used_gsis: set[str] = set()
    used_snap_ids: set[int] = set()

    for row in stats_skill:
        t = to_nflverse_team(row.get("team"))
        name = row.get("player_display_name") or row.get("player_name") or ""
        pos = (row.get("position") or "").upper()
        gsis = (row.get("player_id") or "").strip()
        key = (name_key(name), t)
        used_snap_keys.add(key)
        if gsis:
            used_gsis.add(gsis)

        snap = None
        if gsis and gsis not in gsis_snap_colliding and len(snaps_by_gsis.get(gsis) or []) == 1:
            snap = snaps_by_gsis[gsis][0]
        elif key[0] and key not in name_colliding and key not in snap_colliding:
            snap_list = snaps_by_key.get(key) or []
            if len(snap_list) == 1:
                snap = snap_list[0]
        if snap is not None:
            used_snap_ids.add(id(snap))
        snap_share, offense_snaps, snap_prov = _attach_snap(snap)

        rushes, rushes_prov = _measured(as_int(row["carries"]) if "carries" in row else None)
        targets, targets_prov = _measured(as_int(row["targets"]) if "targets" in row else None)
        tshare, tshare_prov = _measured(
            as_pct(row["target_share"]) if "target_share" in row else None
        )

        joined = len(joined_plays.get(t) or ())
        on_db = route_counts.get(gsis, {}).get("on_dropbacks", 0) if gsis else 0
        if joined == 0:
            route_val, route_prov, on_out = None, MISSING_NOT_RECORDED, None
        else:
            route_val = round(100.0 * on_db / joined, 1)
            route_prov, on_out = MEASURED, on_db

        if not _player_has_usage(snap_share, offense_snaps, rushes, targets, on_out):
            continue

        player = {
            "name": name,
            "position": pos,
            "snap_share": snap_share,
            "snap_share_provenance": snap_prov,
            "offense_snaps": offense_snaps,
            "rushes": rushes,
            "rushes_provenance": rushes_prov,
            "targets": targets,
            "targets_provenance": targets_prov,
            "target_share": tshare,
            "target_share_provenance": tshare_prov,
            "route_proxy": route_val,
            "route_proxy_provenance": route_prov,
            "on_dropbacks": on_out,
        }
        apply_qb_usage(
            player, gsis, qb_stats, int((drop_stats.get(t) or {}).get("dropbacks") or 0),
            has_pbp=t in drop_stats,
        )
        players_by_team[t].append(player)

    for snap, gsis, key in snap_identity:
        if id(snap) in used_snap_ids:
            continue
        if gsis and gsis in used_gsis:
            continue
        if key in used_snap_keys or key in snap_colliding:
            continue
        t = key[1]
        snap_share, offense_snaps, snap_prov = _attach_snap(snap)
        if not _player_has_usage(snap_share, offense_snaps, None, None, None):
            continue
        on_db = route_counts.get(gsis, {}).get("on_dropbacks") if gsis else None
        joined = len(joined_plays.get(t) or ())
        if gsis and joined:
            route_val = round(100.0 * (on_db or 0) / joined, 1)
            route_prov, on_out = MEASURED, on_db or 0
        else:
            route_val, route_prov, on_out = None, MISSING_NOT_RECORDED, None
        player = {
            "name": snap.get("player") or "",
            "position": (snap.get("position") or "").upper(),
            "snap_share": snap_share,
            "snap_share_provenance": snap_prov,
            "offense_snaps": offense_snaps,
            "rushes": None,
            "rushes_provenance": MISSING_NOT_RECORDED,
            "targets": None,
            "targets_provenance": MISSING_NOT_RECORDED,
            "target_share": None,
            "target_share_provenance": MISSING_NOT_RECORDED,
            "route_proxy": route_val,
            "route_proxy_provenance": route_prov,
            "on_dropbacks": on_out,
        }
        apply_qb_usage(
            player, gsis, qb_stats, int((drop_stats.get(t) or {}).get("dropbacks") or 0),
            has_pbp=t in drop_stats,
        )
        players_by_team[t].append(player)

    headlines: list[str] = []
    teams_out: list[dict] = []
    for t in sorted(set(players_by_team) | set(drop_stats)):
        roster = players_by_team.get(t) or []
        notes = position_notes(roster) + qb_notes(roster)
        headlines.extend(f"{t}: {n}" for n in notes)
        visible = [
            p for p in roster
            if p["snap_share"] is None or p["snap_share"] >= float(min_snap_share)
        ]
        visible.sort(
            key=lambda p: (
                _pos_sort_key(p["position"]),
                -(p["snap_share"] if p["snap_share"] is not None else -1),
                p["name"],
            )
        )
        if not visible:
            continue
        ds = drop_stats.get(t) or {}
        dropbacks = int(ds.get("dropbacks") or 0)
        designed = int(ds.get("designed_runs") or 0)
        rate = ds.get("dropback_rate")
        joined_n = len(joined_plays.get(t) or ())
        teams_out.append({
            "team": t,
            "dropback_rate": rate,
            "dropback_rate_provenance": MEASURED if rate is not None else MISSING_NOT_RECORDED,
            "dropbacks": dropbacks,
            "designed_runs": designed,
            "dropbacks_with_participation": joined_n,
            "notes": notes,
            "players": visible,
        })

    if not teams_out:
        return _empty_error(str(season), week, team_up)

    return {
        "season": str(season),
        "week": week,
        "source": NFLVERSE_SOURCE,
        "units": UNITS,
        "limitations": [ROUTE_LIMITATION, QB_LIMITATION],
        "headlines": headlines,
        "teams": teams_out,
    }


def _player_has_usage(
    snap_share: float | None,
    offense_snaps: int | None,
    rushes: int | None,
    targets: int | None,
    on_dropbacks: int | None,
) -> bool:
    if snap_share and snap_share > 0:
        return True
    if offense_snaps and offense_snaps > 0:
        return True
    if rushes and rushes > 0:
        return True
    if targets and targets > 0:
        return True
    if on_dropbacks and on_dropbacks > 0:
        return True
    return False


def week_usage(
    week: int | None = None,
    season: str | None = None,
    team: str | None = None,
    min_snap_share: float | None = None,
) -> dict:
    """Load nflverse CSVs for one week and assemble the usage report."""
    season = str(season or current_season())
    if week is None:
        state = get_json(f"/state/{SPORT}", cache=True) or {}
        try:
            week = int(state.get("week") or 1)
        except (TypeError, ValueError):
            week = 1
    week = int(week)
    team_up = to_nflverse_team(team) if team else None
    if min_snap_share is None:
        min_snap_share = 0.0 if team_up else 10.0

    def keep_player_week(row: dict) -> bool:
        if not week_matches(row.get("week"), week):
            return False
        if team_up and to_nflverse_team(row.get("team")) != team_up:
            return False
        return True

    stats_rows = nflverse_csv(
        "stats_player", f"stats_player_week_{season}.csv",
        row_filter=keep_player_week, ttl=STATS_CACHE_TTL,
    )
    snap_rows = nflverse_csv(
        "snap_counts", f"snap_counts_{season}.csv",
        row_filter=keep_player_week, ttl=STATS_CACHE_TTL,
    )

    def keep_pbp(row: dict) -> bool:
        if not week_matches(row.get("week"), week):
            return False
        if (row.get("play_type") or "") not in ("pass", "run"):
            return False
        if team_up and to_nflverse_team(row.get("posteam")) != team_up:
            return False
        return True

    pbp_rows = nflverse_csv(
        "pbp", f"play_by_play_{season}.csv",
        row_filter=keep_pbp, ttl=STATS_CACHE_TTL,
    )
    game_ids = {r.get("game_id") for r in pbp_rows if r.get("game_id")}

    def keep_participation(row: dict) -> bool:
        if (row.get("nflverse_game_id") or "") not in game_ids:
            return False
        if team_up:
            poss = to_nflverse_team(row.get("possession_team"))
            if poss and poss != team_up:
                return False
        return True

    participation_rows = nflverse_csv(
        "pbp_participation", f"pbp_participation_{season}.csv",
        row_filter=keep_participation, ttl=STATS_CACHE_TTL,
    )

    def keep_roster(row: dict) -> bool:
        pos = (row.get("position") or "").upper()
        if pos not in SKILL_POSITIONS:
            return False
        if team_up and to_nflverse_team(row.get("team")) != team_up:
            return False
        return True

    roster_rows = nflverse_csv(
        "rosters", f"roster_{season}.csv",
        row_filter=keep_roster, ttl=STATS_CACHE_TTL,
    )

    return assemble_week_usage(
        season=season,
        week=week,
        team=team_up,
        stats_rows=stats_rows,
        snap_rows=snap_rows,
        pbp_rows=pbp_rows,
        participation_rows=participation_rows,
        roster_rows=roster_rows,
        min_snap_share=min_snap_share,
    )

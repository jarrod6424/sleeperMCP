"""
Start/sit advice: legal lineup on projections, with a reason per swap.

UNOFFICIAL. Projections still come from Sleeper's undocumented endpoint.
Reason codes are heuristics on top of that lineup — they explain a swap,
they are not a second optimizer.

Codes
-----
higher_projection          Optimal player projects more points than the sit.
injury_risk                Sitting player is dinged, or the start is cleaner.
favorable_matchup          nflverse schedule: player's team is a spread favorite.
negative_game_script_risk  RB (or QB) on a sizable underdog — optional heuristic.
higher_floor               strategy=floor preferred the safer of a close call.
superflex_qb_slot          A QB is occupying SUPER_FLEX (eligibility reminder).

strategy: balanced (default) | floor | ceiling. Floor blends a lightweight
availability/recent-PPR floor into the greedy sort for close calls. It does
not call custom_score_player per roster player (that would fan out). Ceiling
sorts on raw projection. Matchup/script codes are omitted when the schedule
file has no spread — we do not invent defense ranks.
"""

from __future__ import annotations

from typing import Any

from . import advice
from .config import NFLVERSE_SOURCE, STATS_CACHE_TTL
from .http import get_json, nflverse_csv
from .league import resolve_league_id, resolve_my_roster, resolve_roster
from .players import load_players, player_name
from .projections import SKIP_SLOTS, optimal_lineup, proj_points, scoring_field, projections_for
from .stats import to_nflverse_team
from .values import league_format

STRATEGIES = ("balanced", "floor", "ceiling")

REASON_HIGHER_PROJECTION = "higher_projection"
REASON_INJURY_RISK = "injury_risk"
REASON_FAVORABLE_MATCHUP = "favorable_matchup"
REASON_NEGATIVE_SCRIPT = "negative_game_script_risk"
REASON_HIGHER_FLOOR = "higher_floor"
REASON_SUPERFLEX_QB = "superflex_qb_slot"

INJURED_OUT = {"OUT", "IR", "PUP", "SUS", "COV"}
INJURED_RISK = INJURED_OUT | {"DOUBTFUL", "QUESTIONABLE"}
CLOSE_CALL_PTS = 2.5
FAVORITE_THRESHOLD = -3.0  # home spread: more negative = home more favored
UNDERDOG_SCRIPT = 6.5


def injury_rank(injury_status: str | None) -> int:
    """Higher = more likely to miss. Used so injury_risk means the sit is worse."""
    inj = (injury_status or "").upper()
    if inj in INJURED_OUT:
        return 3
    if inj == "DOUBTFUL":
        return 2
    if inj == "QUESTIONABLE":
        return 1
    return 0


def normalize_strategy(strategy: str | None) -> str:
    s = (strategy or "balanced").strip().lower()
    return s if s in STRATEGIES else "balanced"


def injury_haircut(injury_status: str | None, proj: float) -> float:
    """Cheap floor proxy when we do not have a weekly-PPR sample."""
    inj = (injury_status or "").upper()
    if inj in INJURED_OUT:
        return 0.0
    if inj == "DOUBTFUL":
        return round(proj * 0.35, 2)
    if inj == "QUESTIONABLE":
        return round(proj * 0.75, 2)
    return float(proj)


def lineup_score(proj: float, floor: float, strategy: str) -> float:
    if strategy == "floor":
        return round(0.35 * proj + 0.65 * floor, 4)
    if strategy == "ceiling":
        return round(float(proj), 4)
    return round(0.80 * proj + 0.20 * floor, 4)


def week_matchups(season: str, week: int) -> dict[str, dict[str, Any]]:
    """nflverse schedule for one week, keyed by team abbreviation (nflverse form).

    {} on any failure — matchup codes are optional.
    """
    season_s = str(season)
    week_s = str(week)

    def keep(row: dict) -> bool:
        return str(row.get("season") or "") == season_s and str(row.get("week") or "") == week_s

    try:
        rows = nflverse_csv(
            "schedules",
            "games.csv",
            row_filter=keep,
            ttl=STATS_CACHE_TTL,
        )
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if (row.get("game_type") or "REG") not in {"REG", ""}:
            continue
        home = (row.get("home_team") or "").upper()
        away = (row.get("away_team") or "").upper()
        if not home or not away:
            continue
        spread = None
        raw_spread = row.get("spread_line")
        try:
            if raw_spread not in (None, "", "NA", "NULL"):
                spread = float(raw_spread)
        except (TypeError, ValueError):
            spread = None
        out[home] = {
            "opponent": away,
            "home": True,
            "spread_line": spread,
            "favorite": (spread is not None and spread <= FAVORITE_THRESHOLD),
            "underdog_by": (spread if spread is not None and spread > 0 else None),
        }
        # Away spread is the opposite of the home line.
        away_spread = None if spread is None else -spread
        out[away] = {
            "opponent": home,
            "home": False,
            "spread_line": away_spread,
            "favorite": (away_spread is not None and away_spread <= FAVORITE_THRESHOLD),
            "underdog_by": (away_spread if away_spread is not None and away_spread > 0 else None),
        }
    return out


def recent_floor_map(names: list[str], season: str) -> dict[str, float]:
    """Share of games at or above 10 PPR, 0–1, keyed by lowercased full name.

    Best-effort. Empty if nflverse is down or the season has no rows yet.
    """
    needles = {n.strip().lower() for n in names if n}
    if not needles:
        return {}

    def keep(row: dict) -> bool:
        display = (row.get("player_display_name") or row.get("player_name") or "").lower()
        return display in needles

    try:
        rows = nflverse_csv(
            "stats_player",
            f"stats_player_week_{season}.csv",
            row_filter=keep,
            ttl=STATS_CACHE_TTL,
        )
    except Exception:
        return {}
    grouped: dict[str, list[float]] = {}
    for row in rows or []:
        display = (row.get("player_display_name") or row.get("player_name") or "").strip().lower()
        if not display:
            continue
        try:
            pts = float(row.get("fantasy_points_ppr") or 0)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(display, []).append(pts)
    out = {}
    for name, pts in grouped.items():
        if not pts:
            continue
        out[name] = sum(1 for p in pts if p >= 10.0) / len(pts)
    return out


def attach_floors(pool: list[dict], season: str, strategy: str) -> None:
    """Set floor_proj and lineup_score on each pool player (in place)."""
    floors: dict[str, float] = {}
    if strategy in {"floor", "balanced"}:
        floors = recent_floor_map([p.get("name") or "" for p in pool], season)
    for p in pool:
        inj = p.get("injury_status")
        haircut = injury_haircut(inj, float(p.get("proj") or 0))
        sample = floors.get((p.get("name") or "").strip().lower())
        if sample is None:
            p["floor_proj"] = haircut
        else:
            # 10-PPR hit rate scaled into the same units as a typical weekly proj.
            sample_pts = sample * max(float(p.get("proj") or 0), 10.0)
            p["floor_proj"] = round(0.5 * haircut + 0.5 * sample_pts, 2)
        p["lineup_score"] = lineup_score(float(p.get("proj") or 0), p["floor_proj"], strategy)


def _matchup_for(team: str | None, matchups: dict[str, dict]) -> dict | None:
    if not team or not matchups:
        return None
    return matchups.get(to_nflverse_team(team)) or matchups.get((team or "").upper())


def _codes_and_strings(
    *,
    start: dict,
    sit: dict | None,
    matchups: dict[str, dict],
    strategy: str,
) -> tuple[list[str], list[str]]:
    codes: list[str] = []
    strings: list[str] = []
    start_proj = float(start.get("proj") or 0)
    if sit is not None:
        sit_proj = float(sit.get("proj") or 0)
        if start_proj >= sit_proj:
            codes.append(REASON_HIGHER_PROJECTION)
            strings.append(
                f"higher_projection: {start.get('name')} {start_proj:.1f} vs "
                f"{sit.get('name')} {sit_proj:.1f}"
            )
        sit_inj = (sit.get("injury_status") or "").upper()
        start_inj = (start.get("injury_status") or "").upper()
        if injury_rank(sit_inj) > injury_rank(start_inj):
            codes.append(REASON_INJURY_RISK)
            strings.append(f"injury_risk: sitting {sit.get('name')} ({sit_inj or 'flagged'})")
        if abs(start_proj - sit_proj) <= CLOSE_CALL_PTS and strategy == "floor":
            if float(start.get("floor_proj") or 0) >= float(sit.get("floor_proj") or 0):
                codes.append(REASON_HIGHER_FLOOR)
                strings.append(
                    f"higher_floor: close call ({abs(start_proj - sit_proj):.1f} pts); "
                    f"prefer {start.get('name')} floor {start.get('floor_proj')}"
                )
    else:
        codes.append(REASON_HIGHER_PROJECTION)
        strings.append(f"higher_projection: {start.get('name')} {start_proj:.1f} belongs in the optimal lineup")

    if start.get("slot") == "SUPER_FLEX" and start.get("position") == "QB":
        codes.append(REASON_SUPERFLEX_QB)
        strings.append("superflex_qb_slot: second QB is occupying SUPER_FLEX")

    mu = _matchup_for(start.get("team"), matchups)
    if mu:
        loc = "home" if mu.get("home") else "away"
        strings.append(f"plays {mu.get('opponent')} ({loc})")
        if mu.get("favorite"):
            codes.append(REASON_FAVORABLE_MATCHUP)
            strings.append(
                f"favorable_matchup: {start.get('team')} favored vs {mu.get('opponent')} "
                "(spread heuristic, not a D/ST ranking)"
            )
        under = mu.get("underdog_by")
        if (
            under is not None
            and under >= UNDERDOG_SCRIPT
            and (start.get("position") in {"RB", "QB"})
        ):
            codes.append(REASON_NEGATIVE_SCRIPT)
            strings.append(
                f"negative_game_script_risk: {start.get('position')} on a "
                f"{under:.1f}-point underdog (heuristic)"
            )
    if not strings:
        strings.append(f"higher_projection: {start.get('name')} is in the optimal lineup")
        if REASON_HIGHER_PROJECTION not in codes:
            codes.append(REASON_HIGHER_PROJECTION)
    return codes, strings


def annotate_swaps(
    start: list[dict],
    sit: list[dict],
    matchups: dict[str, dict] | None,
    strategy: str,
) -> None:
    """Attach reasons[] and reason_codes[] to every start/sit suggestion."""
    matchups = matchups or {}
    used_sit: set[str] = set()

    def take_sit(starter: dict) -> dict | None:
        for cand in sit:
            if cand["player_id"] in used_sit:
                continue
            if cand.get("position") == starter.get("position"):
                used_sit.add(cand["player_id"])
                return cand
        for cand in sit:
            if cand["player_id"] not in used_sit:
                used_sit.add(cand["player_id"])
                return cand
        return None

    for s in start:
        partner = take_sit(s)
        codes, strings = _codes_and_strings(
            start=s, sit=partner, matchups=matchups, strategy=strategy
        )
        s["reason_codes"] = codes
        s["reasons"] = strings
        if partner:
            s["swap_for"] = {
                "name": partner.get("name"),
                "player_id": partner.get("player_id"),
                "proj": partner.get("proj"),
            }

    for b in sit:
        partner = next(
            (s for s in start if (s.get("swap_for") or {}).get("player_id") == b["player_id"]),
            None,
        )
        sit_proj = float(b.get("proj") or 0)
        if partner:
            start_proj = float(partner.get("proj") or 0)
            b["reason_codes"] = [REASON_HIGHER_PROJECTION]
            b["reasons"] = [
                f"sit behind {partner.get('name')} ({start_proj:.1f} vs {sit_proj:.1f})"
            ]
        else:
            b["reason_codes"] = [REASON_HIGHER_PROJECTION]
            b["reasons"] = [
                f"lower projected points ({sit_proj:.1f}) than the optimal lineup"
            ]
        inj = (b.get("injury_status") or "").upper()
        if inj in INJURED_RISK:
            b["reason_codes"].append(REASON_INJURY_RISK)
            b["reasons"].append(f"injury_risk: {b.get('name')} is {inj}")


def projection_failure(
    *,
    league_id: str | None,
    platform: str,
    team_name: str | None,
    week: int | None,
    season: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured error when the undocumented projections endpoint is empty."""
    payload = {
        "error": "no projection data returned",
        "league_id": league_id,
        "platform": platform,
        "team": team_name,
        "week": week,
        "season": season,
        "hint": (
            "The undocumented projections endpoint may have changed or be unavailable."
        ),
        "source": "api.sleeper.com projections (UNDOCUMENTED, unsupported)",
        "verdict": "Could not build start/sit: weekly projections unavailable.",
        "fallback": {
            "guidance": (
                "Use prior-week player points from get_player_stats, last week's "
                "box score, or set the lineup manually. This tool does not guess "
                "a lineup without projections."
            ),
            "try": ["get_player_stats", "get_snap_counts", "get_injuries"],
        },
        "reasons": ["Weekly Sleeper projections returned no rows."],
        "data_sources": ["sleeper"],
        "limitations": [
            "Start/sit is blocked without a projection feed; we do not invent points.",
        ],
        "unofficial": True,
    }
    if extra:
        payload.update(extra)
    return payload


def build_from_pool(
    *,
    league_id: str,
    platform: str,
    fmt: dict | None,
    season: str,
    week: int,
    subject: dict,
    scoring_label: str,
    slots: list[str],
    pool: list[dict],
    current_starter_ids: list[str],
    strategy: str,
    source: str,
    extra: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Shared Sleeper/Yahoo lineup + reasons once a scored pool exists."""
    strategy_n = normalize_strategy(strategy)
    attach_floors(pool, season, strategy_n)
    matchups = week_matchups(season, week)

    pool_by_id = {p["player_id"]: p for p in pool}
    optimal = optimal_lineup(slots, pool, score_key="lineup_score")
    optimal_ids = {p["player_id"] for p in optimal}
    current_ids = {str(p) for p in current_starter_ids}

    current_proj = round(
        sum(pool_by_id.get(str(pid), {}).get("proj", 0.0) for pid in current_starter_ids),
        2,
    )
    optimal_proj = round(sum(p["proj"] for p in optimal), 2)

    sit = [
        dict(pool_by_id[str(pid)])
        for pid in current_starter_ids
        if str(pid) not in optimal_ids and str(pid) in pool_by_id
    ]
    start = [dict(p) for p in optimal if p["player_id"] not in current_ids]
    sit.sort(key=lambda p: p.get("proj") or 0, reverse=True)
    start.sort(key=lambda p: p.get("proj") or 0, reverse=True)
    annotate_swaps(start, sit, matchups, strategy_n)

    if not start and not sit:
        verdict = "Current lineup already matches the optimal projection set. No swap."
    else:
        names = ", ".join(p.get("name") or "?" for p in start[:3]) or "bench"
        gain = round(optimal_proj - current_proj, 2)
        verdict = f"Start {names} for about {gain} more projected points ({strategy_n})."

    reasons = [
        f"Greedy legal lineup on {scoring_label} projections, strategy={strategy_n}.",
        f"Current {current_proj} vs optimal {optimal_proj} (gain {round(optimal_proj - current_proj, 2)}).",
    ]
    if any(REASON_FAVORABLE_MATCHUP in (p.get("reason_codes") or []) for p in start):
        reasons.append("At least one swap cites a spread-based favorable_matchup heuristic.")
    if not matchups:
        reasons.append("No nflverse schedule row for this week — matchup codes omitted.")

    limitations = [
        "Projections come from an undocumented Sleeper endpoint and are estimates.",
        "Lineup is a greedy heuristic, not a guaranteed optimum.",
        "favorable_matchup / negative_game_script_risk use nflverse spreads when present, not opponent D/ST ranks.",
        "Floor strategy uses injury haircuts plus optional prior-season 10-PPR hit rate, not a full custom_score_player call.",
    ]
    data_sources = ["sleeper"]
    if matchups:
        data_sources.append("nflverse")

    envelope = advice.advice_envelope(
        league_id=league_id,
        platform=platform,
        fmt=fmt,
        season=season,
        week=week,
        subject=subject,
        verdict=verdict,
        reasons=reasons,
        data_sources=data_sources,
        limitations=limitations,
        recommendations=start,
        extra={
            "team": (subject or {}).get("team_name"),
            "week": week,
            "scoring_format": scoring_label,
            "strategy": strategy_n,
            "source": source,
            "current_projected": current_proj,
            "optimal_projected": optimal_proj,
            "potential_point_gain": round(optimal_proj - current_proj, 2),
            "consider_starting": start,
            "consider_benching": sit,
            "optimal_lineup": [
                {
                    "slot": p.get("slot"),
                    "name": p.get("name"),
                    "position": p.get("position"),
                    "proj": p.get("proj"),
                    "player_id": p.get("player_id"),
                }
                for p in optimal
            ],
            "note": note
            or (
                "Projections come from an undocumented Sleeper endpoint and are "
                "estimates. Lineup is a greedy heuristic, not a guaranteed optimum."
            ),
        },
    )
    if extra:
        envelope.update(extra)
    return envelope


def start_sit_advice(
    league_id: str | None = None,
    *,
    week: int | None = None,
    team_name_or_manager: str | None = None,
    strategy: str = "balanced",
) -> dict[str, Any]:
    from .config import SPORT

    lid = resolve_league_id(league_id)
    resolved = (
        resolve_roster(lid, team_name_or_manager)
        if team_name_or_manager
        else resolve_my_roster(lid)
    )
    if not resolved:
        return {"error": "could not resolve team", "query": team_name_or_manager}

    roster = resolved["roster"]
    owner = resolved["owner"]
    state = get_json(f"/state/{SPORT}", cache=True) or {}
    week = int(week or state.get("week") or 1)
    season = str(state.get("season") or state.get("league_season") or "")
    if not season:
        return {"error": "could not determine current season from NFL state"}

    field, scoring_label = scoring_field(lid)
    proj = projections_for(season, week)
    if not proj:
        return projection_failure(
            league_id=lid,
            platform="sleeper",
            team_name=owner.get("team_name"),
            week=week,
            season=season,
        )

    players = load_players()
    league = get_json(f"/league/{lid}", cache=True) or {}
    fmt = league_format(lid)
    starters = roster.get("starters") or []
    all_ids = roster.get("players") or []

    pool = []
    for pid in all_ids:
        rec = player_name(str(pid), players)
        info = players.get(str(pid)) or {}
        pool.append(
            {
                "player_id": str(pid),
                "name": rec.get("name"),
                "position": rec.get("position"),
                "team": rec.get("team") or info.get("team"),
                "injury_status": rec.get("injury_status") or info.get("injury_status"),
                "proj": proj_points(pid, proj, field),
            }
        )

    slots = [s for s in (league.get("roster_positions") or []) if s not in SKIP_SLOTS]
    return build_from_pool(
        league_id=lid,
        platform="sleeper",
        fmt=fmt,
        season=season,
        week=week,
        subject=advice.subject_block(
            team_name=owner.get("team_name"),
            manager=owner.get("display_name"),
            roster_id=roster.get("roster_id"),
        ),
        scoring_label=scoring_label,
        slots=slots,
        pool=pool,
        current_starter_ids=[str(p) for p in starters if p],
        strategy=strategy,
        source="api.sleeper.com projections (UNDOCUMENTED, unsupported)",
    )

"""
Yahoo league format → FantasyCalc trade values, joined via sleeper_id.
"""

from __future__ import annotations

from typing import Any

from sleeper_core import values as sleeper_values
from sleeper_core.config import FC_SOURCE

from .config import YAHOO_AUCTION_BUDGET, YAHOO_SCORING_FORMAT
from .league import (
    _config_error,
    compute_league,
    compute_my_team,
    resolve_league_key,
    scout_team,
)
from .parse import to_int
from .scoring import ppr_from_reception_points, reception_points
from .start_sit import scoring_field


def _num_qbs(roster_positions: list[str]) -> int:
    positions = [str(p).upper() for p in roster_positions]
    if any(p in {"SUPER_FLEX", "Q/W/R/T", "QP"} for p in positions):
        return 2
    return 2 if positions.count("QB") >= 2 else 1


def league_format(league_key: str | None = None) -> dict[str, Any]:
    """FantasyCalc parameters inferred from Yahoo league settings."""
    league = compute_league(league_key)
    if isinstance(league, dict) and league.get("error"):
        return league

    raw_settings = league.get("raw_settings") or {}
    rec = reception_points(raw_settings if isinstance(raw_settings, dict) else {})
    if rec is None and league.get("reception_points") is not None:
        rec = float(league["reception_points"])
    if rec is None:
        field, _label = scoring_field(YAHOO_SCORING_FORMAT)
        rec = 1.0 if field == "pts_ppr" else (0.5 if field == "pts_half_ppr" else 0.0)
        rec_source = "YAHOO_SCORING_FORMAT"
        ppr, label = ppr_from_reception_points(rec)
    else:
        rec_source = "yahoo_stat_modifiers"
        ppr, label = ppr_from_reception_points(rec)

    roster_positions = league.get("roster_positions") or []
    return {
        "platform": "yahoo",
        "league_key": league.get("league_key"),
        "ppr": ppr if ppr is not None else 0,
        "reception_points": rec,
        "reception_points_source": rec_source,
        "numQbs": _num_qbs(roster_positions),
        "numTeams": league.get("num_teams") or 12,
        "isDynasty": False,  # phase 3 targets redraft; Yahoo dynasty not detected yet
        "scoring_format_label": label or "Standard",
    }


def _value_index(fmt: dict[str, Any]) -> tuple[dict[str, dict], dict[str, dict]]:
    values = sleeper_values.fc_values(fmt)
    by_sid: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for v in values:
        player = v.get("player") or {}
        sid = player.get("sleeperId")
        if sid:
            by_sid[str(sid)] = v
        name = (player.get("name") or "").strip().lower()
        if name:
            by_name[name] = v
    return by_sid, by_name


def value_my_roster(
    team_name_or_manager: str | None = None,
    league_key: str | None = None,
) -> dict[str, Any]:
    lid = resolve_league_key(league_key)
    if not lid:
        return _config_error("YAHOO_LEAGUE_KEY is not configured")

    fmt = league_format(lid)
    if fmt.get("error"):
        return fmt

    if team_name_or_manager:
        report = scout_team(team_name_or_manager, lid)
    else:
        report = compute_my_team(lid)
    if isinstance(report, dict) and report.get("error"):
        return report

    by_sid, _by_name = _value_index(fmt)
    rows: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    total = 0.0
    for row in list(report.get("starters") or []) + list(report.get("bench") or []):
        sid = row.get("sleeper_id")
        if not sid:
            unmatched.append(
                {
                    "name": row.get("name"),
                    "yahoo_player_id": row.get("player_id"),
                    "reason": "no sleeper_id",
                }
            )
            continue
        v = by_sid.get(str(sid))
        val = (v or {}).get("value")
        entry = {
            "name": row.get("name"),
            "position": row.get("position"),
            "sleeper_id": str(sid),
            "yahoo_player_id": row.get("player_id"),
            "selected_position": row.get("selected_position"),
            "value": val,
            "crosswalk_matched_by": row.get("crosswalk_matched_by"),
        }
        if val is not None:
            total += float(val)
        else:
            unmatched.append({**entry, "reason": "no FantasyCalc value"})
        rows.append(entry)

    rows.sort(key=lambda r: (r.get("value") is None, -(r.get("value") or 0)))
    for index, row in enumerate(rows, start=1):
        row["rank_on_roster"] = index

    return {
        "platform": "yahoo",
        "team": report.get("owner"),
        "league_key": lid,
        "format": {
            "ppr": fmt["ppr"],
            "numQbs": fmt["numQbs"],
            "numTeams": fmt["numTeams"],
            "isDynasty": fmt["isDynasty"],
            "scoring_format_label": fmt["scoring_format_label"],
            "reception_points_source": fmt["reception_points_source"],
        },
        "total_value": round(total, 2),
        "players": rows,
        "unmatched_players": unmatched,
        "source": FC_SOURCE,
    }


def analyze_trade(
    give: list[str],
    get: list[str],
    league_key: str | None = None,
) -> dict[str, Any]:
    lid = resolve_league_key(league_key)
    if not lid:
        return _config_error("YAHOO_LEAGUE_KEY is not configured")

    fmt = league_format(lid)
    if fmt.get("error"):
        return fmt

    by_sid, by_name = _value_index(fmt)

    def resolve(item: str) -> tuple[dict | None, list[str]]:
        raw = str(item).strip()
        key = raw.lower()
        v = by_sid.get(raw) or by_name.get(key)
        if v:
            return v, []
        # Yahoo id / key via crosswalk → sleeper id
        try:
            from sleeper_core.crosswalk import yahoo_to_sleeper

            crossed = yahoo_to_sleeper(raw, name=raw)
            sid = crossed.get("sleeper_id")
            if sid and str(sid) in by_sid:
                return by_sid[str(sid)], []
        except Exception:
            pass
        partial = [vv for nm, vv in by_name.items() if key in nm]
        if len(partial) == 1:
            return partial[0], []
        names = [((vv.get("player") or {}).get("name") or nm) for nm, vv in by_name.items() if key in nm]
        return None, names[:8]

    def side(items: list[str]) -> dict[str, Any]:
        resolved_rows = []
        missing = []
        total = 0.0
        for item in items:
            v, suggestions = resolve(item)
            if not v:
                missing.append({"query": item, "suggestions": suggestions})
                continue
            player = v.get("player") or {}
            val = v.get("value") or 0
            total += float(val)
            resolved_rows.append(
                {
                    "name": player.get("name"),
                    "sleeper_id": player.get("sleeperId"),
                    "position": player.get("position"),
                    "value": val,
                }
            )
        return {
            "players": resolved_rows,
            "total": round(total, 2),
            "missing": missing,
        }

    give_side = side(give)
    get_side = side(get)
    delta = round(get_side["total"] - give_side["total"], 2)
    if delta > 5:
        verdict = "favor_get"
    elif delta < -5:
        verdict = "favor_give"
    else:
        verdict = "roughly_even"

    return {
        "platform": "yahoo",
        "league_key": lid,
        "format": {
            "ppr": fmt["ppr"],
            "numQbs": fmt["numQbs"],
            "numTeams": fmt["numTeams"],
            "isDynasty": fmt["isDynasty"],
            "scoring_format_label": fmt["scoring_format_label"],
            "reception_points_source": fmt["reception_points_source"],
        },
        "give": give_side,
        "get": get_side,
        "delta_get_minus_give": delta,
        "verdict": verdict,
        "source": FC_SOURCE,
    }


def trade_values(
    league_key: str | None = None,
    *,
    limit: int = 50,
    position: str | None = None,
) -> dict[str, Any]:
    """FantasyCalc board for a Yahoo league's inferred format."""
    fmt = league_format(league_key)
    if fmt.get("error"):
        return fmt
    raw = sleeper_values.fc_values(fmt)
    if not raw:
        return {"error": "no trade values returned", "format": fmt, "source": FC_SOURCE}
    rows = [sleeper_values.fc_row(v) for v in raw]
    if position:
        pos = position.upper()
        rows = [r for r in rows if r.get("position") == pos]
    rows.sort(key=lambda r: r.get("value") or 0, reverse=True)
    return {
        "platform": "yahoo",
        "format": fmt,
        "source": FC_SOURCE,
        "players": rows[:limit],
    }


def auction_budgets(
    league_key: str | None = None,
    *,
    limit: int = 80,
    ceiling_pct: float = 0.12,
    position: str | None = None,
    budget: int | None = None,
) -> dict[str, Any]:
    """Auction $ targets for a Yahoo league using FantasyCalc values."""
    from sleeper_core import auction as sleeper_auction

    league = compute_league(league_key)
    if isinstance(league, dict) and league.get("error"):
        return league

    fmt = league_format(league.get("league_key"))
    if fmt.get("error"):
        return fmt

    settings = league.get("settings") or {}
    is_auction = str(settings.get("is_auction_draft") or "").strip() in {"1", "true", "True"}
    resolved_budget = budget if budget is not None else YAHOO_AUCTION_BUDGET
    if resolved_budget is None:
        resolved_budget = 200

    roster_positions = league.get("roster_positions") or []
    roster_spots = len(roster_positions) or 15
    k_def_spots = sum(1 for p in roster_positions if str(p).upper() in {"K", "DEF"})
    num_teams = int(league.get("num_teams") or fmt.get("numTeams") or 12)

    raw = sleeper_values.fc_values(fmt)
    if not raw:
        return {"error": "no trade values returned", "format": fmt, "source": FC_SOURCE}
    rows = [sleeper_values.fc_row(v) for v in raw]
    if position:
        pos = position.upper()
        rows = [r for r in rows if r.get("position") == pos]

    priced = sleeper_auction.price_board(
        rows,
        budget=int(resolved_budget),
        num_teams=num_teams,
        roster_spots=roster_spots,
        k_def_spots=k_def_spots,
        ceiling_pct=ceiling_pct,
        limit=limit,
    )
    return {
        "platform": "yahoo",
        "league_id": league.get("league_key"),
        "league_name": league.get("name"),
        "budget": int(resolved_budget),
        "num_teams": num_teams,
        "roster_spots": roster_spots,
        "assumed_auction": not is_auction,
        "is_auction_draft": is_auction,
        "format": fmt,
        "method": {
            "source": "fantasycalc trade value scaled into auction pool",
            "ceiling_pct": ceiling_pct,
            "note": (
                "fair = market share of discretionary dollars + $1 floor; "
                "max ~= fair * (1 + ceiling_pct). Set YAHOO_AUCTION_BUDGET to override."
            ),
        },
        "players": priced,
        "sum_fair": sum(p["fair"] for p in priced),
        "note": None
        if is_auction
        else (
            "Yahoo league is not flagged as auction draft; using "
            f"${resolved_budget} budget and roster size from settings."
        ),
    }


def playoff_bracket(
    league_key: str | None = None,
    *,
    weeks: int = 4,
) -> dict[str, Any]:
    """Yahoo has no Sleeper-style bracket resource — return playoff-week boards."""
    from .league import compute_matchups

    league = compute_league(league_key)
    if isinstance(league, dict) and league.get("error"):
        return league

    settings = league.get("settings") or {}
    start = to_int(settings.get("playoff_start_week"), 0) or None
    if not start:
        return {
            "platform": "yahoo",
            "league_key": league.get("league_key"),
            "error": "playoff_start_week not found in Yahoo settings",
            "hint": "Use get_matchups(platform='yahoo', week=N) for a specific week.",
        }

    boards = []
    for week in range(int(start), int(start) + max(1, int(weeks))):
        board = compute_matchups(league.get("league_key"), week=week)
        if isinstance(board, dict) and board.get("error"):
            boards.append({"week": week, "error": board.get("error")})
        else:
            boards.append(board)

    return {
        "platform": "yahoo",
        "league_key": league.get("league_key"),
        "playoff_start_week": int(start),
        "num_playoff_teams": settings.get("num_playoff_teams"),
        "note": (
            "Yahoo Fantasy API has no winners/losers bracket resource like Sleeper. "
            "This returns scoreboards for playoff weeks instead."
        ),
        "weeks": boards,
    }


def weekly_projections(
    league_key: str | None = None,
    *,
    week: int | None = None,
    position: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Sleeper weekly projections ranked in the Yahoo league's scoring format."""
    from sleeper_core import players as sleeper_players
    from sleeper_core import projections as sleeper_proj
    from sleeper_core.http import get_json as sleeper_get_json

    league = compute_league(league_key)
    if isinstance(league, dict) and league.get("error"):
        return league

    state = sleeper_get_json("/state/nfl", cache=True) or {}
    week = week or to_int(league.get("current_week"), 0) or state.get("week") or 1
    season = str(state.get("season") or state.get("league_season") or league.get("season") or "")
    if not season:
        return {"error": "could not determine current season", "platform": "yahoo"}

    fmt = league_format(league.get("league_key"))
    if fmt.get("error"):
        return fmt
    label = str(fmt.get("scoring_format_label") or "PPR").lower()
    if "half" in label:
        field, scoring_label = "pts_half_ppr", "Half-PPR"
    elif label in {"standard", "std"} or float(fmt.get("ppr") or 0) == 0:
        field, scoring_label = "pts_std", "Standard"
    else:
        field, scoring_label = "pts_ppr", "PPR"

    proj = sleeper_proj.projections_for(season, int(week))
    if not proj:
        return {
            "error": "no projection data returned",
            "week": week,
            "season": season,
            "platform": "yahoo",
            "hint": "The undocumented projections endpoint may have changed or be unavailable.",
            "source": "api.sleeper.com projections (UNDOCUMENTED, unsupported)",
        }

    players = sleeper_players.load_players()
    pos = position.upper() if position else None
    rows = []
    for pid in proj:
        rec = sleeper_players.player_name(pid, players)
        if pos and rec["position"] != pos:
            continue
        rec["projected_points"] = sleeper_proj.proj_points(pid, proj, field)
        rows.append(rec)
    rows.sort(key=lambda r: r["projected_points"], reverse=True)

    return {
        "platform": "yahoo",
        "league_key": league.get("league_key"),
        "week": int(week),
        "season": season,
        "scoring_format": scoring_label,
        "reception_points_source": fmt.get("reception_points_source"),
        "source": "api.sleeper.com projections (UNDOCUMENTED, unsupported)",
        "players": rows[:limit],
    }


def adp(
    league_key: str | None = None,
    *,
    position: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """FFC ADP for a Yahoo league's inferred FantasyCalc format."""
    from sleeper_core import adp as sleeper_adp
    from sleeper_core import stats as sleeper_stats

    fmt = league_format(league_key)
    if fmt.get("error"):
        return fmt
    season = int(sleeper_stats.current_season())
    result = sleeper_adp.adp_rows(
        fmt,
        season,
        position=position,
        limit=limit,
        fc_values=sleeper_values.fc_values(fmt),
    )
    if isinstance(result, dict):
        result = {**result, "platform": "yahoo"}
    return result


def dynasty_tiers(
    league_key: str | None = None,
    *,
    position: str | None = None,
) -> dict[str, Any]:
    """FantasyCalc dynasty tiers using Yahoo team-count / PPR context."""
    fmt = league_format(league_key)
    if fmt.get("error"):
        return fmt
    dynasty_fmt = {**fmt, "isDynasty": True}
    raw = sleeper_values.fc_values(dynasty_fmt)
    if not raw:
        return {"error": "no trade values returned", "format": dynasty_fmt, "source": FC_SOURCE}

    pos = position.upper() if position else None
    rows = [sleeper_values.fc_row(v) for v in raw if v.get("value")]
    if pos:
        rows = [r for r in rows if r.get("position") == pos]
    rows.sort(key=lambda r: r.get("overall_rank") or 9999)

    groups: dict[int, list[dict]] = {}
    for r in rows:
        tier = r.get("tier") or 99
        entry = {k: v for k, v in r.items() if k != "tier"}
        groups.setdefault(tier, []).append(entry)

    return {
        "platform": "yahoo",
        "format": dynasty_fmt,
        "source": FC_SOURCE,
        "tiers": [
            {"tier": t, "players": players}
            for t, players in sorted(groups.items(), key=lambda kv: kv[0])
        ],
        "note": (
            "Yahoo dynasty flag is not auto-detected yet; this always pulls "
            "FantasyCalc dynasty values using the Yahoo league's PPR / QB / team count."
        ),
    }


def traded_picks_unavailable(league_key: str | None = None) -> list[dict[str, Any]]:
    """Honest gap: Yahoo has no Sleeper-style traded-picks resource."""
    lid = resolve_league_key(league_key)
    return [
        {
            "platform": "yahoo",
            "league_key": lid or None,
            "error": "yahoo_traded_picks_unsupported",
            "note": (
                "Yahoo Fantasy API does not expose a Sleeper-style traded draft "
                "picks feed. Use get_draft_picks(platform='yahoo') for completed "
                "draft results, or inspect transactions for pick-related trades."
            ),
        }
    ]

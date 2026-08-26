"""
Yahoo league format → FantasyCalc trade values, joined via sleeper_id.
"""

from __future__ import annotations

from typing import Any

from sleeper_core import values as sleeper_values
from sleeper_core.config import FC_SOURCE

from .config import YAHOO_SCORING_FORMAT
from .league import (
    _config_error,
    compute_league,
    compute_my_team,
    resolve_league_key,
    scout_team,
)
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

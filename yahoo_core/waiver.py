"""Yahoo best-effort waiver_advice. Picks/taxi are Sleeper-only gaps."""

from __future__ import annotations

from typing import Any

from sleeper_core import advice, projections as sleeper_proj, waiver as sleeper_waiver
from sleeper_core import values as sleeper_values
from sleeper_core.config import FC_SOURCE, SPORT
from sleeper_core.http import get_json as sleeper_get_json
from sleeper_core.players import load_players, player_name

from .league import (
    _config_error,
    compute_available_players,
    compute_league,
    compute_my_team,
    resolve_league_key,
    scout_team,
)
from .parse import to_int
from .values import league_format


def _rows_to_owned(report: dict[str, Any]) -> tuple[list[str], list[str]]:
    owned: list[str] = []
    starters: list[str] = []
    for row in list(report.get("starters") or []):
        sid = row.get("sleeper_id")
        if sid:
            owned.append(str(sid))
            starters.append(str(sid))
    for row in list(report.get("bench") or []):
        sid = row.get("sleeper_id")
        if sid:
            owned.append(str(sid))
    return owned, starters


def waiver_advice(
    league_key: str | None = None,
    *,
    team_name_or_manager: str | None = None,
    week: int | None = None,
    position: str | None = None,
    faab_remaining: float | None = None,
    max_adds: int = 5,
    mode: str = "auto",
) -> dict[str, Any]:
    lid = resolve_league_key(league_key)
    if not lid:
        return _config_error("YAHOO_LEAGUE_KEY is not configured")

    league = compute_league(lid)
    if isinstance(league, dict) and league.get("error"):
        return league

    if team_name_or_manager:
        report = scout_team(team_name_or_manager, lid)
    else:
        report = compute_my_team(lid)
    if isinstance(report, dict) and report.get("error"):
        return report

    fmt = league_format(lid)
    if fmt.get("error"):
        return fmt
    resolved_mode = sleeper_waiver.resolve_mode(mode, bool(fmt.get("isDynasty")))
    weights = sleeper_waiver.weights_for(resolved_mode)

    owned, starters = _rows_to_owned(report)
    players_map = load_players()
    fc_raw = sleeper_values.fc_values(fmt) or []
    fc_by_sid: dict[str, dict] = {}
    max_fc = 1.0
    for v in fc_raw:
        sid = (v.get("player") or {}).get("sleeperId")
        if sid:
            fc_by_sid[str(sid)] = v
        val = v.get("value") or 0
        if val > max_fc:
            max_fc = float(val)

    needs = sleeper_waiver.positional_needs(
        owned=owned,
        starters=starters,
        taxi=[],
        reserve=[],
        players_map=players_map,
        roster_positions=league.get("roster_positions") or [],
        fc_by_sid=fc_by_sid,
    )

    pos_filter = position.upper() if position else None
    fas = compute_available_players(lid, position=pos_filter, limit=sleeper_waiver.FA_POOL_LIMIT)
    if isinstance(fas, dict) and fas.get("error"):
        return fas

    trending_raw = sleeper_get_json(
        f"/players/{SPORT}/trending/add?lookback_hours=24&limit={sleeper_waiver.TREND_LIMIT}"
    ) or []
    trend_map = {
        str(t.get("player_id")): int(t.get("count") or 0)
        for t in trending_raw
        if t.get("player_id")
    }
    max_trend = max(trend_map.values(), default=0)

    state = sleeper_get_json(f"/state/{SPORT}", cache=True) or {}
    week = int(week or to_int(league.get("current_week"), 0) or state.get("week") or 1)
    season = str(state.get("season") or state.get("league_season") or league.get("season") or "")
    proj_map: dict[str, dict] = {}
    proj_field = "pts_ppr"
    proj_ok = True
    if season:
        try:
            label = str(fmt.get("scoring_format_label") or "PPR").lower()
            if "half" in label:
                proj_field = "pts_half_ppr"
            elif label in {"standard", "std"} or float(fmt.get("ppr") or 0) == 0:
                proj_field = "pts_std"
            else:
                proj_field = "pts_ppr"
            proj_map = sleeper_proj.projections_for(season, week) or {}
        except Exception:
            proj_ok = False
            proj_map = {}
        if not proj_map:
            proj_ok = False
    max_proj = 1.0
    for pid in proj_map:
        pts = sleeper_proj.proj_points(pid, proj_map, proj_field)
        if pts > max_proj:
            max_proj = pts

    owned_set = set(owned)
    scored_rows = []
    for row in fas:
        sid = row.get("sleeper_id")
        pid = str(sid or row.get("player_id") or "")
        if not pid or (sid and str(sid) in owned_set):
            continue
        rec = player_name(str(sid), players_map) if sid else {
            "player_id": pid,
            "name": row.get("name"),
            "position": row.get("position"),
            "team": row.get("team"),
            "injury_status": row.get("status"),
        }
        info = players_map.get(str(sid)) if sid else {}
        pts = sleeper_proj.proj_points(str(sid), proj_map, proj_field) if sid and proj_map else 0.0
        scored = sleeper_waiver.score_candidate(
            rec=rec,
            info=info,
            fc_row=fc_by_sid.get(str(sid)) if sid else None,
            trend_count=trend_map.get(str(sid), 0) if sid else 0,
            max_trend=max_trend,
            proj_pts=pts,
            max_proj=max_proj,
            max_fc=max_fc,
            needs=needs,
            weights=weights,
        )
        scored_rows.append((rec, scored, row))

    scored_rows.sort(key=lambda pair: pair[1]["score"], reverse=True)
    max_adds = max(1, min(int(max_adds or 5), 15))
    recommendations = []
    for rank, (rec, scored, _row) in enumerate(scored_rows[:max_adds], start=1):
        pid = str(rec.get("player_id"))
        recommendations.append(
            {
                "rank": rank,
                "action": "add",
                "player": {
                    "name": rec.get("name"),
                    "position": rec.get("position"),
                    "team": rec.get("team"),
                    "player_id": pid,
                },
                "score": scored["score"],
                "components": scored["components"],
                "suggested_drops": sleeper_waiver.suggest_drops(
                    add_pid=pid,
                    owned=owned,
                    starters=starters,
                    taxi=[],
                    reserve=[],
                    players_map=players_map,
                    fc_by_sid=fc_by_sid,
                    needs=needs,
                ),
                "faab": sleeper_waiver.faab_bands(scored["score"], faab_remaining),
                "reasons": scored["reasons"] or ["best remaining composite on the wire"],
            }
        )

    pass_on = []
    for rec, scored, _row in scored_rows[max_adds : max_adds + 5]:
        pass_on.append(
            {
                "player": rec.get("name"),
                "player_id": rec.get("player_id"),
                "position": rec.get("position"),
                "score": scored["score"],
                "reason": sleeper_waiver._pass_reason(rec, scored, needs),
            }
        )

    if not recommendations:
        verdict = "No Yahoo free-agent claim stands out (or the wire join is empty)."
    else:
        top_name = recommendations[0]["player"]["name"]
        verdict = f"Priority: claim {top_name}."
        if len(recommendations) > 1:
            verdict = f"Priority: claim {top_name}; secondary {recommendations[1]['player']['name']}."

    limitations = [
        "Yahoo dynasty is not auto-detected yet; pass mode='dynasty' for Gridiron-like leagues.",
        "Yahoo FA join depends on sleeper_id crosswalk — unmatched Yahoo players score without FantasyCalc.",
        "Yahoo has no Sleeper taxi/IR split; drops treat everyone as starter or bench.",
        "FAAB bands are heuristics and only populate when faab_remaining is passed.",
    ]
    if not proj_ok:
        limitations.append("Weekly projections were unavailable; that component scored 0.")

    return advice.advice_envelope(
        league_id=lid,
        platform="yahoo",
        fmt=fmt,
        season=season,
        week=week,
        subject=advice.subject_block(
            team_name=report.get("owner"),
            manager=report.get("manager"),
            roster_id=report.get("roster_id"),
        ),
        verdict=verdict,
        reasons=[
            f"{resolved_mode.title()} weights: "
            + ", ".join(f"{k.replace('_', ' ')} {int(v * 100)}%" for k, v in weights.items())
            + "."
        ],
        data_sources=["fantasycalc", "yahoo", "sleeper"],
        limitations=limitations,
        recommendations=recommendations,
        extra={
            "mode": resolved_mode,
            "weights": weights,
            "needs": [{k: n[k] for k in ("position", "severity", "detail")} for n in needs],
            "pass_on": pass_on,
            "source": FC_SOURCE,
        },
    )

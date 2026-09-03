"""
Waiver / FAAB recommendations with dynasty-aware scoring.

Weights (must stay explicit — tests assert dynasty values trade value above
weekly projection):

    Dynasty:  40% FantasyCalc, 25% need, 15% trend, 10% projection, 10% situation
    Redraft:  10% FantasyCalc, 30% need, 15% trend, 30% projection, 15% situation

Situation uses Sleeper player-map fields only (injury_status, depth_chart_order,
team) so this tool does not fan out to nflverse per candidate.
"""

from __future__ import annotations

from typing import Any

from . import advice, projections as proj
from .config import FC_SOURCE, SPORT
from .http import get_json
from .league import (
    list_free_agents,
    resolve_league_id,
    resolve_my_roster,
    resolve_roster,
)
from .players import load_players, player_name
from .values import fc_values, league_format

DYNASTY_WEIGHTS = {
    "trade_value": 0.40,
    "need": 0.25,
    "trend": 0.15,
    "projection": 0.10,
    "situation": 0.10,
}

REDRAFT_WEIGHTS = {
    "trade_value": 0.10,
    "need": 0.30,
    "trend": 0.15,
    "projection": 0.30,
    "situation": 0.15,
}

SKILL = ("QB", "RB", "WR", "TE")
STARTER_SLOTS = {"QB", "RB", "WR", "TE", "FLEX", "WRRB_FLEX", "REC_FLEX", "SUPER_FLEX"}
INJURED = {"OUT", "IR", "PUP", "SUS", "COV"}
ELITE_OVERALL_RANK = 36
FA_POOL_LIMIT = 80
TREND_LIMIT = 25


def resolve_mode(mode: str | None, is_dynasty: bool) -> str:
    requested = (mode or "auto").strip().lower()
    if requested in {"dynasty", "redraft"}:
        return requested
    return "dynasty" if is_dynasty else "redraft"


def weights_for(mode: str) -> dict[str, float]:
    return dict(DYNASTY_WEIGHTS if mode == "dynasty" else REDRAFT_WEIGHTS)


def _slot_demand(roster_positions: list[str]) -> dict[str, int]:
    """How many startable slots each skill position can fill, counting flex."""
    demand = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    flex_rb_wr_te = 0
    flex_rb_wr = 0
    flex_wr_te = 0
    superflex = 0
    for slot in roster_positions or []:
        up = str(slot).upper()
        if up == "QB":
            demand["QB"] += 1
        elif up == "RB":
            demand["RB"] += 1
        elif up == "WR":
            demand["WR"] += 1
        elif up == "TE":
            demand["TE"] += 1
        elif up == "SUPER_FLEX":
            superflex += 1
        elif up in {"FLEX", "WRRB_FLEX"} and up == "FLEX":
            flex_rb_wr_te += 1
        elif up == "WRRB_FLEX":
            flex_rb_wr += 1
        elif up == "REC_FLEX":
            flex_wr_te += 1
    # Superflex is extra QB demand first (the whole point of SF), then a FLEX.
    demand["QB"] += superflex
    demand["_flex"] = flex_rb_wr_te
    demand["_flex_rb_wr"] = flex_rb_wr
    demand["_flex_wr_te"] = flex_wr_te
    return demand


def _is_startable(info: dict, pid: str, taxi: set[str], reserve: set[str]) -> bool:
    if pid in taxi or pid in reserve:
        return False
    inj = (info.get("injury_status") or "").upper()
    if inj in INJURED:
        return False
    return True


def positional_needs(
    *,
    owned: list[str],
    starters: list[str],
    taxi: list[str],
    reserve: list[str],
    players_map: dict,
    roster_positions: list[str],
    fc_by_sid: dict[str, dict],
) -> list[dict[str, Any]]:
    """Need scores per skill position from roster construction + injuries."""
    demand = _slot_demand(roster_positions)
    taxi_set = {str(p) for p in taxi}
    reserve_set = {str(p) for p in reserve}
    starter_set = {str(p) for p in starters}

    counts = {pos: [] for pos in SKILL}
    for pid in owned:
        rec = player_name(str(pid), players_map)
        pos = (rec.get("position") or "").upper()
        if pos not in counts:
            continue
        info = players_map.get(str(pid)) or {}
        inj = rec.get("injury_status") or info.get("injury_status")
        startable = _is_startable({**info, "injury_status": inj}, str(pid), taxi_set, reserve_set)
        val = (fc_by_sid.get(str(pid)) or {}).get("value") or 0
        counts[pos].append(
            {
                "player_id": str(pid),
                "name": rec.get("name"),
                "startable": startable,
                "is_starter": str(pid) in starter_set,
                "value": val,
                "injured": bool(inj) and str(inj).upper() in INJURED,
            }
        )

    needs = []
    for pos in SKILL:
        required = demand.get(pos, 0)
        startable_n = sum(1 for p in counts[pos] if p["startable"])
        quality = sorted((p["value"] or 0) for p in counts[pos] if p["startable"])
        quality_top = sum(quality[-required:]) if required else 0
        if startable_n < required:
            severity = "high"
            detail = f"{startable_n} startable {pos} for {required} slot(s)"
            score = 95.0 if startable_n == 0 else 80.0
        elif startable_n == required:
            severity = "medium"
            detail = f"no {pos} bench behind {required} starter slot(s)"
            score = 60.0
        elif startable_n == required + 1:
            severity = "low"
            detail = f"{startable_n} startable {pos} (thin bench)"
            score = 35.0
        else:
            severity = "none"
            detail = f"{startable_n} startable {pos}"
            score = 10.0
        # Superflex: even with 2 QBs, a third is still a real need.
        if pos == "QB" and demand.get("QB", 0) >= 2 and startable_n <= 2:
            if severity == "none":
                severity = "low"
            score = max(score, 45.0)
            detail += "; Superflex QB depth is scarce"
        needs.append(
            {
                "position": pos,
                "severity": severity,
                "detail": detail,
                "startable": startable_n,
                "slots": required,
                "score": score,
                "quality_top": quality_top,
            }
        )
    return needs


def need_score_for(position: str, needs: list[dict]) -> float:
    pos = (position or "").upper()
    for row in needs:
        if row["position"] == pos:
            return float(row["score"])
    return 15.0  # K/DEF/unknown: almost never a dynasty priority


def _fc_component(val: float | None, max_val: float) -> float:
    if not val or max_val <= 0:
        return 0.0
    return min(100.0, (float(val) / max_val) * 100.0)


def _trend_component(count: int, max_count: int) -> float:
    if max_count <= 0 or count <= 0:
        return 0.0
    return min(100.0, (count / max_count) * 100.0)


def _proj_component(points: float, max_points: float) -> float:
    if max_points <= 0 or points <= 0:
        return 0.0
    return min(100.0, (points / max_points) * 100.0)


def situation_score(info: dict | None, rec: dict | None = None) -> float:
    info = info or {}
    rec = rec or {}
    team = rec.get("team") or info.get("team")
    if not team:
        return 20.0
    inj = (rec.get("injury_status") or info.get("injury_status") or "").upper()
    if inj in INJURED:
        base = 25.0
    elif inj == "DOUBTFUL":
        base = 40.0
    elif inj == "QUESTIONABLE":
        base = 55.0
    else:
        base = 70.0
    depth = info.get("depth_chart_order")
    try:
        depth_n = int(depth) if depth is not None else None
    except (TypeError, ValueError):
        depth_n = None
    if depth_n == 1:
        base += 25
    elif depth_n == 2:
        base += 10
    elif depth_n is not None and depth_n >= 3:
        base -= 15
    return min(100.0, max(0.0, base))


def score_candidate(
    *,
    rec: dict,
    info: dict | None,
    fc_row: dict | None,
    trend_count: int,
    max_trend: int,
    proj_pts: float,
    max_proj: float,
    max_fc: float,
    needs: list[dict],
    weights: dict[str, float],
) -> dict[str, Any]:
    val = (fc_row or {}).get("value")
    tv = _fc_component(val, max_fc)
    need = need_score_for(rec.get("position"), needs)
    trend = _trend_component(trend_count, max_trend)
    pr = _proj_component(proj_pts, max_proj)
    sit = situation_score(info, rec)
    score = (
        weights["trade_value"] * tv
        + weights["need"] * need
        + weights["trend"] * trend
        + weights["projection"] * pr
        + weights["situation"] * sit
    )
    reasons = []
    pos = rec.get("position")
    for row in needs:
        if row["position"] == pos and row["severity"] in {"high", "medium"}:
            reasons.append(f"fills {pos} hole ({row['detail']})")
            break
    if val:
        reasons.append(f"FantasyCalc {int(val)}")
    if trend_count:
        reasons.append(f"trending +{trend_count} adds")
    if proj_pts:
        reasons.append(f"projected {proj_pts:.1f} this week")
    inj = rec.get("injury_status") or (info or {}).get("injury_status")
    if inj:
        reasons.append(f"injury flag {inj}")
    depth = (info or {}).get("depth_chart_order")
    if depth == 1:
        reasons.append("listed first on depth chart")
    elif depth and int(depth) >= 3:
        reasons.append(f"crowded depth chart (order {depth})")
    return {
        "score": round(score, 1),
        "components": {
            "trade_value": round(tv, 1),
            "need": round(need, 1),
            "trend": round(trend, 1),
            "projection": round(pr, 1),
            "situation": round(sit, 1),
        },
        "reasons": reasons,
        "value": val,
        "trend_count": trend_count,
        "projected_points": proj_pts,
    }


def faab_bands(score: float, remaining: float | None) -> dict[str, Any]:
    """Heuristic bid bands. Never presented as market truth."""
    if remaining is None:
        return {
            "low": None,
            "likely": None,
            "aggressive": None,
            "unit": "unknown",
            "heuristic": True,
            "note": "Pass faab_remaining (or a league waiver budget) for bid bands.",
        }
    remaining = max(0.0, float(remaining))
    low_pct = 0.02 + (score / 100.0) * 0.06
    likely_pct = 0.04 + (score / 100.0) * 0.12
    agg_pct = 0.08 + (score / 100.0) * 0.22
    def dollars(pct: float) -> int:
        if remaining <= 0:
            return 0
        return int(max(1, min(remaining, round(remaining * pct))))
    return {
        "low": dollars(low_pct),
        "likely": dollars(likely_pct),
        "aggressive": dollars(agg_pct),
        "unit": "dollars",
        "remaining": remaining,
        "heuristic": True,
        "note": "Heuristic bands as a share of remaining FAAB, not league-market truth.",
    }


def suggest_drops(
    *,
    add_pid: str,
    owned: list[str],
    starters: list[str],
    taxi: list[str],
    reserve: list[str],
    players_map: dict,
    fc_by_sid: dict[str, dict],
    needs: list[dict],
    limit: int = 3,
) -> list[dict[str, Any]]:
    taxi_set = {str(p) for p in taxi}
    reserve_set = {str(p) for p in reserve}
    starter_set = {str(p) for p in starters}
    need_by_pos = {n["position"]: n for n in needs}

    ranked = []
    for pid in owned:
        if str(pid) == str(add_pid):
            continue
        rec = player_name(str(pid), players_map)
        pos = (rec.get("position") or "").upper()
        fc = fc_by_sid.get(str(pid)) or {}
        val = fc.get("value") or 0
        overall = fc.get("overallRank") or 999
        layer = 0
        if str(pid) in taxi_set:
            layer = 0
            loc = "taxi"
        elif str(pid) in reserve_set:
            layer = 1
            loc = "ir"
        elif str(pid) not in starter_set:
            layer = 2
            loc = "bench"
        else:
            layer = 3
            loc = "starter"
        need = need_by_pos.get(pos) or {}
        # Prefer dropping a position we are deep at.
        surplus_bonus = 0 if need.get("severity") in {"high", "medium"} else -15
        inj = (rec.get("injury_status") or "").upper()
        inj_bonus = -20 if inj in INJURED else 0
        drop_score = layer * 1000 + val + surplus_bonus + inj_bonus
        elite = overall <= ELITE_OVERALL_RANK or (val and val >= 2500)
        ranked.append(
            {
                "player_id": str(pid),
                "name": rec.get("name"),
                "position": pos,
                "team": rec.get("team"),
                "value": val,
                "location": loc,
                "elite": bool(elite),
                "drop_score": drop_score,
                "injury_status": rec.get("injury_status"),
            }
        )
    ranked.sort(key=lambda r: r["drop_score"])
    out = []
    for row in ranked[:limit]:
        reason_bits = [row["location"]]
        if row["value"]:
            reason_bits.append(f"FC {int(row['value'])}")
        else:
            reason_bits.append("no FantasyCalc value")
        if row.get("injury_status"):
            reason_bits.append(str(row["injury_status"]))
        item = {
            "name": row["name"],
            "player_id": row["player_id"],
            "position": row["position"],
            "reason": ", ".join(reason_bits),
        }
        if row["elite"]:
            item["risk"] = "high"
            item["reason"] += " — elite asset, only drop if you mean it"
        out.append(item)
    return out


def _pass_reason(rec: dict, scored: dict, needs: list[dict]) -> str:
    pos = rec.get("position")
    need = next((n for n in needs if n["position"] == pos), None)
    if need and need["severity"] == "none":
        return f"no roster need at {pos}"
    comps = scored.get("components") or {}
    if comps.get("situation", 100) < 40:
        return "crowded depth chart / injury situation"
    if not scored.get("value"):
        return "no FantasyCalc value and weak weekly outlook"
    return "lower composite than the claimed names"


def waiver_advice(
    league_id: str | None = None,
    *,
    team_name_or_manager: str | None = None,
    week: int | None = None,
    position: str | None = None,
    faab_remaining: float | None = None,
    max_adds: int = 5,
    mode: str = "auto",
) -> dict[str, Any]:
    lid = resolve_league_id(league_id)
    league = get_json(f"/league/{lid}", cache=True) or {}
    state = get_json(f"/state/{SPORT}", cache=True) or {}
    week = int(week or state.get("week") or 1)
    season = str(state.get("season") or state.get("league_season") or "")
    fmt = league_format(lid)
    resolved_mode = resolve_mode(mode, bool(fmt.get("isDynasty")))
    weights = weights_for(resolved_mode)

    resolved = (
        resolve_roster(lid, team_name_or_manager)
        if team_name_or_manager
        else resolve_my_roster(lid)
    )
    if not resolved:
        return {"error": "could not resolve team", "query": team_name_or_manager, "league_id": lid}

    roster = resolved["roster"]
    owner = resolved["owner"]
    players_map = load_players()
    owned = [str(p) for p in (roster.get("players") or [])]
    starters = [str(p) for p in (roster.get("starters") or []) if p]
    taxi = [str(p) for p in (roster.get("taxi") or [])]
    reserve = [str(p) for p in (roster.get("reserve") or [])]
    rostered_league = {
        str(pid)
        for r in (get_json(f"/league/{lid}/rosters", cache=True) or [])
        for pid in (r.get("players") or [])
    }

    values = fc_values(fmt)
    fc_by_sid: dict[str, dict] = {}
    max_fc = 1.0
    for v in values or []:
        sid = (v.get("player") or {}).get("sleeperId")
        if sid:
            fc_by_sid[str(sid)] = v
        val = v.get("value") or 0
        if val > max_fc:
            max_fc = float(val)

    needs = positional_needs(
        owned=owned,
        starters=starters,
        taxi=taxi,
        reserve=reserve,
        players_map=players_map,
        roster_positions=league.get("roster_positions") or [],
        fc_by_sid=fc_by_sid,
    )

    pos_filter = position.upper() if position else None
    fas = list_free_agents(lid, position=pos_filter, limit=FA_POOL_LIMIT)

    trending_raw = get_json(
        f"/players/{SPORT}/trending/add?lookback_hours=24&limit={TREND_LIMIT}"
    ) or []
    trend_map = {str(t.get("player_id")): int(t.get("count") or 0) for t in trending_raw if t.get("player_id")}
    max_trend = max(trend_map.values(), default=0)

    # Include trending FAs that missed the search_rank slice.
    have = {str(r.get("player_id")) for r in fas}
    for pid, count in trend_map.items():
        if pid in have or pid in rostered_league:
            continue
        rec = player_name(pid, players_map)
        if pos_filter and rec.get("position") != pos_filter:
            continue
        pos = rec.get("position")
        if pos not in SKILL and pos not in {"K", "DEF"}:
            continue
        rec["search_rank"] = None
        rec["status"] = (players_map.get(pid) or {}).get("status")
        rec["_from_trending"] = True
        rec["_trend_count"] = count
        fas.append(rec)

    proj_map: dict[str, dict] = {}
    proj_field = "pts_ppr"
    proj_ok = True
    if season:
        try:
            proj_field, _fmt_label = proj.scoring_field(lid)
            proj_map = proj.projections_for(season, week) or {}
        except Exception:
            proj_ok = False
            proj_map = {}
        if not proj_map:
            proj_ok = False
    max_proj = 0.0
    for pid, stats in proj_map.items():
        pts = proj.proj_points(pid, proj_map, proj_field)
        if pts > max_proj:
            max_proj = pts
    max_proj = max_proj or 1.0

    settings = league.get("settings") or {}
    budget = settings.get("waiver_budget")
    used = (roster.get("settings") or {}).get("waiver_budget_used")
    remaining = faab_remaining
    if remaining is None and budget is not None:
        try:
            remaining = float(budget) - float(used or 0)
        except (TypeError, ValueError):
            remaining = float(budget)

    scored_rows = []
    for rec in fas:
        pid = str(rec.get("player_id"))
        if pid in rostered_league or pid in owned:
            continue
        info = players_map.get(pid) or {}
        fc = fc_by_sid.get(pid)
        pts = proj.proj_points(pid, proj_map, proj_field) if proj_map else 0.0
        scored = score_candidate(
            rec=rec,
            info=info,
            fc_row=fc,
            trend_count=trend_map.get(pid, 0),
            max_trend=max_trend,
            proj_pts=pts,
            max_proj=max_proj,
            max_fc=max_fc,
            needs=needs,
            weights=weights,
        )
        scored_rows.append((rec, scored))

    scored_rows.sort(key=lambda pair: pair[1]["score"], reverse=True)
    max_adds = max(1, min(int(max_adds or 5), 15))
    top = scored_rows[:max_adds]
    rest = scored_rows[max_adds:]

    recommendations = []
    for rank, (rec, scored) in enumerate(top, start=1):
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
                    "injury_status": rec.get("injury_status"),
                },
                "score": scored["score"],
                "components": scored["components"],
                "suggested_drops": suggest_drops(
                    add_pid=pid,
                    owned=owned,
                    starters=starters,
                    taxi=taxi,
                    reserve=reserve,
                    players_map=players_map,
                    fc_by_sid=fc_by_sid,
                    needs=needs,
                ),
                "faab": faab_bands(scored["score"], remaining),
                "reasons": scored["reasons"] or ["best remaining composite on the wire"],
            }
        )

    pass_on = []
    for rec, scored in rest[:5]:
        pass_on.append(
            {
                "player": rec.get("name"),
                "player_id": rec.get("player_id"),
                "position": rec.get("position"),
                "score": scored["score"],
                "reason": _pass_reason(rec, scored, needs),
            }
        )

    high_needs = [n for n in needs if n["severity"] in {"high", "medium"}]
    if not fas:
        verdict = "Wire is barren — no free agents matched the filter."
    elif not recommendations:
        verdict = "Roster looks set relative to this week's wire; no claim stands out."
    elif not high_needs and (recommendations[0]["score"] < 45):
        names = ", ".join(r["player"]["name"] for r in recommendations[:2] if r["player"].get("name"))
        verdict = (
            "Roster looks set; no urgent claim. "
            f"Optional stashes if you have a cut: {names}."
        )
    else:
        top_name = recommendations[0]["player"]["name"]
        drop0 = (recommendations[0].get("suggested_drops") or [{}])[0].get("name")
        extra = f" Drop {drop0} if you need a roster spot." if drop0 else ""
        if len(recommendations) == 1:
            verdict = f"Priority: claim {top_name}.{extra}"
        else:
            second = recommendations[1]["player"]["name"]
            verdict = f"Priority: claim {top_name}; secondary {second}.{extra}"
        if high_needs:
            holes = ", ".join(n["position"] for n in high_needs)
            verdict += f" Roster holes: {holes}."

    limitations = [
        "Waiver ranks are a weighted heuristic, not a projection or market quote.",
        "FAAB bands are heuristics labelled as such — league culture varies.",
        "Situation uses Sleeper player-map depth/injury flags, not nflverse snapshots.",
    ]
    if not proj_ok:
        limitations.append(
            "Weekly projections were unavailable; that component scored 0. "
            "Try get_projections or start_sit_advice after the endpoint recovers."
        )
    if not values:
        limitations.append("FantasyCalc returned no values; trade-value component scored 0.")

    reasons = [
        f"{resolved_mode.title()} weights: "
        + ", ".join(f"{k.replace('_', ' ')} {int(v * 100)}%" for k, v in weights.items())
        + ".",
    ]
    for n in needs:
        if n["severity"] != "none":
            reasons.append(f"{n['position']}: {n['severity']} — {n['detail']}")
    if not high_needs:
        reasons.append("No high-severity positional hole; ranking leans on value and trend.")

    return advice.advice_envelope(
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
        verdict=verdict,
        reasons=reasons,
        data_sources=["fantasycalc", "sleeper"],
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

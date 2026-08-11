"""
Convert FantasyCalc trade values into auction-dollar bid targets.

Fair price scales each player's market value into the league's auction pool.
Max price is a small stretch above fair for primary targets.

Same ownership boundary as everywhere else in this package:

    this side   what the numbers ARE     fair / max dollars from market value
    that side   what they mean           nomination strategy, room reads

Nothing here nominates, grades, or recommends a build — it prices the board.
"""

from __future__ import annotations


def resolve_auction_settings(lid: str) -> dict:
    """Pull budget / rounds / team count from the league's auction draft.

    Prefers the newest draft with type == "auction". Returns an error dict
    when the league has no auction draft configured yet.
    """
    from . import values as _values
    from .http import get_json

    league = get_json(f"/league/{lid}", cache=True) or {}
    drafts = get_json(f"/league/{lid}/drafts", cache=True) or []
    auction = next((d for d in drafts if d.get("type") == "auction"), None)
    positions = league.get("roster_positions") or []
    k_def_spots = sum(1 for p in positions if p in ("K", "DEF"))

    if not auction:
        # Snake / linear leagues still benefit from a $200 planning sheet.
        num_teams = int(league.get("total_rosters") or 12)
        roster_spots = len(positions) or 15
        budget = 200
        return {
            "league_id": lid,
            "league_name": league.get("name"),
            "draft_id": None,
            "budget": budget,
            "num_teams": num_teams,
            "roster_spots": roster_spots,
            "k_def_spots": k_def_spots,
            "total_money": budget * num_teams,
            "format": _values.league_format(lid),
            "assumed_auction": True,
            "note": (
                "League has no auction draft; using $200 budget and "
                "roster size from league settings."
            ),
        }

    settings = auction.get("settings") or {}
    roster_spots = int(settings.get("rounds") or len(positions) or 15)
    num_teams = int(settings.get("teams") or league.get("total_rosters") or 12)
    budget = int(settings.get("budget") or 200)

    return {
        "league_id": lid,
        "league_name": league.get("name"),
        "draft_id": auction.get("draft_id"),
        "budget": budget,
        "num_teams": num_teams,
        "roster_spots": roster_spots,
        "k_def_spots": k_def_spots,
        "total_money": budget * num_teams,
        "format": _values.league_format(lid),
        "assumed_auction": False,
    }


def price_board(
    players: list[dict],
    *,
    budget: int = 200,
    num_teams: int = 12,
    roster_spots: int = 15,
    k_def_spots: int = 2,
    ceiling_pct: float = 0.12,
    tail_mass: float = 0.18,
    limit: int | None = None,
) -> list[dict]:
    """Map {name, position, value, ...} rows to fair / max auction dollars.

    Pool math:
      total_money     = budget * num_teams
      min_bids        = roster_spots * num_teams   # every slot costs >= $1
      discretionary   = total_money - min_bids     # dollars above the floor
      total_value     = sum(top values) * (1 + tail_mass)

    fair = round(player_value / total_value * discretionary) + 1
    max  = round(fair * (1 + ceiling_pct))  (at least fair)

    `tail_mass` stands in for the long-tail of cheap skill players not in the
    input list so the top of the board is not over-allocated.
    """
    ranked = sorted(
        (p for p in players if (p.get("value") or 0) > 0),
        key=lambda p: p.get("value") or 0,
        reverse=True,
    )
    if limit is not None:
        ranked = ranked[:limit]

    sum_top = sum(float(p["value"]) for p in ranked)
    if sum_top <= 0:
        return []

    total_money = budget * num_teams
    min_bids = roster_spots * num_teams
    discretionary = max(0.0, float(total_money - min_bids))
    total_value = sum_top * (1.0 + tail_mass)

    out: list[dict] = []
    for p in ranked:
        value = float(p["value"])
        fair = max(1, int(round(value / total_value * discretionary)) + 1)
        ceiling = max(fair, int(round(fair * (1.0 + ceiling_pct))))
        row = {
            "name": p.get("name"),
            "position": p.get("position"),
            "team": p.get("team"),
            "sleeper_id": p.get("sleeper_id"),
            "market_value": int(value) if value == int(value) else value,
            "fair": fair,
            "max": ceiling,
            "overall_rank": p.get("overall_rank"),
            "position_rank": p.get("position_rank"),
        }
        out.append(row)
    return out


def auction_budgets(
    lid: str,
    *,
    limit: int = 80,
    ceiling_pct: float = 0.12,
    tail_mass: float = 0.18,
    position: str | None = None,
) -> dict:
    """End-to-end: league auction settings + FantasyCalc → bid targets."""
    from . import values as _values

    settings = resolve_auction_settings(lid)
    if settings.get("error"):
        return settings

    fmt = settings["format"]
    raw = _values.fc_values(fmt)
    if not raw:
        return {
            "error": "no trade values returned",
            "format": fmt,
            "league_id": lid,
        }

    rows = [_values.fc_row(v) for v in raw]
    if position:
        pos = position.upper()
        rows = [r for r in rows if r.get("position") == pos]

    priced = price_board(
        rows,
        budget=settings["budget"],
        num_teams=settings["num_teams"],
        roster_spots=settings["roster_spots"],
        k_def_spots=settings["k_def_spots"],
        ceiling_pct=ceiling_pct,
        tail_mass=tail_mass,
        limit=limit,
    )

    out = {
        "league_id": settings["league_id"],
        "league_name": settings["league_name"],
        "draft_id": settings["draft_id"],
        "budget": settings["budget"],
        "num_teams": settings["num_teams"],
        "roster_spots": settings["roster_spots"],
        "assumed_auction": bool(settings.get("assumed_auction")),
        "format": fmt,
        "method": {
            "source": "fantasycalc trade value scaled into auction pool",
            "ceiling_pct": ceiling_pct,
            "tail_mass": tail_mass,
            "note": (
                "fair = market share of discretionary dollars + $1 floor; "
                "max ~= fair * (1 + ceiling_pct). K/DEF should stay $1."
            ),
        },
        "players": priced,
        "sum_fair": sum(p["fair"] for p in priced),
    }
    if settings.get("note"):
        out["note"] = settings["note"]
    return out

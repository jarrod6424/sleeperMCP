"""
Draft-pick token parsing and heuristic schedule values.

Picks are not a FantasyCalc product. Values here are a documented in-repo
schedule so dynasty trade analysis can include the asset that often decides
the deal. They are labelled heuristic in every tool response.

Valuation models
----------------
schedule (default)
    Hybrid of (A) static Superflex / 1QB tables and (B) FantasyCalc rank-band
    means from the *same* board the league already uses. When the board has
    enough ranked players, a 12-team first is the mean value of overall ranks
    4–9 (mid-round), then slot-adjusted. Superflex vs 1QB is then whatever
    FantasyCalc already priced into that board — we do not apply a second SF
    multiplier on top of (B). If the band is empty, fall back to (A).

manual
    Caller passes pick_overrides {token: value}. Those tokens use the
    override; everything else still uses schedule.

Static fallback (FantasyCalc-like points, 12-team dynasty)
----------------------------------------------------------
Calibrated so a Superflex mid 1st is 2800 and a mid 2nd is 1100, matching
the FRD examples.

    Round  SF early / mid / late     1QB early / mid / late
    1      3360 / 2800 / 2296        2520 / 2100 / 1722
    2      1344 / 1100 / 858         1008 /  825 /  644
    3       560 /  420 / 336          420 /  315 /  252
    4       252 /  196 / 140          189 /  147 /  105
    5+     mid *= 0.55 each round, same slot ratios

Slot multipliers applied to a mid value: early 1.20, mid 1.00, late 0.82.
`slot_estimate=auto` maps a team's standings third to those slots
(bottom third = early, top third = late). Unknown origin → mid.
"""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------
# Documented constants
# --------------------------------------------------------------------------

SLOT_MULT = {
    "early": 1.20,
    "mid": 1.00,
    "late": 0.82,
}

# Mid-round fallback by (num_qbs_bucket, round). num_qbs_bucket is 2 for
# Superflex / 2QB, 1 otherwise.
ROUND_MID_SF = {
    1: 2800,
    2: 1100,
    3: 420,
    4: 196,
    5: 108,
    6: 59,
}
ROUND_MID_1QB = {
    1: 2100,
    2: 825,
    3: 315,
    4: 147,
    5: 81,
    6: 45,
}

MAX_PRICED_ROUND = 6

ORDINALS = {
    "1st": 1,
    "2nd": 2,
    "3rd": 3,
    "4th": 4,
    "5th": 5,
    "6th": 6,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
}

_YEAR = r"(?P<season>20\d{2})"
_ORD = r"(?P<ord>1st|2nd|3rd|4th|5th|6th|first|second|third|fourth|fifth|sixth)"
_ROUND_N = r"round\s+(?P<round>\d+)"

# 2027 1st / 2027 Round 1, with optional "from TEAM" or "(roster 11)" / "(other)"
_PICK_RE = re.compile(
    rf"^\s*{_YEAR}\s+(?:{_ROUND_N}|{_ORD})\s*"
    rf"(?:from\s+(?P<from_team>.+?)|(?:\((?:roster\s+(?P<roster_id>\d+)|(?P<other>other))\)))?"
    rf"\s*$",
    re.IGNORECASE,
)


def looks_like_pick(token: str) -> bool:
    """True when a string is trying to be a pick, even if we cannot price it.

    Used to keep pick-shaped junk out of the player-name matcher so we do not
    silently treat "2027 1st" as a missing player.
    """
    raw = str(token or "").strip()
    if not raw:
        return False
    if _PICK_RE.match(raw):
        return True
    return bool(
        re.search(
            r"20\d{2}\s+(?:round|\d+(?:st|nd|rd|th)|first|second|third|pick)",
            raw,
            re.I,
        )
    )


def parse_pick_token(token: str) -> dict[str, Any]:
    """Parse a pick token into a normalized record or an error reason.

    Always returns a dict. `ok` is True only when season + round are known.
    """
    raw = str(token or "").strip()
    if not raw:
        return {"ok": False, "token": token, "reason": "empty_token"}

    match = _PICK_RE.match(raw)
    if not match:
        if looks_like_pick(raw):
            return {"ok": False, "token": raw, "reason": "unparseable_pick"}
        return {"ok": False, "token": raw, "reason": "not_a_pick"}

    season = match.group("season")
    round_n = match.group("round")
    if round_n is not None:
        rnd = int(round_n)
    else:
        rnd = ORDINALS[match.group("ord").lower()]

    if rnd < 1:
        return {"ok": False, "token": raw, "reason": "invalid_round", "season": season, "round": rnd}

    from_team = (match.group("from_team") or "").strip() or None
    if from_team:
        from_team = from_team.strip(" ()")
    roster_id = match.group("roster_id")
    other = match.group("other")

    origin: dict[str, Any] = {}
    if roster_id:
        origin["roster_id"] = int(roster_id)
    if from_team:
        origin["from_team"] = from_team
    if other:
        origin["other"] = True

    return {
        "ok": True,
        "token": raw,
        "season": str(season),
        "round": rnd,
        "origin": origin,
    }


def slot_from_rank(rank: int | None, num_teams: int) -> str:
    """Map a standings rank (1 = first place) onto early/mid/late.

    Contenders (top third) draft late; tanks (bottom third) draft early.
    """
    if not rank or not num_teams or num_teams < 1:
        return "mid"
    third = max(1, num_teams // 3)
    if rank <= third:
        return "late"
    if rank > num_teams - third:
        return "early"
    return "mid"


def _mid_table(num_qbs: int) -> dict[int, int]:
    return ROUND_MID_SF if int(num_qbs or 1) >= 2 else ROUND_MID_1QB


def static_pick_value(round_n: int, slot: str, num_qbs: int) -> int | None:
    """Fallback schedule value. None if the round is beyond what we will invent."""
    if round_n < 1:
        return None
    table = _mid_table(num_qbs)
    if round_n in table:
        mid = table[round_n]
    elif round_n > MAX_PRICED_ROUND:
        # Still price deep rounds with the decay rather than silently dropping
        # a 2028 7th, but never fabricate a 1st-like number.
        mid = table[MAX_PRICED_ROUND]
        extra = round_n - MAX_PRICED_ROUND
        mid = max(15, int(round(mid * (0.55 ** extra))))
    else:
        return None
    mult = SLOT_MULT.get(slot, SLOT_MULT["mid"])
    return int(round(mid * mult))


def _fc_ranked_values(fc_rows: list[dict] | None) -> list[int]:
    """Sorted player values, best first. Prefers overallRank, else value."""
    if not fc_rows:
        return []
    decorated = []
    for row in fc_rows:
        player = row.get("player") if isinstance(row.get("player"), dict) else {}
        val = row.get("value")
        if val is None:
            continue
        try:
            val_n = int(round(float(val)))
        except (TypeError, ValueError):
            continue
        rank = row.get("overallRank")
        if rank is None:
            rank = row.get("overall_rank")
        try:
            rank_n = int(rank) if rank is not None else None
        except (TypeError, ValueError):
            rank_n = None
        decorated.append((rank_n, -val_n, val_n, player))
    # overallRank ascending when present; otherwise value descending.
    has_rank = any(r[0] is not None for r in decorated)
    if has_rank:
        decorated.sort(key=lambda t: (t[0] is None, t[0] if t[0] is not None else 10**9))
    else:
        decorated.sort(key=lambda t: t[1])
    return [t[2] for t in decorated]


def fc_band_mid(fc_rows: list[dict] | None, round_n: int, num_teams: int) -> int | None:
    """Mean FantasyCalc value of the mid-round ADP/rank band for `round_n`.

    For a 12-team league, round 1 mid is ranks 4–9, round 2 is 16–21, etc.
    Needs at least three values in the band or we fall back to static.
    """
    ranked = _fc_ranked_values(fc_rows)
    if not ranked or round_n < 1 or num_teams < 1:
        return None
    # Need a full round of ranked players or the "mid 1st" window is just
    # "average of whoever we have", which silently inflates sparse test boards
    # and offseason slices into first-round values.
    if len(ranked) < num_teams:
        return None
    start = (round_n - 1) * num_teams + max(1, num_teams // 3)  # 4 in a 12-team
    end = (round_n - 1) * num_teams + (num_teams - max(1, num_teams // 3))  # 9
    sl = ranked[start - 1 : end]
    if len(sl) < 3:
        return None
    return int(round(sum(sl) / len(sl)))


def schedule_pick_value(
    *,
    round_n: int,
    slot: str = "mid",
    num_qbs: int = 2,
    num_teams: int = 12,
    fc_rows: list[dict] | None = None,
) -> tuple[int | None, str]:
    """Return (value, source) where source is 'fantasycalc_band' or 'static_schedule'.

    Value is None only for nonsense rounds (round < 1).
    """
    slot = slot if slot in SLOT_MULT else "mid"
    band = fc_band_mid(fc_rows, round_n, num_teams)
    if band is not None:
        return int(round(band * SLOT_MULT[slot])), "fantasycalc_band"
    static = static_pick_value(round_n, slot, num_qbs)
    if static is None:
        return None, "unpriced_round"
    return static, "static_schedule"


def price_parsed_pick(
    parsed: dict[str, Any],
    *,
    slot: str = "mid",
    num_qbs: int = 2,
    num_teams: int = 12,
    fc_rows: list[dict] | None = None,
    override: int | float | None = None,
) -> dict[str, Any]:
    """Turn a successful parse into the analyze_trade pick row."""
    token = parsed.get("token")
    if override is not None:
        try:
            value = int(round(float(override)))
        except (TypeError, ValueError):
            value = None
            source = "manual_unusable"
        else:
            source = "manual"
        return {
            "token": token,
            "normalized": {"season": parsed.get("season"), "round": parsed.get("round")},
            "origin": parsed.get("origin") or {},
            "value": value,
            "slot_assumption": slot,
            "source": source,
        }

    value, source = schedule_pick_value(
        round_n=int(parsed["round"]),
        slot=slot,
        num_qbs=num_qbs,
        num_teams=num_teams,
        fc_rows=fc_rows,
    )
    return {
        "token": token,
        "normalized": {"season": parsed.get("season"), "round": parsed.get("round")},
        "origin": parsed.get("origin") or {},
        "value": value,
        "slot_assumption": slot,
        "source": source,
    }

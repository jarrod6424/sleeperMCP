"""
Draft-pick token parsing and values.

Valuation models
----------------
schedule (default)
    FantasyCalc already publishes draft picks on the same board as players
    (``position == "PICK"``, names like ``2027 1st (Late)``). A pick token
    uses that quote when the season, round, and early/mid/late tier match.
    The quote is not scaled again: FantasyCalc's late price already is the
    late price.

    If that year only has one published price (``2028 1st``, no tier), that
    single quote is used and the slot multiplier is not applied.

    If the board has no row for that season and round, fall back to the
    static Superflex / 1QB table below. That fallback is a heuristic. It is
    labelled ``static_schedule`` and is not a market quote.

    Do not price a pick as the average of veteran players at the same
    startup-draft slot. A 12-team late 1st is not the 4th–8th best player
    in the league (that mistake priced a late 2027 1st at 6858). FantasyCalc
    ranks the pick itself around overall 86.

manual
    Caller passes pick_overrides {token: value}. Those tokens use the
    override; everything else still uses schedule.

Static fallback (FantasyCalc-like points, 12-team dynasty)
----------------------------------------------------------
Calibrated so a Superflex mid 1st is 2800 and a mid 2nd is 1100, matching
the FRD examples. Used only when the FantasyCalc board has no matching pick.

    Round  SF early / mid / late     1QB early / mid / late
    1      3360 / 2800 / 2296        2520 / 2100 / 1722
    2      1344 / 1100 / 858         1008 /  825 /  644
    3       560 /  420 / 336          420 /  315 /  252
    4       252 /  196 / 140          189 /  147 /  105
    5+     mid *= 0.55 each round, same slot ratios

Slot multipliers apply to the static table only: early 1.20, mid 1.00,
late 0.82. ``slot_estimate=auto`` maps a team's standings third to those
slots (bottom third = early, top third = late). Unknown origin → mid.
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


_FC_PICK_NAME = re.compile(
    r"^(?P<season>20\d{2})\s+(?P<ord>1st|2nd|3rd|4th|5th|6th)"
    r"(?:\s+\((?P<slot>early|mid|late)\))?\s*$",
    re.IGNORECASE,
)
# FantasyCalc sleeper ids: FP_2027_late_0 is the late 1st (round index 0);
# FP_2028_1 is the unslotted 2028 1st (round number, not an index).
_FC_PICK_ID_SLOT = re.compile(
    r"^FP_(?P<season>20\d{2})_(?P<slot>early|mid|late)_(?P<round0>\d+)$",
    re.IGNORECASE,
)
_FC_PICK_ID_PLAIN = re.compile(
    r"^FP_(?P<season>20\d{2})_(?P<round>\d+)$",
    re.IGNORECASE,
)


def _fc_pick_identity(row: dict) -> dict[str, Any] | None:
    """Parse one FantasyCalc value row if it is a draft pick. Else None."""
    player = row.get("player") if isinstance(row.get("player"), dict) else {}
    if (player.get("position") or "").upper() != "PICK":
        return None
    try:
        value = int(round(float(row.get("value"))))
    except (TypeError, ValueError):
        return None

    name = (player.get("name") or "").strip()
    season = None
    round_n = None
    slot = None
    match = _FC_PICK_NAME.match(name)
    if match:
        season = match.group("season")
        round_n = ORDINALS[match.group("ord").lower()]
        slot = (match.group("slot") or "").lower() or None
    else:
        sid = str(player.get("sleeperId") or player.get("sleeper_id") or "")
        slotted = _FC_PICK_ID_SLOT.match(sid)
        plain = _FC_PICK_ID_PLAIN.match(sid)
        if slotted:
            season = slotted.group("season")
            round_n = int(slotted.group("round0")) + 1
            slot = slotted.group("slot").lower()
        elif plain:
            season = plain.group("season")
            round_n = int(plain.group("round"))
            slot = None
        else:
            return None

    rank = row.get("overallRank")
    if rank is None:
        rank = row.get("overall_rank")
    try:
        overall_rank = int(rank) if rank is not None else None
    except (TypeError, ValueError):
        overall_rank = None
    return {
        "season": str(season),
        "round": int(round_n),
        "slot": slot,
        "value": value,
        "name": name or None,
        "overall_rank": overall_rank,
        "sleeper_id": player.get("sleeperId") or player.get("sleeper_id"),
    }


def lookup_fc_pick(
    fc_rows: list[dict] | None,
    *,
    season: str,
    round_n: int,
    slot: str,
) -> dict[str, Any] | None:
    """FantasyCalc's own price for this pick, or None if the board lacks it.

    Exact early/mid/late wins. If FantasyCalc publishes only one price for
    that season and round, that row is returned with ``slot_matched`` False.
    A different year or round is never substituted.
    """
    if not fc_rows or not season or round_n < 1:
        return None
    want_slot = slot if slot in SLOT_MULT else "mid"
    exact = None
    generic = None
    for row in fc_rows:
        ident = _fc_pick_identity(row)
        if not ident:
            continue
        if ident["season"] != str(season) or ident["round"] != int(round_n):
            continue
        if ident["slot"] == want_slot:
            exact = ident
            break
        if ident["slot"] is None and generic is None:
            generic = ident
    chosen = exact or generic
    if not chosen:
        return None
    return {
        "value": chosen["value"],
        "name": chosen["name"],
        "overall_rank": chosen["overall_rank"],
        "sleeper_id": chosen["sleeper_id"],
        "slot_matched": exact is not None,
    }


def schedule_pick_value(
    *,
    round_n: int,
    slot: str = "mid",
    num_qbs: int = 2,
    num_teams: int = 12,
    fc_rows: list[dict] | None = None,
    season: str | None = None,
) -> tuple[int | None, str]:
    """Return (value, source).

    Source is ``fantasycalc_pick`` when the board has that pick, else
    ``static_schedule``. Value is None only for a round we refuse to invent
    (round < 1). ``num_teams`` is accepted for callers that already pass the
    league size; a FantasyCalc quote is not rescaled by it, because the board
    was already fetched for that format.
    """
    del num_teams  # league size is already baked into the FantasyCalc board
    slot = slot if slot in SLOT_MULT else "mid"
    if season:
        quote = lookup_fc_pick(fc_rows, season=str(season), round_n=round_n, slot=slot)
        if quote is not None:
            return quote["value"], "fantasycalc_pick"
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

    season = parsed.get("season")
    value, source = schedule_pick_value(
        round_n=int(parsed["round"]),
        slot=slot,
        num_qbs=num_qbs,
        num_teams=num_teams,
        fc_rows=fc_rows,
        season=str(season) if season else None,
    )
    row = {
        "token": token,
        "normalized": {"season": season, "round": parsed.get("round")},
        "origin": parsed.get("origin") or {},
        "value": value,
        "slot_assumption": slot,
        "source": source,
    }
    if source == "fantasycalc_pick" and season:
        quote = lookup_fc_pick(
            fc_rows,
            season=str(season),
            round_n=int(parsed["round"]),
            slot=slot,
        )
        if quote:
            row["fantasycalc_name"] = quote["name"]
            row["overall_rank"] = quote["overall_rank"]
            row["slot_matched"] = quote["slot_matched"]
    return row

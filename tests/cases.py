"""
Shared definitions for the golden-output regression harness.

Why this exists
---------------
server.py is ~2,100 lines in one file, and the plan is to pull the guts out
into a `sleeper_core` package. That is a lot of moving code with 46 internal
helper call sites and no test coverage. The golden harness gives you one thing:
proof that the refactor did not change any tool's output.

It works because every tool in server.py is a plain synchronous function that
returns a plain dict or list — no MCP machinery is needed to call them.

Workflow
--------
1. Before touching anything:      python tests/capture_golden.py
2. Commit tests/golden.json.
3. After each extraction commit:  pytest tests/test_golden.py
4. Empty diff means you are safe to continue.

Two comparison modes
--------------------
STRICT  Deep equality after stripping known-volatile fields. Used for tools
        whose output is fixed once the week/season is pinned.

SHAPE   Structural equality only: key names and value types, not values. Used
        for tools whose numbers legitimately drift between runs (projections
        get revised, trade values move daily, ADP shifts). A refactor bug shows
        up as a missing key or a changed type, which SHAPE still catches.

Pinning
-------
Never let a tool decide "current week" or "current season" for itself. The
answer changes when the NFL clock rolls over and you get a diff that has
nothing to do with your refactor. Every case below passes explicit values.
Override via environment variable if the defaults do not suit your league.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent / "golden.json"

# --------------------------------------------------------------------------
# Pinned inputs
# --------------------------------------------------------------------------
# SEASON defaults to a completed season so the nflverse-backed tools
# (stats, snap counts, depth charts, offense crowding) read frozen data.
# A completed season never changes, which makes those cases genuinely stable.
SEASON = os.environ.get("GOLDEN_SEASON", "2025")
WEEK = int(os.environ.get("GOLDEN_WEEK", "10"))
TEAM = os.environ.get("GOLDEN_TEAM", "DET")

PLAYER_A = os.environ.get("GOLDEN_PLAYER_A", "Puka Nacua")
PLAYER_B = os.environ.get("GOLDEN_PLAYER_B", "Bijan Robinson")

# analyze_trade needs both sides. Any two real, rostered names will do.
TRADE_GIVE = json.loads(os.environ.get("GOLDEN_TRADE_GIVE", '["Bijan Robinson"]'))
TRADE_GET = json.loads(os.environ.get("GOLDEN_TRADE_GET", '["Puka Nacua"]'))

STRICT = "strict"
SHAPE = "shape"

# --------------------------------------------------------------------------
# Volatile fields
# --------------------------------------------------------------------------
# Stripped before STRICT comparison. These change on Sleeper's side without any
# code change on yours, so comparing them produces noise, not signal.
VOLATILE_KEYS = frozenset(
    {
        "injury_status",
        "injury_body_part",
        "injury_notes",
        "injury_start_date",
        "practice_participation",
        "practice_description",
        "news_updated",
        "last_updated",
        "updated_at",
        "generated_at",
        "as_of",
        "timestamp",
        "search_rank",
        "search_rank_ppr",
    }
)


def strip_volatile(value):
    """Recursively drop keys whose values drift for reasons unrelated to code."""
    if isinstance(value, dict):
        return {
            k: strip_volatile(v)
            for k, v in value.items()
            if k not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [strip_volatile(v) for v in value]
    return value


def shape(value):
    """Reduce a value to its structure: key names and value types only.

    Lists collapse to a single merged element so that a list of 50 players
    becomes one description of what a player looks like. int and float both
    report as "number" — 0 vs 0.0 is not a meaningful regression.
    """
    if isinstance(value, dict):
        return {k: shape(value[k]) for k in sorted(value)}

    if isinstance(value, list):
        if not value:
            return ["<empty>"]
        merged: dict = {}
        scalars: set[str] = set()
        for item in value:
            s = shape(item)
            if isinstance(s, dict):
                for k, t in s.items():
                    # A key that is null in one row and typed in another
                    # should report the concrete type.
                    if merged.get(k) in (None, "null"):
                        merged[k] = t
            else:
                scalars.add(json.dumps(s, sort_keys=True))
        if merged:
            return [merged]
        return [sorted(scalars)]

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def normalize(value, mode: str):
    return shape(value) if mode == SHAPE else strip_volatile(value)


# --------------------------------------------------------------------------
# Fixtures that cannot be hardcoded
# --------------------------------------------------------------------------


def resolve_fixtures() -> dict:
    """Look up IDs that vary by league so cases do not hardcode them.

    Captured into golden.json so the comparison run reuses the exact same IDs
    rather than re-resolving and possibly picking a different draft.
    """
    fixtures: dict = {}

    try:
        drafts = server.get_drafts() or []
        fixtures["draft_id"] = drafts[0].get("draft_id") if drafts else None
    except Exception as exc:  # noqa: BLE001 - capture, never abort
        fixtures["draft_id"] = None
        fixtures["draft_id_error"] = f"{type(exc).__name__}: {exc}"

    try:
        hits = server.search_player(PLAYER_A, limit=1) or []
        fixtures["player_id"] = hits[0].get("player_id") if hits else None
    except Exception as exc:  # noqa: BLE001
        fixtures["player_id"] = None
        fixtures["player_id_error"] = f"{type(exc).__name__}: {exc}"

    # The previous season's league is the single most valuable fixture here.
    #
    # The current league is in `pre_draft`, so its rosters are empty, its
    # standings are all zeros and its transaction log is bare. Cases against it
    # confirm very little: the player-enrichment helpers (_player_name,
    # _enrich_players, _format_roster_entry) barely execute, and those are
    # exactly the functions moving into players.py and league.py.
    #
    # A completed season has full rosters, a real draft, and a real transaction
    # history — and it is frozen, so it makes a more stable baseline than the
    # live league, which will change the moment you draft.
    try:
        league = server.get_league() or {}
        prev = league.get("previous_league_id")
        fixtures["prev_league_id"] = prev
        fixtures["league_status"] = league.get("status")
        fixtures["league_season"] = league.get("season")
    except Exception as exc:  # noqa: BLE001
        fixtures["prev_league_id"] = None
        fixtures["prev_league_error"] = f"{type(exc).__name__}: {exc}"

    prev = fixtures.get("prev_league_id")
    if prev:
        try:
            prev_drafts = server.get_drafts(league_id=prev) or []
            fixtures["prev_draft_id"] = (
                prev_drafts[0].get("draft_id") if prev_drafts else None
            )
        except Exception as exc:  # noqa: BLE001
            fixtures["prev_draft_id"] = None
            fixtures["prev_draft_error"] = f"{type(exc).__name__}: {exc}"

    return fixtures


# --------------------------------------------------------------------------
# The cases
# --------------------------------------------------------------------------


def build_cases(fixtures: dict) -> list[tuple[str, str, dict, str]]:
    """Return (case_id, tool_name, kwargs, mode) for all 36 tools.

    case_id is what pytest reports on failure, so it is kept readable.
    """
    draft_id = fixtures.get("draft_id")
    player_id = fixtures.get("player_id")

    cases: list[tuple[str, str, dict, str]] = [
        # -- league overview -------------------------------------------------
        # get_nfl_state reports the live week, so only its shape is stable.
        ("nfl_state", "get_nfl_state", {}, SHAPE),
        ("league", "get_league", {}, STRICT),
        ("league_full", "get_league_full", {}, STRICT),
        ("managers", "get_managers", {}, STRICT),
        ("rosters", "get_rosters", {}, STRICT),
        ("rosters_no_players", "get_rosters", {"include_players": False}, STRICT),
        ("standings", "get_standings", {}, STRICT),
        # -- identity --------------------------------------------------------
        ("my_team", "get_my_team", {}, STRICT),
        ("my_roster_id", "get_my_roster_id", {}, STRICT),
        (
            "scout_team",
            "scout_team",
            {"team_name_or_manager": server.DEFAULT_TEAM_NAME},
            STRICT,
        ),
        # -- weekly ----------------------------------------------------------
        ("matchups", "get_matchups", {"week": WEEK}, STRICT),
        ("transactions", "get_transactions", {"week": WEEK}, STRICT),
        ("recent_moves", "recent_moves", {"weeks": 3}, STRICT),
        # -- picks and drafts ------------------------------------------------
        ("traded_picks", "get_traded_picks", {}, STRICT),
        ("playoff_bracket_w", "get_playoff_bracket", {"bracket": "winners"}, STRICT),
        ("playoff_bracket_l", "get_playoff_bracket", {"bracket": "losers"}, STRICT),
        ("drafts", "get_drafts", {}, STRICT),
        # -- players ---------------------------------------------------------
        # Availability depends on trending data, so compare shape only.
        ("available_players", "get_available_players", {"limit": 25}, SHAPE),
        (
            "available_rb",
            "get_available_players",
            {"position": "RB", "limit": 10},
            SHAPE,
        ),
        ("search_player", "search_player", {"name": PLAYER_A, "limit": 5}, STRICT),
        ("trending_add", "get_trending_players", {"kind": "add", "limit": 10}, SHAPE),
        ("trending_drop", "get_trending_players", {"kind": "drop", "limit": 10}, SHAPE),
        ("user", "get_user", {"username_or_id": server.DEFAULT_USERNAME}, STRICT),
        # -- projections and start/sit ---------------------------------------
        # Sleeper revises projections continuously, even for a pinned week.
        ("projections", "get_projections", {"week": WEEK, "limit": 25}, SHAPE),
        (
            "projections_wr",
            "get_projections",
            {"week": WEEK, "position": "WR", "limit": 10},
            SHAPE,
        ),
        ("start_sit", "start_sit_advice", {"week": WEEK}, SHAPE),
        # -- trade values (FantasyCalc moves daily) --------------------------
        ("trade_values", "get_trade_values", {"limit": 25}, SHAPE),
        ("trade_values_te", "get_trade_values", {"position": "TE", "limit": 10}, SHAPE),
        ("value_my_roster", "value_my_roster", {}, SHAPE),
        ("analyze_trade", "analyze_trade", {"give": TRADE_GIVE, "get": TRADE_GET}, SHAPE),
        ("adp", "get_adp", {"limit": 25}, SHAPE),
        ("dynasty_tiers", "get_dynasty_tiers", {"position": "RB"}, SHAPE),
        # -- nflverse (completed season, so genuinely stable) ----------------
        (
            "player_stats",
            "get_player_stats",
            {"player_name": PLAYER_A, "season": SEASON, "last_n_weeks": 8},
            STRICT,
        ),
        (
            "snap_counts",
            "get_snap_counts",
            {"player_name": PLAYER_A, "season": SEASON, "last_n_weeks": 8},
            STRICT,
        ),
        (
            "depth_chart",
            "get_depth_chart",
            {"team": TEAM, "season": SEASON},
            STRICT,
        ),
        (
            "depth_chart_wr",
            "get_depth_chart",
            {"team": TEAM, "position": "WR", "season": SEASON},
            STRICT,
        ),
        # Injury feeds update independently of your code.
        ("injuries", "get_injuries", {"team": TEAM, "season": SEASON}, SHAPE),
        (
            "offense_crowding",
            "get_team_offense_crowding",
            {"team": TEAM, "season": SEASON},
            STRICT,
        ),
        # score_player blends FantasyCalc + nflverse, so values drift.
        ("score_player_a", "score_player", {"player_name": PLAYER_A}, SHAPE),
        ("score_player_b", "score_player", {"player_name": PLAYER_B}, SHAPE),
    ]

    # These three need a draft_id that only exists once the league has drafted.
    if draft_id:
        cases += [
            ("draft", "get_draft", {"draft_id": draft_id}, STRICT),
            ("draft_picks", "get_draft_picks", {"draft_id": draft_id}, STRICT),
            (
                "draft_traded_picks",
                "get_draft_traded_picks",
                {"draft_id": draft_id},
                STRICT,
            ),
        ]

    if player_id:
        cases.append(("player", "get_player", {"player_id": player_id}, STRICT))

    # -- previous season: a completed, frozen league ------------------------
    # This is where the roster/enrichment/transaction code paths actually get
    # exercised with real data. Everything here is STRICT: a finished season
    # does not change, so any diff is your refactor, not the NFL.
    prev = fixtures.get("prev_league_id")
    if prev:
        L = {"league_id": prev}
        cases += [
            ("prev_league", "get_league", L, STRICT),
            ("prev_managers", "get_managers", L, STRICT),
            ("prev_rosters", "get_rosters", L, STRICT),
            ("prev_standings", "get_standings", L, STRICT),
            ("prev_my_team", "get_my_team", L, STRICT),
            ("prev_my_roster_id", "get_my_roster_id", L, STRICT),
            # Match on manager name, not team name: team names get changed
            # between seasons, manager handles do not.
            (
                "prev_scout_team",
                "scout_team",
                {"team_name_or_manager": server.DEFAULT_USERNAME, **L},
                STRICT,
            ),
            ("prev_matchups", "get_matchups", {"week": WEEK, **L}, STRICT),
            ("prev_transactions", "get_transactions", {"week": WEEK, **L}, STRICT),
            ("prev_recent_moves", "recent_moves", {"weeks": 3, **L}, STRICT),
            ("prev_traded_picks", "get_traded_picks", L, STRICT),
            (
                "prev_playoff_bracket",
                "get_playoff_bracket",
                {"bracket": "winners", **L},
                STRICT,
            ),
            ("prev_drafts", "get_drafts", L, STRICT),
            # Roster-dependent, so a real roster makes this meaningful.
            ("prev_available", "get_available_players", {"limit": 15, **L}, SHAPE),
            ("prev_value_roster", "value_my_roster", L, SHAPE),
        ]

        prev_draft_id = fixtures.get("prev_draft_id")
        if prev_draft_id:
            D = {"draft_id": prev_draft_id}
            cases += [
                ("prev_draft", "get_draft", D, STRICT),
                # The one case that exercises pick enrichment against a real,
                # completed draft rather than an empty pick list.
                ("prev_draft_picks", "get_draft_picks", D, STRICT),
                ("prev_draft_traded", "get_draft_traded_picks", D, STRICT),
            ]

    return cases


def run_case(tool_name: str, kwargs: dict):
    """Call a tool directly. Errors are captured, not raised.

    One dead upstream endpoint should not abort a 40-case capture, and an error
    string is itself a comparable output: if a tool errored before the refactor
    and errors identically after, nothing regressed.
    """
    try:
        return getattr(server, tool_name)(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"__error__": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------
# Readable diffs
# --------------------------------------------------------------------------


def find_diffs(expected, actual, path: str = "", out: list | None = None, limit: int = 25):
    """Collect human-readable descriptions of where two structures differ."""
    if out is None:
        out = []
    if len(out) >= limit:
        return out

    if type(expected) is not type(actual):
        out.append(f"{path or '<root>'}: type {type(expected).__name__} -> {type(actual).__name__}")
        return out

    if isinstance(expected, dict):
        for key in expected.keys() - actual.keys():
            out.append(f"{path}.{key}: key removed")
        for key in actual.keys() - expected.keys():
            out.append(f"{path}.{key}: key added")
        for key in expected.keys() & actual.keys():
            find_diffs(expected[key], actual[key], f"{path}.{key}", out, limit)
        return out

    if isinstance(expected, list):
        if len(expected) != len(actual):
            out.append(f"{path}: length {len(expected)} -> {len(actual)}")
        for i, (e, a) in enumerate(zip(expected, actual)):
            find_diffs(e, a, f"{path}[{i}]", out, limit)
        return out

    if expected != actual:
        out.append(f"{path or '<root>'}: {expected!r} -> {actual!r}")
    return out

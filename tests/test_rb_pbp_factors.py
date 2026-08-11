"""
RED/GREEN coverage for TDD-001: RB play-by-play ceiling factors.

First test file for build_benchmarks.py / build_factors.py internals — the
existing golden harness (test_golden.py) only covers server.py's @mcp.tool()
surface, never the build scripts underneath it. These are unit tests against
synthetic play-by-play rows; no network required. Real-data verification
(name-join rate, actual benchmark values) is explicitly out of scope for
this sandbox — see docs/tdd/TDD-001-rb-pbp-ceiling-factors.md.
"""

from __future__ import annotations

from unittest.mock import patch

import build_benchmarks as bb


def _row(**kw) -> dict:
    """A play-by-play row with sane defaults, overridden per test."""
    base = {
        "season_type": "REG", "week": "1", "posteam": "KC",
        "play_type": "run", "rusher_player_name": "", "passer_player_name": "",
        "receiver_player_name": "", "yardline_100": "50", "goal_to_go": "0",
        "air_yards": "", "score_differential": "0", "qtr": "2",
        "game_seconds_remaining": "1000",
    }
    base.update(kw)
    return base


def test_empty_fetch_is_best_effort() -> None:
    """No pbp available (network blocked, file not published) must not raise
    and must not fabricate zeros — same convention as load_qb_pbp_season."""
    with patch.object(bb, "nflverse_csv", return_value=[]):
        rb_stats, team_rz, team_gl, team_neutral = bb.load_rb_pbp_season(2025)
    assert rb_stats == {}
    assert team_rz == {}
    assert team_gl == {}
    assert team_neutral == {}


def test_rb_rush_inside_red_zone_counts_toward_own_and_team_total() -> None:
    rows = [
        _row(rusher_player_name="J.Williams", posteam="KC", yardline_100="15"),
        _row(rusher_player_name="J.Williams", posteam="KC", yardline_100="60"),  # outside RZ
    ]
    with patch.object(bb, "nflverse_csv", return_value=rows):
        rb_stats, team_rz, _, _ = bb.load_rb_pbp_season(2025)
    key = bb._qb_name_key("J.Williams")
    assert rb_stats[key]["rz_touches"] == 1
    assert team_rz["KC"] == 1


def test_target_inside_red_zone_counts_as_a_touch() -> None:
    rows = [_row(play_type="pass", receiver_player_name="J.Williams",
                 posteam="KC", yardline_100="10")]
    with patch.object(bb, "nflverse_csv", return_value=rows):
        rb_stats, team_rz, _, _ = bb.load_rb_pbp_season(2025)
    key = bb._qb_name_key("J.Williams")
    assert rb_stats[key]["rz_touches"] == 1
    assert team_rz["KC"] == 1


def test_goal_to_go_drives_gl_carry_share_not_a_yardline_cutoff() -> None:
    """A carry at yardline_100=8 with goal_to_go=0 must NOT count as a
    goal-line carry, and one at yardline_100=22 with goal_to_go=1 MUST —
    proves the implementation uses the source's own flag, not an invented
    threshold."""
    rows = [
        _row(rusher_player_name="J.Williams", posteam="KC",
             yardline_100="8", goal_to_go="0"),
        _row(rusher_player_name="J.Williams", posteam="KC",
             yardline_100="22", goal_to_go="1"),
    ]
    with patch.object(bb, "nflverse_csv", return_value=rows):
        rb_stats, _, team_gl, _ = bb.load_rb_pbp_season(2025)
    key = bb._qb_name_key("J.Williams")
    assert rb_stats[key]["gl_carries"] == 1
    assert team_gl["KC"] == 1


def test_qb_carries_count_in_team_goal_line_denominator() -> None:
    """Resolved decision: QB sneaks are real competition for the touch, so
    they must inflate the team denominator even though a QB is never looked
    up as an RB later."""
    rows = [
        _row(rusher_player_name="P.Mahomes", posteam="KC",
             passer_player_name="P.Mahomes",  # marks him a passer this game
             yardline_100="1", goal_to_go="1"),
        _row(rusher_player_name="J.Williams", posteam="KC",
             yardline_100="2", goal_to_go="1"),
    ]
    with patch.object(bb, "nflverse_csv", return_value=rows):
        rb_stats, _, team_gl, _ = bb.load_rb_pbp_season(2025)
    assert team_gl["KC"] == 2
    rb_key = bb._qb_name_key("J.Williams")
    assert rb_stats[rb_key]["gl_carries"] == 1


def test_neutral_run_rate_is_team_level_not_player_level() -> None:
    neutral = {"score_differential": "3", "qtr": "2", "game_seconds_remaining": "1000"}
    garbage = {"score_differential": "24", "qtr": "4", "game_seconds_remaining": "100"}
    rows = [
        _row(rusher_player_name="J.Williams", posteam="KC", **neutral),
        _row(play_type="pass", posteam="KC", **neutral),
        _row(rusher_player_name="J.Williams", posteam="KC", **garbage),  # excluded
    ]
    with patch.object(bb, "nflverse_csv", return_value=rows):
        _, _, _, team_neutral = bb.load_rb_pbp_season(2025)
    assert team_neutral["KC"]["runs"] == 1
    assert team_neutral["KC"]["plays"] == 2


def test_rb_only_factors_computable() -> None:
    assert {"rz_touch_share", "gl_carry_share", "neutral_run_rate"} <= bb.COMPUTABLE


def test_rb_pbp_factors_are_rate_kind_not_divided_by_games() -> None:
    """These are pre-computed ratios, not counts -- per_game() must pass them
    through unchanged, the same convention snap_share already uses."""
    for fid in ("rz_touch_share", "gl_carry_share", "neutral_run_rate"):
        assert bb.FACTOR_KIND.get(fid) == "rate", fid

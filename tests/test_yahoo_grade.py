from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import pytest

from yahoo_core import grade


def _team(i: int) -> dict:
    # Team i owns one starter worth i * 1000, so team 1 is last in value.
    return {
        "roster_id": str(i),
        "team_key": f"461.l.1000.t.{i}",
        "owner": f"Team {i}",
        "manager": f"m{i}",
        "starters": [{"name": f"P{i}", "position": "WR", "sleeper_id": str(i)}],
        "bench": [],
    }


def _grade(is_dynasty: bool = False, **kwargs) -> dict:
    rosters = [_team(i) for i in range(1, 13)]
    fc = [{"player": {"sleeperId": str(i)}, "value": i * 1000} for i in range(1, 13)]
    fmt = {"isDynasty": is_dynasty, "numQbs": 1, "ppr": 1, "numTeams": 12}
    with ExitStack() as stack:
        for target, value in (
            ("yahoo_core.grade.resolve_league_key", "461.l.1000"),
            ("yahoo_core.grade.compute_league", {"season": "2026"}),
            ("yahoo_core.grade.compute_my_team", rosters[0]),
            ("yahoo_core.grade.league_format", fmt),
            ("yahoo_core.grade.sleeper_values.fc_values", fc),
            ("yahoo_core.grade.compute_rosters", rosters),
            ("yahoo_core.grade.sleeper_get_json", {"season": "2026", "week": 8}),
        ):
            stack.enter_context(patch(target, return_value=value))
        return grade.grade_team(**kwargs)


@pytest.mark.parametrize("horizon", ["dynasty", "win_now"])
def test_bottom_team_is_never_tank_without_pick_data(horizon):
    # "tank" requires pick capital above the league median. Yahoo has no pick
    # data, and 0 >= 0 used to label every bottom-two team a tank.
    result = _grade(horizon=horizon)
    assert result["league_value_rank"] == 12
    assert result["classification"] == "rebuilder"


def test_default_horizon_follows_league_format():
    redraft = _grade(is_dynasty=False)
    dynasty = _grade(is_dynasty=True)
    assert (redraft["horizon"], redraft["horizon_source"]) == ("win_now", "league_format")
    assert (dynasty["horizon"], dynasty["horizon_source"]) == ("dynasty", "league_format")


def test_explicit_horizon_is_kept():
    result = _grade(is_dynasty=False, horizon="dynasty")
    assert (result["horizon"], result["horizon_source"]) == ("dynasty", "explicit")


def test_unknown_horizon_is_an_error_not_a_silent_dynasty():
    result = _grade(horizon="redraft")
    assert result["error"] == "unknown horizon"
    assert result["valid_horizons"] == ["dynasty", "win_now"]


@pytest.mark.parametrize("platform", ["sleeper", "yahoo"])
def test_server_rejects_unknown_horizon_before_routing(platform):
    import server

    with patch("server._grade.grade_team") as sleeper_fn, patch(
        "server._yahoo_grade.grade_team"
    ) as yahoo_fn:
        result = server.grade_team(platform=platform, horizon="redraft")
    assert result["error"] == "unknown horizon"
    sleeper_fn.assert_not_called()
    yahoo_fn.assert_not_called()


def test_server_omitted_horizon_keeps_sleeper_default():
    import server

    with patch("server._grade.grade_team", return_value={"horizon": "dynasty"}) as fn:
        server.grade_team(platform="sleeper")
    # None reaches sleeper_core.grade, which still treats it as dynasty.
    assert fn.call_args.kwargs["horizon"] is None

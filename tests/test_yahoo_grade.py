from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import pytest

from yahoo_core import grade

# Every team's players play for "AVG" teams with a flat, average schedule unless
# a test says otherwise, so schedule only matters where a test puts it.
ALLOWED = {
    "by_defense": {
        "SOFT": {pos: {"games": 7, "per_game": 30.0} for pos in ("QB", "RB", "WR", "TE")},
        "HARD": {pos: {"games": 7, "per_game": 7.5} for pos in ("QB", "RB", "WR", "TE")},
    },
    "league_avg": {"QB": 15.0, "RB": 15.0, "WR": 15.0, "TE": 15.0},
    "through_week": 7,
}
OPPONENTS = {
    "AVG": ["X", "X", "X", "X"],
    "EASY": ["SOFT", "SOFT", "SOFT", "SOFT"],
    "TOUGH": ["HARD", "HARD", "HARD", "HARD"],
    "BYE": ["X", None, "X", "X"],
}


def _player(sid, value_name, position="WR", slot="WR", team="AVG", status=None):
    return {"name": value_name, "position": position, "sleeper_id": sid, "team": team,
            "selected_position": slot, "status": status}


def _team(i: int, starters=None, bench=None, wins=None, points_for=None) -> dict:
    # Default: team i owns one WR worth i * 1000, so team 1 is last in value,
    # and its record and points line up with that order.
    return {
        "roster_id": str(i),
        "team_key": f"461.l.1000.t.{i}",
        "owner": f"Team {i}",
        "manager": f"m{i}",
        "wins": i // 2 if wins is None else wins,
        "losses": 7 - (i // 2 if wins is None else wins),
        "ties": 0,
        "points_for": float(600 + 10 * i if points_for is None else points_for),
        "starters": starters if starters is not None else [_player(str(i), f"P{i}")],
        "bench": bench or [],
    }


def _grade(rosters=None, values=None, is_dynasty=False, target=0, **kwargs) -> dict:
    rosters = rosters or [_team(i) for i in range(1, 13)]
    values = values or {str(i): i * 1000 for i in range(1, 13)}
    fc = [{"player": {"sleeperId": sid}, "value": v} for sid, v in values.items()]
    fmt = {"isDynasty": is_dynasty, "numQbs": 1, "ppr": 1, "numTeams": 12}
    league = {
        "season": "2026", "current_week": 8, "start_week": 1, "end_week": 11,
        "reception_points": 1.0, "roster_positions": ["WR", "BN", "IR"],
        "settings": {"playoff_start_week": 15, "num_playoff_teams": 6},
    }
    with ExitStack() as stack:
        for target_name, value in (
            ("yahoo_core.grade.resolve_league_key", "461.l.1000"),
            ("yahoo_core.grade.compute_league", league),
            ("yahoo_core.grade.compute_my_team", rosters[target]),
            ("yahoo_core.grade.league_format", fmt),
            ("yahoo_core.grade.sleeper_values.fc_values", fc),
            ("yahoo_core.grade.compute_rosters", rosters),
            ("yahoo_core.grade.sleeper_get_json", {"season": "2026", "week": 8}),
            ("yahoo_core.grade.sleeper_sos.points_allowed", ALLOWED),
            ("yahoo_core.grade.sleeper_sos.remaining_opponents", OPPONENTS),
        ):
            stack.enter_context(patch(target_name, return_value=value))
        return grade.grade_team(**kwargs)


# --- dynasty-style model (explicit dynasty, or a dynasty league) -----------

@pytest.mark.parametrize("is_dynasty,horizon", [(False, "dynasty"), (True, "win_now")])
def test_bottom_team_is_never_tank_without_pick_data(is_dynasty, horizon):
    # "tank" requires pick capital above the league median. Yahoo has no pick
    # data, and 0 >= 0 used to label every bottom-two team a tank.
    result = _grade(is_dynasty=is_dynasty, horizon=horizon)
    assert result["league_value_rank"] == 12
    assert result["classification"] == "rebuilder"


# --- horizon handling ---------------------------------------------------------

def test_default_horizon_follows_league_format():
    redraft = _grade(is_dynasty=False)
    dynasty = _grade(is_dynasty=True)
    assert (redraft["horizon"], redraft["horizon_source"], redraft["model"]) == (
        "win_now", "league_format", "redraft")
    assert (dynasty["horizon"], dynasty["horizon_source"]) == ("dynasty", "league_format")
    assert "model" not in dynasty


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


# --- redraft model --------------------------------------------------------------

@pytest.mark.parametrize("rank,label", [(1, "contender"), (4, "contender"), (5, "bubble"),
                                        (8, "bubble"), (9, "long_shot"), (12, "long_shot")])
def test_classify_redraft_bands_around_playoff_line(rank, label):
    assert grade.classify_redraft(rank, 12, 6) == label


def test_redraft_blend_weight_and_composite():
    result = _grade()  # team 1: last in value, record and points
    red = result["redraft"]
    assert red["performance_weight"] == pytest.approx(7 / 14)
    assert (red["best_lineup_rank"], red["points_for_rank"], red["record_rank"]) == (12, 12, 12)
    assert red["composite"] == 0.0
    assert (result["overall_rank"], result["classification"]) == (12, "long_shot")


def test_redraft_counts_benched_stud_and_skips_injured_reserve():
    rosters = [_team(i) for i in range(1, 13)]
    rosters[0] = _team(
        1,
        starters=[_player("weak", "Weak Starter")],
        bench=[_player("stud", "Benched Stud", slot="BN"),
               _player("hurt", "IR Star", slot="BN", status="IR")],
    )
    values = {str(i): i * 1000 for i in range(2, 13)}
    values.update({"weak": 100, "stud": 20000, "hurt": 50000})
    result = _grade(rosters=rosters, values=values)
    assert [p["name"] for p in result["best_lineup"]] == ["Benched Stud"]
    assert result["redraft"]["best_lineup_rank"] == 1
    # The IR player still counts toward roster value.
    assert result["schedule_adjusted_value"] == 70100


def test_redraft_schedule_and_byes_scale_values():
    rosters = [_team(i) for i in range(1, 13)]
    rosters[0] = _team(1, starters=[_player("e", "Easy", team="EASY")])
    rosters[1] = _team(2, starters=[_player("t", "Tough", team="TOUGH")])
    rosters[2] = _team(3, starters=[_player("b", "Bye", team="BYE")])
    values = {str(i): i * 1000 for i in range(4, 13)}
    values.update({"e": 1000, "t": 1000, "b": 1000})
    easy = _grade(rosters=rosters, values=values, target=0)["best_lineup"][0]
    tough = _grade(rosters=rosters, values=values, target=1)["best_lineup"][0]
    bye = _grade(rosters=rosters, values=values, target=2)["best_lineup"][0]
    # SOFT allows 2x average, shrunk halfway -> 1.5; HARD allows 0.5x -> 0.75.
    assert (easy["schedule_factor"], easy["adj_value"]) == (1.5, 1500)
    assert (tough["schedule_factor"], tough["adj_value"]) == (0.75, 750)
    # Average opponents but one bye in four weeks: same factor, 3/4 the value.
    assert (bye["schedule_factor"], bye["byes_left"], bye["adj_value"]) == (1.0, 1, 750)

from __future__ import annotations

from unittest.mock import patch

import pytest

from sleeper_core import sos


def _row(week, defense, position, std, rec):
    return {"week": str(week), "opponent_team": defense, "position": position,
            "fantasy_points": str(std), "receptions": str(rec), "season_type": "REG"}


def _allowed(rows, reception_points=1.0):
    with patch("sleeper_core.sos.nflverse_csv", side_effect=lambda *a, **k: [
        r for r in rows if k["row_filter"](r)
    ]):
        return sos.points_allowed(2026, reception_points)


def test_points_allowed_per_game_and_league_average():
    rows = [
        _row(1, "BUF", "WR", 20, 5), _row(2, "BUF", "WR", 10, 5),  # BUF: 2 games, 40 PPR pts
        _row(1, "MIA", "WR", 6, 2),                                 # MIA: 1 game, 8 PPR pts
        _row(1, "MIA", "K", 9, 0),                                  # not an SOS position
    ]
    allowed = _allowed(rows)
    assert allowed["by_defense"]["BUF"]["WR"] == {"games": 2, "per_game": 20.0}
    assert allowed["by_defense"]["MIA"]["WR"] == {"games": 1, "per_game": 8.0}
    assert allowed["league_avg"]["WR"] == pytest.approx(48 / 3)  # 48 pts over 3 team-games
    assert allowed["through_week"] == 2
    # Half PPR follows the league setting.
    assert _allowed(rows, 0.5)["by_defense"]["BUF"]["WR"]["per_game"] == 17.5


def test_points_allowed_maps_rams_alias():
    allowed = _allowed([_row(1, "LAR", "QB", 15, 0)])
    assert "LA" in allowed["by_defense"] and "LAR" not in allowed["by_defense"]


def _fixture():
    allowed = {
        "by_defense": {
            "SOFT": {"WR": {"games": 7, "per_game": 30.0}},  # twice the average
            "AVG": {"WR": {"games": 7, "per_game": 15.0}},
        },
        "league_avg": {"WR": 15.0, "QB": 15.0, "RB": 15.0, "TE": 15.0},
        "through_week": 7,
    }
    opponents = {"BUF": ["SOFT", "AVG", None, "AVG"], "LA": ["AVG", "AVG", "AVG", "AVG"]}
    return allowed, opponents


def test_matchup_factor_is_shrunk_toward_average():
    allowed, _ = _fixture()
    # Raw ratio 2.0 after 7 games moves halfway (7 / (7 + 7)) toward 1.0.
    assert sos.matchup_factor("SOFT", "WR", allowed) == pytest.approx(1.5)
    assert sos.matchup_factor("AVG", "WR", allowed) == pytest.approx(1.0)
    assert sos.matchup_factor("UNKNOWN", "WR", allowed) == 1.0


def test_schedule_factor_counts_byes_in_multiplier_only():
    allowed, opponents = _fixture()
    result = sos.schedule_factor("Buf", "WR", allowed, opponents)  # Yahoo case
    assert result["factor"] == pytest.approx((1.5 + 1.0 + 1.0) / 3, abs=1e-4)
    assert result["multiplier"] == pytest.approx((1.5 + 1.0 + 1.0) / 4, abs=1e-4)
    assert (result["games"], result["byes"]) == (3, 1)
    assert result["provenance"] == "measured:through_week_7"
    # Yahoo/Sleeper "LAR" reaches nflverse "LA".
    assert sos.schedule_factor("LAR", "WR", allowed, opponents)["factor"] == 1.0


def test_schedule_factor_gaps_are_explicit():
    allowed, opponents = _fixture()
    kicker = sos.schedule_factor("BUF", "K", allowed, opponents)
    nowhere = sos.schedule_factor("XYZ", "WR", allowed, opponents)
    assert (kicker["factor"], kicker["provenance"]) == (None, "unsourced")
    assert (nowhere["factor"], nowhere["provenance"]) == (None, "missing:no_team_schedule")

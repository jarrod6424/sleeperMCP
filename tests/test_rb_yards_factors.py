from __future__ import annotations

from unittest.mock import patch

import build_benchmarks as bb


def test_per_game_computes_yards_efficiency() -> None:
    ps = {
        "games": 17,
        "carries": 264.0,
        "receptions": 58.0,
        "rushing_yards": 1517.0,
        "receiving_yards": 462.0,
        "attempts": 0.0,
        "passing_tds": 0.0,
        "rushing_tds": 0.0,
        "targets": 74.0,
        "receiving_tds": 0.0,
        "team_wins": 15,
    }
    out = bb.per_game(ps)
    assert out["receptions"] == 58.0 / 17
    assert abs(out["yards_per_carry"] - (1517.0 / 264.0)) < 1e-6
    assert abs(out["yards_per_touch"] - ((1517.0 + 462.0) / (264.0 + 58.0))) < 1e-6
    assert out["team_wins"] == 15


def test_per_game_zero_carries_omits_ypc() -> None:
    ps = {
        "games": 10,
        "carries": 0.0,
        "receptions": 20.0,
        "rushing_yards": 0.0,
        "receiving_yards": 100.0,
        "attempts": 0.0,
        "passing_tds": 0.0,
        "rushing_tds": 0.0,
        "targets": 25.0,
        "receiving_tds": 0.0,
    }
    out = bb.per_game(ps)
    assert "yards_per_carry" not in out
    assert abs(out["yards_per_touch"] - 5.0) < 1e-6


def test_load_team_wins_season_counts_reg_wins() -> None:
    rows = [
        {
            "season": "2024",
            "game_type": "REG",
            "home_team": "DET",
            "away_team": "GB",
            "home_score": "27",
            "away_score": "20",
        },
        {
            "season": "2024",
            "game_type": "REG",
            "home_team": "GB",
            "away_team": "DET",
            "home_score": "17",
            "away_score": "24",
        },
        {
            "season": "2024",
            "game_type": "POST",
            "home_team": "DET",
            "away_team": "GB",
            "home_score": "30",
            "away_score": "10",
        },
        {
            "season": "2023",
            "game_type": "REG",
            "home_team": "DET",
            "away_team": "GB",
            "home_score": "20",
            "away_score": "10",
        },
    ]
    with patch.object(bb, "nflverse_csv", return_value=rows):
        wins = bb.load_team_wins_season(2024)
    assert wins["DET"] == 2
    assert wins.get("GB", 0) == 0


def test_load_team_wins_empty_is_best_effort() -> None:
    with patch.object(bb, "nflverse_csv", return_value=[]):
        assert bb.load_team_wins_season(2024) == {}


def test_rb_factors_include_yards_and_receptions() -> None:
    ids = [f for f, _ in bb.FACTORS["RB"]]
    assert "receptions" in ids
    assert "yards_per_carry" in ids
    assert "yards_per_touch" in ids
    assert "team_wins" in ids
    for fid in ("receptions", "yards_per_carry", "yards_per_touch", "team_wins"):
        assert fid in bb.COMPUTABLE

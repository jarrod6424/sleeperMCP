from __future__ import annotations

from unittest.mock import patch

import build_benchmarks as bb


def test_load_ngs_catch_pct_uses_week_zero() -> None:
    rows = [
        {
            "season": "2024",
            "season_type": "REG",
            "week": "0",
            "player_display_name": "Amon-Ra St. Brown",
            "player_position": "WR",
            "catch_percentage": "68.5",
        },
        {
            "season": "2024",
            "season_type": "REG",
            "week": "5",
            "player_display_name": "Amon-Ra St. Brown",
            "player_position": "WR",
            "catch_percentage": "90.0",
        },
        {
            "season": "2023",
            "season_type": "REG",
            "week": "0",
            "player_display_name": "Amon-Ra St. Brown",
            "player_position": "WR",
            "catch_percentage": "70.0",
        },
    ]
    with patch.object(bb, "nflverse_csv", return_value=rows):
        out = bb.load_ngs_catch_pct(2024)
    assert abs(out[bb._qb_name_key("Amon-Ra St. Brown")] - 68.5) < 1e-6


def test_load_ngs_normalizes_fraction_to_percent() -> None:
    rows = [
        {
            "season": "2024",
            "season_type": "REG",
            "week": "0",
            "player_display_name": "Ja'Marr Chase",
            "player_position": "WR",
            "catch_percentage": "0.72",
        },
    ]
    with patch.object(bb, "nflverse_csv", return_value=rows):
        out = bb.load_ngs_catch_pct(2024)
    assert abs(out[bb._qb_name_key("Ja'Marr Chase")] - 72.0) < 1e-6


def test_load_ngs_empty_is_best_effort() -> None:
    with patch.object(bb, "nflverse_csv", return_value=[]):
        assert bb.load_ngs_catch_pct(2024) == {}


def test_wr_reception_perception_source() -> None:
    assert dict(bb.FACTORS["WR"])["reception_perception"] == "nflverse:ngs"
    assert "reception_perception" in bb.COMPUTABLE

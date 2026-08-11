from __future__ import annotations

from unittest.mock import patch

import build_benchmarks as bb


def test_per_game_computes_wr_catch_efficiency() -> None:
    ps = {
        "games": 17,
        "carries": 0.0,
        "receptions": 100.0,
        "rushing_yards": 0.0,
        "receiving_yards": 1200.0,
        "receiving_yards_after_catch": 400.0,
        "attempts": 0.0,
        "passing_tds": 0.0,
        "rushing_tds": 0.0,
        "targets": 150.0,
        "receiving_tds": 8.0,
        "target_share": 0.28,
    }
    out = bb.per_game(ps)
    assert abs(out["yards_per_catch"] - 12.0) < 1e-6
    assert abs(out["yac_per_reception"] - 4.0) < 1e-6
    assert abs(out["target_share"] - 0.28) < 1e-6


def test_per_game_zero_receptions_omits_catch_rates() -> None:
    ps = {
        "games": 10,
        "carries": 0.0,
        "receptions": 0.0,
        "rushing_yards": 0.0,
        "receiving_yards": 0.0,
        "receiving_yards_after_catch": 0.0,
        "attempts": 0.0,
        "passing_tds": 0.0,
        "rushing_tds": 0.0,
        "targets": 5.0,
        "receiving_tds": 0.0,
    }
    out = bb.per_game(ps)
    assert "yards_per_catch" not in out
    assert "yac_per_reception" not in out


def test_wr_factors_include_volume_efficiency() -> None:
    ids = [f for f, _ in bb.FACTORS["WR"]]
    for fid in ("yards_per_catch", "yac_per_reception", "target_share"):
        assert fid in ids
        assert fid in bb.COMPUTABLE

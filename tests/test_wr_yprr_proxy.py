from __future__ import annotations

from unittest.mock import patch

import build_benchmarks as bb


def test_yprr_from_yards_and_on_pass() -> None:
    # Unit the attach math without full CSV: simulate post-attach row via per_game
    ps = {
        "games": 17,
        "carries": 0.0,
        "receptions": 80.0,
        "rushing_yards": 0.0,
        "receiving_yards": 1200.0,
        "receiving_yards_after_catch": 300.0,
        "attempts": 0.0,
        "passing_tds": 0.0,
        "rushing_tds": 0.0,
        "targets": 120.0,
        "receiving_tds": 8.0,
        "yprr": 1200.0 / 300.0,
    }
    out = bb.per_game(ps)
    assert abs(out["yprr"] - 4.0) < 1e-6


def test_compute_yprr_skips_zero_routes() -> None:
    assert bb.compute_yprr(1200.0, 0) is None
    assert bb.compute_yprr(1200.0, 300) == 4.0


def test_wr_yprr_is_computable() -> None:
    src = dict(bb.FACTORS["WR"])
    assert src["yprr"] == "nflverse:participation"
    assert "yprr" in bb.COMPUTABLE

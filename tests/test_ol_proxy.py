from __future__ import annotations

import build_benchmarks as bb


def test_pressure_rate_and_rank() -> None:
    rows = [
        # team A: 1 sack on 2 dropbacks → pressure_rate 0.5
        {"posteam": "DET", "pass_attempt": "1", "sack": "0", "qb_hit": "0", "qb_scramble": "0"},
        {"posteam": "DET", "pass_attempt": "0", "sack": "1", "qb_hit": "0", "qb_scramble": "0"},
        # team B: clean 2 dropbacks → 0.0
        {"posteam": "KC", "pass_attempt": "1", "sack": "0", "qb_hit": "0", "qb_scramble": "0"},
        {"posteam": "KC", "pass_attempt": "1", "sack": "0", "qb_hit": "0", "qb_scramble": "0"},
    ]
    rates = bb.pressure_rates_from_rows(rows)
    assert abs(rates["DET"] - 0.5) < 1e-9
    assert abs(rates["KC"] - 0.0) < 1e-9
    ranks = bb.rank_teams_ascending(rates)
    assert ranks["KC"] == 1
    assert ranks["DET"] == 2


def test_stuff_rate_and_rank() -> None:
    rows = [
        {"posteam": "PHI", "rush_attempt": "1", "rushing_yards": "-1"},
        {"posteam": "PHI", "rush_attempt": "1", "rushing_yards": "5"},
        {"posteam": "SF", "rush_attempt": "1", "rushing_yards": "4"},
        {"posteam": "SF", "rush_attempt": "1", "rushing_yards": "3"},
    ]
    rates = bb.stuff_rates_from_rows(rows)
    assert abs(rates["PHI"] - 0.5) < 1e-9
    assert abs(rates["SF"] - 0.0) < 1e-9
    ranks = bb.rank_teams_ascending(rates)
    assert ranks["SF"] == 1
    assert ranks["PHI"] == 2


def test_dropback_includes_sack_without_pass_attempt() -> None:
    rows = [
        {"posteam": "CHI", "pass_attempt": "0", "sack": "1", "qb_hit": "0", "qb_scramble": "0"},
    ]
    rates = bb.pressure_rates_from_rows(rows)
    assert abs(rates["CHI"] - 1.0) < 1e-9

from __future__ import annotations

from tools import build_benchmarks as bb


def test_pass_epa_rank_orders_higher_epa_first():
    rows = [
        {"posteam": "KC", "pass": 1, "epa": 0.4, "season_type": "REG", "play_type": "pass"},
        {"posteam": "KC", "pass": 1, "epa": 0.2, "season_type": "REG", "play_type": "pass"},
        {"posteam": "CHI", "pass": 1, "epa": -0.1, "season_type": "REG", "play_type": "pass"},
    ]
    means = bb.pass_epa_mean_from_rows(rows)
    assert means["KC"] > means["CHI"]
    ranks = bb.rank_teams_descending(means)
    assert ranks["KC"] == 1
    assert ranks["CHI"] == 2


def test_qb_factors_use_pass_epa_not_dvoa():
    ids = [fid for fid, _ in bb.FACTORS["QB"]]
    assert "pass_epa_rank" in ids
    assert "pass_dvoa_rank" not in ids
    assert dict(bb.FACTORS["QB"])["pass_epa_rank"].startswith("nflverse")

# tests/test_wr_qb_pff_proxy.py
from __future__ import annotations

import build_benchmarks as bb


def test_wr_gets_team_primary_qb_rank_as_qb_pff_rank() -> None:
    qbr = {
        bb._qb_name_key("S.Darnold"): {
            "rank": 5, "qb_plays": 500, "team": "SEA", "qbr": 60.0,
        },
    }
    agg = {
        "jsn": {"name": "Jaxon Smith-Njigba", "position": "WR", "team": "SEA"},
        "kittle": {"name": "George Kittle", "position": "TE", "team": "SF"},
    }
    bb._attach_team_qbr_ranks(agg, qbr)
    assert agg["jsn"]["qb_pff_rank"] == 5
    assert "qb_pff_rank" not in agg["kittle"]
    assert agg["kittle"].get("qb_qbr_rank") is None  # SF not in map

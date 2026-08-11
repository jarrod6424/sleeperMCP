# tests/test_qbr_factors.py
from __future__ import annotations
from unittest.mock import patch
import build_benchmarks as bb


def test_empty_qbr_fetch_is_best_effort() -> None:
    with patch.object(bb, "nflverse_csv", return_value=[]):
        assert bb.load_espn_qbr_season(2024) == {}


def test_qbr_ranks_qualified_qbs_lower_better() -> None:
    rows = [
        {"season": "2024", "name_display": "A.Allen", "qbr_total": "70.0",
         "qb_plays": "400", "team_abb": "BUF", "qualified": "True"},
        {"season": "2024", "name_display": "B.Backup", "qbr_total": "40.0",
         "qb_plays": "50", "team_abb": "BUF", "qualified": "True"},
        {"season": "2023", "name_display": "C.Other", "qbr_total": "99.0",
         "qb_plays": "400", "team_abb": "KC", "qualified": "True"},
    ]
    with patch.object(bb, "nflverse_csv", return_value=rows):
        out = bb.load_espn_qbr_season(2024)
    assert out[bb._qb_name_key("A.Allen")]["rank"] == 1
    assert out[bb._qb_name_key("B.Backup")]["rank"] == 2
    assert bb._qb_name_key("C.Other") not in out


def test_qbr_factors_computable() -> None:
    assert {"qbr_rank", "qb_qbr_rank"} <= bb.COMPUTABLE


def test_qbr_factors_are_rank_kind() -> None:
    for fid in ("qbr_rank", "qb_qbr_rank"):
        assert bb.FACTOR_KIND.get(fid) == "rank", fid


def test_injury_data_source_has_categorical_gap_note() -> None:
    assert bb._gap_note("nflverse:injuries") == (
        "categorical; sourced via nflverse injuries, not cohort-benchmarked"
    )

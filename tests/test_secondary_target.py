from __future__ import annotations

import build_benchmarks as bb


def test_secondary_bands() -> None:
    assert bb.classify_secondary_target(100, 74) == "less"
    assert bb.classify_secondary_target(100, 75) == "same"
    assert bb.classify_secondary_target(100, 99) == "same"
    assert bb.classify_secondary_target(100, 100) == "more"


def test_attach_secondary_on_team_wrs() -> None:
    rows = [
        {"name": "A.Alpha", "position": "WR", "team": "DET", "targets": 140},
        {"name": "B.Beta", "position": "WR", "team": "DET", "targets": 90},
        {"name": "C.Solo", "position": "WR", "team": "LV", "targets": 80},
    ]
    bb._attach_wr_secondary_targets(rows)
    alpha = next(r for r in rows if r["name"] == "A.Alpha")
    beta = next(r for r in rows if r["name"] == "B.Beta")
    solo = next(r for r in rows if r["name"] == "C.Solo")
    assert alpha["secondary_target"] == 90
    assert alpha["secondary_target_cat"] == "less"
    assert beta["secondary_target"] == 140
    assert beta["secondary_target_cat"] == "more"
    assert "secondary_target" not in solo

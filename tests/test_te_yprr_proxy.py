from __future__ import annotations

import build_benchmarks as bb


def test_te_factors_drop_licensed_and_use_yprr():
    ids = [fid for fid, _ in bb.FACTORS["TE"]]
    assert "yprr" in ids
    assert "yprr_rank" not in ids
    assert "inline_pct" not in ids
    assert dict(bb.FACTORS["TE"])["yprr"] == "nflverse:participation"


def test_participation_loop_sets_te_yprr(monkeypatch):
    # minimal: call compute_yprr path — assert compute_yprr(900, 300) == 3.0
    assert bb.compute_yprr(900.0, 300) == 3.0

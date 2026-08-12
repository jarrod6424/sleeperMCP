from __future__ import annotations

import build_benchmarks as bb


def test_qb_factors_exclude_adp_include_injury() -> None:
    ids = [fid for fid, _ in bb.FACTORS["QB"]]
    assert "adp" not in ids
    assert "injury_concern" in ids
    assert "ol_pass_block_rank" in ids


def test_wr_te_have_pace_and_te_has_ol_pass() -> None:
    wr = dict(bb.FACTORS["WR"])
    te = dict(bb.FACTORS["TE"])
    assert wr.get("neutral_pace_rank") == "nflverse:pbp"
    assert te.get("neutral_pace_rank") == "nflverse:pbp"
    assert te.get("ol_pass_block_rank") == "nflverse:pbp:proxy"
    assert wr.get("ol_pass_block_rank") == "nflverse:pbp:proxy"
    assert dict(bb.FACTORS["RB"]).get("ol_run_block_rank") == "nflverse:pbp:proxy"


def test_ol_and_pace_computable() -> None:
    assert "ol_pass_block_rank" in bb.COMPUTABLE
    assert "ol_run_block_rank" in bb.COMPUTABLE
    assert "neutral_pace_rank" in bb.COMPUTABLE

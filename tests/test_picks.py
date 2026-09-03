from __future__ import annotations

from sleeper_core import picks


def test_parse_common_tokens():
    a = picks.parse_pick_token("2027 1st")
    assert a["ok"] is True
    assert a["season"] == "2027"
    assert a["round"] == 1

    b = picks.parse_pick_token("2027 Round 1")
    assert b["ok"] is True
    assert b["round"] == 1

    c = picks.parse_pick_token("2027 1st from IDAHO SPUD REAPERS")
    assert c["ok"] is True
    assert c["origin"]["from_team"] == "IDAHO SPUD REAPERS"

    d = picks.parse_pick_token("2027 1st (roster 11)")
    assert d["ok"] is True
    assert d["origin"]["roster_id"] == 11

    e = picks.parse_pick_token("2026 2nd (other)")
    assert e["ok"] is True
    assert e["round"] == 2
    assert e["origin"]["other"] is True


def test_invalid_pick_does_not_crash():
    bad = picks.parse_pick_token("2027 pick please")
    assert bad["ok"] is False
    assert picks.looks_like_pick("2027 pick please") is True
    assert picks.parse_pick_token("Marvin Harrison")["ok"] is False
    assert picks.looks_like_pick("Marvin Harrison") is False
    assert picks.parse_pick_token("")["reason"] == "empty_token"


def test_static_superflex_first_higher_than_1qb():
    sf = picks.static_pick_value(1, "mid", num_qbs=2)
    one_qb = picks.static_pick_value(1, "mid", num_qbs=1)
    assert sf == 2800
    assert one_qb == 2100
    assert sf > one_qb
    assert picks.static_pick_value(2, "mid", num_qbs=2) == 1100
    assert picks.static_pick_value(1, "early", num_qbs=2) == 3360
    assert picks.static_pick_value(1, "late", num_qbs=2) == 2296


def test_slot_from_rank():
    assert picks.slot_from_rank(1, 12) == "late"
    assert picks.slot_from_rank(12, 12) == "early"
    assert picks.slot_from_rank(6, 12) == "mid"
    assert picks.slot_from_rank(None, 12) == "mid"


def test_fc_band_superflex_higher_than_1qb():
    # Superflex board: QBs cluster at the top with huge values.
    sf_board = [
        {"overallRank": i + 1, "value": 9000 - i * 100, "player": {"name": f"SF{i}"}}
        for i in range(24)
    ]
    one_qb_board = [
        {"overallRank": i + 1, "value": 5000 - i * 80, "player": {"name": f"1QB{i}"}}
        for i in range(24)
    ]
    sf_val, sf_src = picks.schedule_pick_value(
        round_n=1, slot="mid", num_qbs=2, num_teams=12, fc_rows=sf_board
    )
    one_val, one_src = picks.schedule_pick_value(
        round_n=1, slot="mid", num_qbs=1, num_teams=12, fc_rows=one_qb_board
    )
    assert sf_src == "fantasycalc_band"
    assert one_src == "fantasycalc_band"
    assert sf_val > one_val


def test_empty_fc_falls_back_to_static():
    val, src = picks.schedule_pick_value(round_n=1, slot="mid", num_qbs=2, fc_rows=[])
    assert src == "static_schedule"
    assert val == 2800

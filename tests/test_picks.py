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


def _veteran(rank: int, value: int, name: str, position: str = "WR") -> dict:
    return {
        "overallRank": rank,
        "value": value,
        "player": {"name": name, "position": position},
    }


def _pick(rank: int, value: int, name: str, sleeper_id: str) -> dict:
    return {
        "overallRank": rank,
        "value": value,
        "player": {"name": name, "position": "PICK", "sleeperId": sleeper_id},
    }


# Overall ranks 4–8 and 16–20 from the superflex dynasty board. The retired
# rank-band priced a late 1st as round(round(mean(ranks 4–8)) * 0.82) = 6858
# and a late 2nd as round(round(mean(ranks 16–20)) * 0.82) = 4950.
_WINDOW = {
    4: (9778, "Jaxon Smith-Njigba"),
    5: (8525, "Ja'Marr Chase"),
    6: (8201, "Puka Nacua"),
    7: (8031, "Brock Bowers", "TE"),
    8: (7286, "Amon-Ra St. Brown"),
    16: (6242, "Brock Purdy", "QB"),
    17: (6071, "Justin Jefferson"),
    18: (6026, "Malik Nabers"),
    19: (5960, "Patrick Mahomes", "QB"),
    20: (5886, "Ashton Jeanty", "RB"),
}


def _dense_board() -> list[dict]:
    rows = []
    for rank in range(1, 25):
        if rank in _WINDOW:
            spec = _WINDOW[rank]
            value, name = spec[0], spec[1]
            position = spec[2] if len(spec) > 2 else "WR"
            rows.append(_veteran(rank, value, name, position))
        else:
            rows.append(_veteran(rank, 10000 - rank, f"Vet {rank}"))
    rows.extend(
        [
            _pick(29, 5043, "2027 1st (Early)", "FP_2027_early_0"),
            _pick(61, 3321, "2027 1st (Mid)", "FP_2027_mid_0"),
            _pick(68, 3039, "2027 1st", "FP_2027_1"),
            _pick(86, 2560, "2027 1st (Late)", "FP_2027_late_0"),
            _pick(99, 2242, "2028 1st", "FP_2028_1"),
            _pick(116, 1707, "2027 2nd (Mid)", "FP_2027_mid_1"),
            _pick(146, 1463, "2027 2nd (Late)", "FP_2027_late_1"),
        ]
    )
    return rows


def _retired_rank_band(round_n: int, slot: str, num_teams: int = 12) -> int:
    """The old startup-slot average, so a regression back to it fails loudly."""
    ranked = sorted(_dense_board(), key=lambda row: row["overallRank"])
    values = [row["value"] for row in ranked]
    start = (round_n - 1) * num_teams + max(1, num_teams // 3)
    end = (round_n - 1) * num_teams + (num_teams - max(1, num_teams // 3))
    window = values[start - 1 : end]
    band = int(round(sum(window) / len(window)))
    return int(round(band * picks.SLOT_MULT[slot]))


def test_player_board_without_pick_rows_uses_static_table():
    board = [
        {"overallRank": i + 1, "value": 9000 - i * 100, "player": {"name": f"P{i}", "position": "WR"}}
        for i in range(24)
    ]
    sf_val, sf_src = picks.schedule_pick_value(
        round_n=1, slot="mid", season="2027", num_qbs=2, num_teams=12, fc_rows=board
    )
    one_val, one_src = picks.schedule_pick_value(
        round_n=1, slot="mid", season="2027", num_qbs=1, num_teams=12, fc_rows=board
    )
    assert sf_src == "static_schedule"
    assert one_src == "static_schedule"
    assert sf_val == 2800
    assert one_val == 2100
    assert sf_val > one_val


def test_late_picks_use_fantasycalc_quote_not_veteran_band():
    board = _dense_board()
    assert _retired_rank_band(1, "late") == 6858
    assert _retired_rank_band(2, "late") == 4950

    first, first_src = picks.schedule_pick_value(
        round_n=1, slot="late", season="2027", num_qbs=2, num_teams=12, fc_rows=board
    )
    second, second_src = picks.schedule_pick_value(
        round_n=2, slot="late", season="2027", num_qbs=2, num_teams=12, fc_rows=board
    )
    assert first_src == "fantasycalc_pick"
    assert second_src == "fantasycalc_pick"
    assert first == 2560
    assert second == 1463
    assert first != _retired_rank_band(1, "late")
    assert second != _retired_rank_band(2, "late")


def test_slot_selects_the_published_tier_without_rescaling():
    board = _dense_board()
    early, _ = picks.schedule_pick_value(
        round_n=1, slot="early", season="2027", num_qbs=2, fc_rows=board
    )
    mid, _ = picks.schedule_pick_value(
        round_n=1, slot="mid", season="2027", num_qbs=2, fc_rows=board
    )
    assert early == 5043
    assert mid == 3321
    assert early != int(round(3321 * picks.SLOT_MULT["early"]))


def test_unslotted_year_is_not_multiplied_by_slot():
    board = _dense_board()
    value, source = picks.schedule_pick_value(
        round_n=1, slot="late", season="2028", num_qbs=2, fc_rows=board
    )
    assert source == "fantasycalc_pick"
    assert value == 2242
    quote = picks.lookup_fc_pick(board, season="2028", round_n=1, slot="late")
    assert quote["slot_matched"] is False
    assert quote["name"] == "2028 1st"


def test_missing_season_falls_back_to_static():
    value, source = picks.schedule_pick_value(
        round_n=1, slot="late", season="2030", num_qbs=2, num_teams=12, fc_rows=_dense_board()
    )
    assert source == "static_schedule"
    assert value == 2296


def test_pick_row_records_the_fantasycalc_name():
    row = picks.price_parsed_pick(
        picks.parse_pick_token("2027 1st"),
        slot="late",
        num_qbs=2,
        num_teams=12,
        fc_rows=_dense_board(),
    )
    assert row["value"] == 2560
    assert row["source"] == "fantasycalc_pick"
    assert row["fantasycalc_name"] == "2027 1st (Late)"
    assert row["overall_rank"] == 86
    assert row["slot_matched"] is True
    spelled = picks.price_parsed_pick(
        picks.parse_pick_token("2027 Round 1"),
        slot="late",
        fc_rows=_dense_board(),
    )
    assert spelled["value"] == 2560
    assert spelled["fantasycalc_name"] == "2027 1st (Late)"


def test_sleeper_id_identifies_a_pick_when_the_name_does_not():
    board = [
        {
            "overallRank": 86,
            "value": 2560,
            "player": {"name": "future first", "position": "PICK", "sleeperId": "FP_2027_late_0"},
        }
    ]
    value, source = picks.schedule_pick_value(
        round_n=1, slot="late", season="2027", num_qbs=2, fc_rows=board
    )
    assert source == "fantasycalc_pick"
    assert value == 2560


def test_format_specific_pick_quote_is_passed_through():
    sf_board = [_pick(61, 3321, "2027 1st (Mid)", "FP_2027_mid_0")]
    one_board = [_pick(40, 1800, "2027 1st (Mid)", "FP_2027_mid_0")]
    sf_val, sf_src = picks.schedule_pick_value(
        round_n=1, slot="mid", season="2027", num_qbs=2, num_teams=12, fc_rows=sf_board
    )
    one_val, one_src = picks.schedule_pick_value(
        round_n=1, slot="mid", season="2027", num_qbs=1, num_teams=12, fc_rows=one_board
    )
    assert sf_src == "fantasycalc_pick"
    assert one_src == "fantasycalc_pick"
    assert sf_val == 3321
    assert one_val == 1800
    assert sf_val > one_val


def test_empty_fc_falls_back_to_static():
    val, src = picks.schedule_pick_value(round_n=1, slot="mid", num_qbs=2, fc_rows=[])
    assert src == "static_schedule"
    assert val == 2800

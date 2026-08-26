from __future__ import annotations

from unittest.mock import patch

from sleeper_core import crosswalk


SAMPLE_PLAYERS = {
    "6794": {
        "player_id": "6794",
        "full_name": "Justin Jefferson",
        "first_name": "Justin",
        "last_name": "Jefferson",
        "search_full_name": "justinjefferson",
        "position": "WR",
        "team": "MIN",
        "yahoo_id": 32692,
        "active": True,
    },
    "9493": {
        "player_id": "9493",
        "full_name": "Puka Nacua",
        "first_name": "Puka",
        "last_name": "Nacua",
        "search_full_name": "pukanacua",
        "position": "WR",
        "team": "LAR",
        "yahoo_id": None,
        "active": True,
    },
    "9999": {
        "player_id": "9999",
        "full_name": "Duplicate Name",
        "search_full_name": "duplicatename",
        "position": "RB",
        "team": "DET",
        "yahoo_id": None,
        "active": True,
    },
    "9998": {
        "player_id": "9998",
        "full_name": "Duplicate Name",
        "search_full_name": "duplicatename",
        "position": "WR",
        "team": "CHI",
        "yahoo_id": None,
        "active": True,
    },
}


def test_normalize_yahoo_id_from_key():
    assert crosswalk.normalize_yahoo_id("461.p.32692") == "32692"
    assert crosswalk.normalize_yahoo_id(32692) == "32692"
    assert crosswalk.normalize_yahoo_id(None) is None


def test_sleeper_to_yahoo():
    result = crosswalk.sleeper_to_yahoo("6794", SAMPLE_PLAYERS)
    assert result["yahoo_id"] == "32692"
    assert result["matched_by"] == "yahoo_id"


def test_sleeper_to_yahoo_missing():
    result = crosswalk.sleeper_to_yahoo("9493", SAMPLE_PLAYERS)
    assert result["yahoo_id"] is None
    assert result["matched_by"] == "missing:no_yahoo_id"


def test_yahoo_to_sleeper_by_id():
    indexes = crosswalk.build_indexes(SAMPLE_PLAYERS)
    result = crosswalk.yahoo_to_sleeper("461.p.32692", indexes=indexes)
    assert result["sleeper_id"] == "6794"
    assert result["matched_by"] == "yahoo_id"


def test_yahoo_to_sleeper_name_fallback():
    indexes = crosswalk.build_indexes(SAMPLE_PLAYERS)
    result = crosswalk.yahoo_to_sleeper(None, name="Puka Nacua", indexes=indexes)
    assert result["sleeper_id"] == "9493"
    assert result["matched_by"] == "name"


def test_yahoo_to_sleeper_name_position_disambiguates():
    indexes = crosswalk.build_indexes(SAMPLE_PLAYERS)
    result = crosswalk.yahoo_to_sleeper(
        None, name="Duplicate Name", position="WR", indexes=indexes
    )
    assert result["sleeper_id"] == "9998"
    assert result["matched_by"] == "name+position"


def test_enrich_with_sleeper_ids():
    with patch("sleeper_core.crosswalk.load_players", return_value=SAMPLE_PLAYERS):
        rows = crosswalk.enrich_with_sleeper_ids(
            [
                {
                    "player_id": "32692",
                    "player_key": "461.p.32692",
                    "name": "Justin Jefferson",
                    "position": "WR",
                }
            ]
        )
    assert rows[0]["sleeper_id"] == "6794"
    assert rows[0]["crosswalk_matched_by"] == "yahoo_id"


def test_crosswalk_stats():
    stats = crosswalk.crosswalk_stats(SAMPLE_PLAYERS)
    assert stats["players_total"] == 4
    assert stats["with_yahoo_id"] == 1


def test_server_resolve_player_crosswalk():
    import server

    with patch("server._crosswalk.sleeper_to_yahoo", return_value={"yahoo_id": "1"}) as fn:
        result = server.resolve_player_crosswalk(sleeper_id="6794")
    assert result["yahoo_id"] == "1"
    fn.assert_called_once_with("6794")

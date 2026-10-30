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


# Shape of a real Sleeper defense record: keyed by team abbreviation, no yahoo_id.
DEF_PLAYERS = {
    "CHI": {
        "player_id": "CHI",
        "first_name": "Chicago",
        "last_name": "Bears",
        "position": "DEF",
        "team": "CHI",
        "active": True,
    },
    "LAR": {
        "player_id": "LAR",
        "first_name": "Los Angeles",
        "last_name": "Rams",
        "position": "DEF",
        "team": "LAR",
        "active": True,
    },
}


def test_yahoo_to_sleeper_team_defense():
    # Yahoo sends a defense as name "Bears", team "Chi", player_key 470.p.100003.
    indexes = crosswalk.build_indexes(DEF_PLAYERS)
    result = crosswalk.yahoo_to_sleeper(
        "470.p.100003", name="Bears", position="DEF", team="Chi", indexes=indexes
    )
    assert result["sleeper_id"] == "CHI"
    assert result["matched_by"] == "team_abbr"


def test_yahoo_to_sleeper_team_defense_rejects_nickname_mismatch():
    # A wrong abbreviation must not silently resolve to another team's defense.
    indexes = crosswalk.build_indexes(DEF_PLAYERS)
    result = crosswalk.yahoo_to_sleeper(
        "470.p.100003", name="Bears", position="DEF", team="LAR", indexes=indexes
    )
    assert result["sleeper_id"] is None
    assert result["matched_by"] is None


def test_yahoo_to_sleeper_team_defense_needs_team():
    indexes = crosswalk.build_indexes(DEF_PLAYERS)
    result = crosswalk.yahoo_to_sleeper("470.p.100003", name="Bears", position="DEF", indexes=indexes)
    assert result["sleeper_id"] is None


def test_enrich_with_sleeper_ids_team_defense():
    with patch("sleeper_core.crosswalk.load_players", return_value=DEF_PLAYERS):
        rows = crosswalk.enrich_with_sleeper_ids(
            [{"player_key": "470.p.100014", "name": "Rams", "position": "DEF", "nfl_team": "LAR"}],
            team_key="nfl_team",
        )
    assert rows[0]["sleeper_id"] == "LAR"
    assert rows[0]["crosswalk_matched_by"] == "team_abbr"


# Sleeper lists Yahoo's "Bam Knight" under his legal name; Ty Knight is a
# made-up teammate-elsewhere to prove the team and position gates.
NICKNAME_PLAYERS = {
    "8122": {
        "player_id": "8122",
        "full_name": "Zonovan Knight",
        "first_name": "Zonovan",
        "last_name": "Knight",
        "search_full_name": "zonovanknight",
        "position": "RB",
        "team": "ARI",
        "yahoo_id": None,
    },
    "9001": {
        "player_id": "9001",
        "full_name": "Ty Knight",
        "last_name": "Knight",
        "search_full_name": "tyknight",
        "position": "WR",
        "team": "ARI",
        "yahoo_id": None,
    },
    "7547": {
        "player_id": "7547",
        "full_name": "Kenneth Walker",
        "last_name": "Walker",
        "search_full_name": "kennethwalker",
        "position": "RB",
        "team": "KC",
        "yahoo_id": None,
    },
}


def test_last_name_key_strips_suffixes():
    assert crosswalk.last_name_key("Kenneth Walker III") == "walker"
    assert crosswalk.last_name_key("Marvin Harrison Jr.") == "harrison"
    assert crosswalk.last_name_key("Bam Knight") == "knight"
    assert crosswalk.last_name_key(None) == ""


def test_yahoo_to_sleeper_nickname_falls_back_to_surname_position_team():
    indexes = crosswalk.build_indexes(NICKNAME_PLAYERS)
    result = crosswalk.yahoo_to_sleeper(
        "470.p.34357", name="Bam Knight", position="RB", team="Ari", indexes=indexes
    )
    assert result["sleeper_id"] == "8122"
    assert result["matched_by"] == "last_name+position+team"


def test_surname_fallback_requires_team_and_position_to_agree():
    indexes = crosswalk.build_indexes(NICKNAME_PLAYERS)
    wrong_team = crosswalk.yahoo_to_sleeper(
        None, name="Bam Knight", position="RB", team="DEN", indexes=indexes
    )
    no_team = crosswalk.yahoo_to_sleeper(None, name="Bam Knight", position="RB", indexes=indexes)
    wrong_pos = crosswalk.yahoo_to_sleeper(
        None, name="Bam Knight", position="TE", team="Ari", indexes=indexes
    )
    assert [r["sleeper_id"] for r in (wrong_team, no_team, wrong_pos)] == [None, None, None]


def test_surname_fallback_refuses_ambiguous_hits():
    players = dict(NICKNAME_PLAYERS)
    players["9002"] = {**NICKNAME_PLAYERS["8122"], "player_id": "9002", "full_name": "Other Knight",
                       "search_full_name": "otherknight"}
    indexes = crosswalk.build_indexes(players)
    result = crosswalk.yahoo_to_sleeper(
        None, name="Bam Knight", position="RB", team="Ari", indexes=indexes
    )
    assert result["sleeper_id"] is None


def test_surname_fallback_handles_suffix_mismatch():
    # Yahoo "Kenneth Walker III" vs Sleeper "Kenneth Walker": full names differ.
    indexes = crosswalk.build_indexes(NICKNAME_PLAYERS)
    result = crosswalk.yahoo_to_sleeper(
        None, name="Kenneth Walker III", position="RB", team="KC", indexes=indexes
    )
    assert result["sleeper_id"] == "7547"
    assert result["matched_by"] == "last_name+position+team"


# Real shape: one active Kyle Williams WR on NE plus two retired namesakes.
NAMESAKE_PLAYERS = {
    sid: {"player_id": sid, "full_name": "Kyle Williams", "last_name": "Williams",
          "search_full_name": "kylewilliams", "position": "WR", "team": team,
          "yahoo_id": yahoo_id}
    for sid, team, yahoo_id in (("12547", "NE", None), ("7437", None, 33350), ("638", None, 24182))
}


def test_name_position_tie_broken_by_team():
    indexes = crosswalk.build_indexes(NAMESAKE_PLAYERS)
    result = crosswalk.yahoo_to_sleeper(
        "470.p.41864", name="Kyle Williams", position="WR", team="NE", indexes=indexes
    )
    assert result["sleeper_id"] == "12547"
    assert result["matched_by"] == "name+position+team"


def test_name_position_tie_stays_ambiguous_without_unique_team():
    indexes = crosswalk.build_indexes(NAMESAKE_PLAYERS)
    no_team = crosswalk.yahoo_to_sleeper(None, name="Kyle Williams", position="WR", indexes=indexes)
    other_team = crosswalk.yahoo_to_sleeper(
        None, name="Kyle Williams", position="WR", team="BUF", indexes=indexes
    )
    assert no_team["error"] == other_team["error"] == "ambiguous name+position match"
    assert no_team["matched_by"] is other_team["matched_by"] is None

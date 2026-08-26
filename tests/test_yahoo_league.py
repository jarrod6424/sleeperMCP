from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import yahoo_core.league as yahoo_league
from yahoo_core.parse import user_game_leagues

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "yahoo"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _yahoo_env(monkeypatch):
    monkeypatch.setenv("YAHOO_LEAGUE_KEY", "461.l.1000")
    monkeypatch.setenv("YAHOO_TEAM_NAME", "Pine Bluff Escapees")


def test_compute_league_parses_metadata():
    with patch("yahoo_core.league._fetch_league", return_value=_load("league_metadata.json")):
        league = yahoo_league.compute_league()
    assert league["platform"] == "yahoo"
    assert league["name"] == "Sunday Sweat"
    assert league["num_teams"] == 2
    assert league["current_week"] == 10
    assert "QB" in league["roster_positions"]


def test_compute_rosters_splits_starters_and_bench():
    with patch("yahoo_core.league._fetch_league", return_value=_load("league_teams.json")):
        rosters = yahoo_league.compute_rosters()
    assert isinstance(rosters, list)
    mine = next(r for r in rosters if r["owner"] == "Pine Bluff Escapees")
    assert mine["wins"] == 7
    assert mine["losses"] == 2
    assert mine["points_for"] == 1123.4
    assert len(mine["starters"]) == 1
    assert mine["starters"][0]["name"] == "Justin Jefferson"
    assert len(mine["bench"]) == 1


def test_compute_standings_ranks_by_wins():
    with patch("yahoo_core.league._fetch_league", return_value=_load("league_teams.json")):
        standings = yahoo_league.compute_standings()
    assert standings[0]["owner"] == "Pine Bluff Escapees"
    assert standings[0]["rank"] == 1
    assert standings[1]["rank"] == 2


def test_compute_my_team_includes_matchup():
    teams = _load("league_teams.json")
    scoreboard = _load("scoreboard_week10.json")

    def fake_fetch(league_key: str, out: str):
        if out == "teams,standings,rosters":
            return teams
        if out == "metadata,settings":
            return _load("league_metadata.json")
        raise AssertionError(out)

    with patch("yahoo_core.league._fetch_league", side_effect=fake_fetch):
        with patch("yahoo_core.league.get_json", return_value=scoreboard):
            report = yahoo_league.compute_my_team()
    assert report["owner"] == "Pine Bluff Escapees"
    assert report["rank"] == 1
    assert report["this_week"]["opponent"] == "Rival Squad"
    assert report["this_week"]["points"] == 121.34


def test_missing_league_key_returns_error():
    result = yahoo_league.compute_league(league_key="")
    assert result["error"]
    assert result["platform"] == "yahoo"


def test_server_platform_routing_unknown():
    import server

    result = server.get_league(platform="espn")
    assert "error" in result
    assert "supported_platforms" in result


def test_user_game_leagues_parser_skips_non_nfl_when_listed():
    raw = user_game_leagues(_load("user_leagues.json"))
    keys = {row["league_key"] for row in raw}
    assert "461.l.1000" in keys
    assert "461.l.2000" in keys
    assert "458.l.9" in keys  # parser returns all; list_user_leagues filters NFL


def test_list_user_leagues_filters_nfl_and_marks_default():
    with patch("yahoo_core.league.get_json", return_value=_load("user_leagues.json")):
        result = yahoo_league.list_user_leagues(season="2025")
    assert result["platform"] == "yahoo"
    assert len(result["leagues"]) == 2
    assert {row["name"] for row in result["leagues"]} == {"Sunday Sweat", "Work League"}
    default = next(row for row in result["leagues"] if row["is_default"])
    assert default["league_id"] == "461.l.1000"


def test_list_my_leagues_merges_platforms():
    import server

    sleeper = {
        "platform": "sleeper",
        "leagues": [
            {
                "platform": "sleeper",
                "league_id": "1312218810614300672",
                "name": "Gridiron Time Machine",
                "season": "2025",
                "status": "in_season",
                "num_teams": 12,
                "is_default": True,
            }
        ],
    }
    yahoo = {
        "platform": "yahoo",
        "leagues": [
            {
                "platform": "yahoo",
                "league_id": "461.l.1000",
                "name": "Sunday Sweat",
                "season": "2025",
                "is_default": True,
            }
        ],
    }
    with patch("server._league_mod.list_user_leagues", return_value=sleeper):
        with patch("server._yahoo_league.list_user_leagues", return_value=yahoo):
            result = server.list_my_leagues(season="2025")
    assert result["platforms_queried"] == ["sleeper", "yahoo"]
    assert len(result["leagues"]) == 2
    assert result["errors"] == []


def test_list_my_leagues_yahoo_only_surfaces_config_error():
    import server

    with patch(
        "server._yahoo_league.list_user_leagues",
        return_value={"error": "Yahoo is not configured", "platform": "yahoo"},
    ):
        result = server.list_my_leagues(platform="yahoo")
    assert result["leagues"] == []
    assert result["errors"][0]["platform"] == "yahoo"


def test_scout_team_partial_match():
    teams = _load("league_teams.json")
    scoreboard = _load("scoreboard_week10.json")

    def fake_fetch(league_key: str, out: str):
        if out == "teams,standings,rosters":
            return teams
        if out == "metadata,settings":
            return _load("league_metadata.json")
        raise AssertionError(out)

    with patch("yahoo_core.league._fetch_league", side_effect=fake_fetch):
        with patch("yahoo_core.league.get_json", return_value=scoreboard):
            report = yahoo_league.scout_team("Rival Sq")
    assert report["owner"] == "Rival Squad"
    assert report["matched_by"] == "partial"
    assert report["this_week"]["opponent"] == "Pine Bluff Escapees"


def test_scout_team_no_match_lists_available():
    with patch("yahoo_core.league._fetch_league", return_value=_load("league_teams.json")):
        result = yahoo_league.scout_team("Nobody")
    assert result["error"] == "no team matched"
    assert "Pine Bluff Escapees" in result["available_teams"]


def test_compute_transactions_parses_add_drop_and_trade():
    with patch("yahoo_core.league.get_json", return_value=_load("transactions.json")):
        moves = yahoo_league.compute_transactions()
    assert isinstance(moves, list)
    assert len(moves) == 2
    assert moves[0]["type"] == "add/drop"
    assert moves[0]["adds"][0]["name"] == "Pickup Player"
    assert moves[0]["drops"][0]["name"] == "Dropped Player"
    assert moves[0]["faab_bid"] == 7
    assert moves[1]["type"] == "trade"
    assert "Rival Squad" in moves[1]["teams"]


def test_server_yahoo_transactions_routing():
    import server

    with patch(
        "server._yahoo_league.compute_transactions",
        return_value=[{"type": "add", "platform": "yahoo"}],
    ):
        result = server.get_transactions(platform="yahoo")
    assert result[0]["platform"] == "yahoo"
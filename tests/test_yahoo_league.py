from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import yahoo_core.league as yahoo_league

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

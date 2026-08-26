from __future__ import annotations

from unittest.mock import patch

from yahoo_core import scoring, values
from yahoo_core.scoring import reception_points


def test_reception_points_from_stat_modifiers():
    settings = {
        "stat_modifiers": {
            "stats": {
                "0": {"stat": {"stat_id": 11, "value": "1.0"}},
                "count": 1,
            }
        }
    }
    assert reception_points(settings) == 1.0
    ppr, label = scoring.ppr_from_reception_points(1.0)
    assert ppr == 1.0
    assert label == "PPR"


def test_compute_league_exposes_half_ppr():
    import yahoo_core.league as yahoo_league
    from pathlib import Path
    import json

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/yahoo/league_metadata.json").read_text()
    )
    with patch("yahoo_core.league._fetch_league", return_value=fixture):
        league = yahoo_league.compute_league("461.l.1000")
    assert league["reception_points"] == 0.5
    assert league["scoring_format_label"] == "Half-PPR"


def test_league_format_uses_modifiers():
    league = {
        "league_key": "461.l.1000",
        "num_teams": 12,
        "roster_positions": ["QB", "RB", "WR", "TE", "FLEX", "Q/W/R/T"],
        "reception_points": 0.5,
        "raw_settings": {
            "stat_modifiers": {
                "stats": {"0": {"stat": {"stat_id": 11, "value": "0.5"}}, "count": 1}
            }
        },
    }
    with patch("yahoo_core.values.compute_league", return_value=league):
        fmt = values.league_format("461.l.1000")
    assert fmt["ppr"] == 0.5
    assert fmt["numQbs"] == 2
    assert fmt["reception_points_source"] == "yahoo_stat_modifiers"


def test_value_my_roster_joins_sleeper_ids():
    report = {
        "owner": "Pine Bluff Escapees",
        "starters": [
            {
                "name": "Justin Jefferson",
                "player_id": "32692",
                "sleeper_id": "6794",
                "position": "WR",
                "selected_position": "WR",
                "crosswalk_matched_by": "yahoo_id",
            }
        ],
        "bench": [],
    }
    fmt = {
        "ppr": 1,
        "numQbs": 1,
        "numTeams": 12,
        "isDynasty": False,
        "scoring_format_label": "PPR",
        "reception_points_source": "yahoo_stat_modifiers",
        "league_key": "461.l.1000",
    }
    fc = [
        {
            "value": 88.5,
            "player": {"sleeperId": "6794", "name": "Justin Jefferson", "position": "WR"},
        }
    ]
    with patch("yahoo_core.values.resolve_league_key", return_value="461.l.1000"):
        with patch("yahoo_core.values.league_format", return_value=fmt):
            with patch("yahoo_core.values.compute_my_team", return_value=report):
                with patch("yahoo_core.values.sleeper_values.fc_values", return_value=fc):
                    result = values.value_my_roster()
    assert result["total_value"] == 88.5
    assert result["players"][0]["sleeper_id"] == "6794"


def test_analyze_trade_verdict():
    fmt = {
        "ppr": 1,
        "numQbs": 1,
        "numTeams": 12,
        "isDynasty": False,
        "scoring_format_label": "PPR",
        "reception_points_source": "YAHOO_SCORING_FORMAT",
        "league_key": "461.l.1000",
    }
    fc = [
        {
            "value": 100,
            "player": {"sleeperId": "1", "name": "Star A", "position": "RB"},
        },
        {
            "value": 40,
            "player": {"sleeperId": "2", "name": "Role B", "position": "WR"},
        },
    ]
    with patch("yahoo_core.values.resolve_league_key", return_value="461.l.1000"):
        with patch("yahoo_core.values.league_format", return_value=fmt):
            with patch("yahoo_core.values.sleeper_values.fc_values", return_value=fc):
                result = values.analyze_trade(["Star A"], ["Role B"])
    assert result["verdict"] == "favor_give"
    assert result["delta_get_minus_give"] == -60


def test_server_yahoo_value_and_trade_routing():
    import server

    with patch(
        "server._yahoo_values.value_my_roster",
        return_value={"platform": "yahoo", "total_value": 1},
    ):
        assert server.value_my_roster(platform="yahoo")["platform"] == "yahoo"
    with patch(
        "server._yahoo_values.analyze_trade",
        return_value={"platform": "yahoo", "verdict": "roughly_even"},
    ):
        assert server.analyze_trade(["A"], ["B"], platform="yahoo")["verdict"] == "roughly_even"

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


def test_trade_values_filters_position():
    fmt = {
        "ppr": 0.5,
        "numQbs": 1,
        "numTeams": 12,
        "isDynasty": False,
        "scoring_format_label": "Half-PPR",
        "reception_points_source": "yahoo_stat_modifiers",
        "league_key": "461.l.1000",
    }
    fc = [
        {"value": 90, "player": {"sleeperId": "1", "name": "RB1", "position": "RB"}},
        {"value": 80, "player": {"sleeperId": "2", "name": "WR1", "position": "WR"}},
    ]
    with patch("yahoo_core.values.league_format", return_value=fmt):
        with patch("yahoo_core.values.sleeper_values.fc_values", return_value=fc):
            result = values.trade_values(position="WR", limit=10)
    assert result["platform"] == "yahoo"
    assert len(result["players"]) == 1
    assert result["players"][0]["position"] == "WR"


def test_auction_budgets_assumes_200_when_not_auction():
    league = {
        "league_key": "461.l.1000",
        "name": "Sunday Sweat",
        "num_teams": 12,
        "roster_positions": ["QB", "RB", "WR", "TE", "FLEX", "BN", "BN"],
        "settings": {"is_auction_draft": "0"},
    }
    fmt = {
        "ppr": 0.5,
        "numQbs": 1,
        "numTeams": 12,
        "isDynasty": False,
        "scoring_format_label": "Half-PPR",
        "reception_points_source": "yahoo_stat_modifiers",
        "league_key": "461.l.1000",
    }
    fc = [
        {"value": 100, "player": {"sleeperId": "1", "name": "Star", "position": "RB"}},
        {"value": 50, "player": {"sleeperId": "2", "name": "Role", "position": "WR"}},
    ]
    with patch("yahoo_core.values.compute_league", return_value=league):
        with patch("yahoo_core.values.league_format", return_value=fmt):
            with patch("yahoo_core.values.sleeper_values.fc_values", return_value=fc):
                result = values.auction_budgets(limit=5)
    assert result["budget"] == 200
    assert result["assumed_auction"] is True
    assert result["players"]
    assert result["players"][0]["fair"] >= 1


def test_playoff_bracket_uses_scoreboards():
    league = {
        "league_key": "461.l.1000",
        "settings": {"playoff_start_week": "14", "num_playoff_teams": "4"},
    }
    board = {"platform": "yahoo", "week": 14, "matchups": [{"home": "A", "away": "B"}]}
    with patch("yahoo_core.values.compute_league", return_value=league):
        with patch("yahoo_core.league.compute_matchups", return_value=board):
            result = values.playoff_bracket(weeks=1)
    assert result["playoff_start_week"] == 14
    assert len(result["weeks"]) == 1
    assert "no winners/losers bracket" in result["note"].lower()


def test_traded_picks_unavailable_is_explicit():
    with patch("yahoo_core.values.resolve_league_key", return_value="461.l.1000"):
        rows = values.traded_picks_unavailable()
    assert rows[0]["error"] == "yahoo_traded_picks_unsupported"


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
    with patch(
        "server._yahoo_waiver.waiver_advice",
        return_value={"platform": "yahoo", "verdict": "ok"},
    ):
        assert server.waiver_advice(platform="yahoo")["platform"] == "yahoo"
    with patch(
        "server._yahoo_grade.grade_team",
        return_value={"platform": "yahoo", "classification": "mid_pack"},
    ):
        assert server.grade_team(platform="yahoo")["classification"] == "mid_pack"
    with patch(
        "server._yahoo_values.trade_values",
        return_value={"platform": "yahoo", "players": []},
    ):
        assert server.get_trade_values(platform="yahoo")["platform"] == "yahoo"
    with patch(
        "server._yahoo_values.auction_budgets",
        return_value={"platform": "yahoo", "budget": 200},
    ):
        assert server.get_auction_budgets(platform="yahoo")["budget"] == 200
    with patch(
        "server._yahoo_values.playoff_bracket",
        return_value={"platform": "yahoo", "weeks": []},
    ):
        assert server.get_playoff_bracket(platform="yahoo")["platform"] == "yahoo"
    with patch(
        "server._yahoo_values.weekly_projections",
        return_value={"platform": "yahoo", "players": []},
    ):
        assert server.get_projections(platform="yahoo")["platform"] == "yahoo"
    with patch(
        "server._yahoo_values.adp",
        return_value={"platform": "yahoo", "players": []},
    ):
        assert server.get_adp(platform="yahoo")["platform"] == "yahoo"
    with patch(
        "server._yahoo_values.dynasty_tiers",
        return_value={"platform": "yahoo", "tiers": []},
    ):
        assert server.get_dynasty_tiers(platform="yahoo")["platform"] == "yahoo"
    with patch(
        "server._yahoo_values.traded_picks_unavailable",
        return_value=[{"error": "yahoo_traded_picks_unsupported"}],
    ):
        assert server.get_traded_picks(platform="yahoo")[0]["error"] == "yahoo_traded_picks_unsupported"

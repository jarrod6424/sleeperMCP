from __future__ import annotations

from unittest.mock import patch

from sleeper_core import trade, waiver
from sleeper_core.picks import ROUND_MID_SF


FC = [
    {
        "value": 8000,
        "overallRank": 5,
        "positionRank": 1,
        "player": {
            "name": "Marvin Harrison",
            "position": "WR",
            "sleeperId": "111",
            "maybeTeam": "ARI",
            "maybeAge": 23,
        },
    },
    {
        "value": 1200,
        "overallRank": 80,
        "positionRank": 30,
        "player": {
            "name": "MarShawn Lloyd",
            "position": "RB",
            "sleeperId": "222",
            "maybeTeam": "GB",
            "maybeAge": 24,
        },
    },
    {
        "value": 900,
        "overallRank": 90,
        "positionRank": 40,
        "player": {
            "name": "Matthew Golden",
            "position": "WR",
            "sleeperId": "333",
            "maybeTeam": "GB",
            "maybeAge": 22,
        },
    },
    {
        "value": 3500,
        "overallRank": 20,
        "positionRank": 8,
        "player": {
            "name": "Daniel Jones",
            "position": "QB",
            "sleeperId": "444",
            "maybeTeam": "IND",
            "maybeAge": 29,
        },
    },
]


def _fmt(num_qbs=2, dynasty=True):
    return {"ppr": 1, "numQbs": num_qbs, "numTeams": 12, "isDynasty": dynasty}


def _run(give, get, **kwargs):
    with patch("sleeper_core.trade.resolve_league_id", return_value="L1"):
        with patch("sleeper_core.trade.league_format", return_value=_fmt()):
            with patch("sleeper_core.trade.fc_values", return_value=FC):
                with patch("sleeper_core.trade.get_json", return_value={"week": 1, "season": "2026"}):
                    with patch("sleeper_core.trade.user_map", return_value={}):
                        with patch("sleeper_core.trade.compute_standings", return_value=[]):
                            with patch("sleeper_core.trade.resolve_my_roster", return_value=None):
                                with patch("sleeper_core.trade.resolve_roster", return_value=None):
                                    return trade.analyze_trade(give, get, **kwargs)


def test_player_only_regression_fields():
    result = _run(["MarShawn Lloyd"], ["Marvin Harrison"])
    assert "give" in result and "get" in result
    assert result["give"][0]["name"] == "MarShawn Lloyd"
    assert result["get"][0]["name"] == "Marvin Harrison"
    assert result["give_total"] == 1200
    assert result["get_total"] == 8000
    assert result["difference"] == 6800
    assert result["verdict"] == "you come out ahead"
    assert result["unmatched"] == {"give": [], "get": []}
    assert result["picks"]["give"] == []
    assert result["picks"]["get"] == []
    assert "format" in result
    assert result["format"]["isDynasty"] is True


def test_pick_vs_player_numeric_totals():
    result = _run(["2027 1st"], ["Marvin Harrison"])
    assert result["picks"]["give"]
    assert result["picks"]["give"][0]["value"] == ROUND_MID_SF[1]
    assert result["give_total"] == ROUND_MID_SF[1]
    assert result["get_total"] == 8000
    assert result["get"][0]["name"] == "Marvin Harrison"
    assert result["unpriced_assets"] == []
    assert any("heuristic" in x.lower() for x in result["limitations"])


def test_invalid_pick_is_unpriced_not_crash():
    result = _run(["2027 pick please"], ["Marvin Harrison"])
    assert result["get_total"] == 8000
    assert result["give_total"] == 0
    assert result["unpriced_assets"]
    assert result["unpriced_assets"][0]["kind"] == "pick"
    assert result["unpriced_assets"][0]["side"] == "give"


def test_superflex_first_higher_than_1qb_in_trade_pricing():
    with patch("sleeper_core.trade.resolve_league_id", return_value="L1"):
        with patch("sleeper_core.trade.fc_values", return_value=FC):
            with patch("sleeper_core.trade.get_json", return_value={}):
                with patch("sleeper_core.trade.user_map", return_value={}):
                    with patch("sleeper_core.trade.compute_standings", return_value=[]):
                        with patch("sleeper_core.trade.resolve_my_roster", return_value=None):
                            with patch("sleeper_core.trade.league_format", return_value=_fmt(num_qbs=2)):
                                sf = trade.analyze_trade(["2027 1st"], ["Daniel Jones"])
                            with patch("sleeper_core.trade.league_format", return_value=_fmt(num_qbs=1)):
                                one = trade.analyze_trade(["2027 1st"], ["Daniel Jones"])
    assert sf["picks"]["give"][0]["value"] > one["picks"]["give"][0]["value"]
    assert sf["picks"]["give"][0]["source"] == "static_schedule"
    assert one["picks"]["give"][0]["source"] == "static_schedule"


def test_manual_override():
    result = _run(
        ["2027 1st"],
        ["Daniel Jones"],
        pick_value_model="manual",
        pick_overrides={"2027 1st": 999},
    )
    assert result["picks"]["give"][0]["value"] == 999
    assert result["picks"]["give"][0]["source"] == "manual"


def test_dynasty_weights_value_ahead_of_projection():
    d = waiver.weights_for("dynasty")
    r = waiver.weights_for("redraft")
    assert d["trade_value"] > d["projection"]
    assert r["projection"] > r["trade_value"]
    assert abs(sum(d.values()) - 1.0) < 1e-9
    assert abs(sum(r.values()) - 1.0) < 1e-9
    assert waiver.resolve_mode("auto", True) == "dynasty"
    assert waiver.resolve_mode("auto", False) == "redraft"

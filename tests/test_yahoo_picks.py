from __future__ import annotations

from unittest.mock import patch

from yahoo_core import values


def test_yahoo_pick_tokens_are_unpriced():
    fmt = {
        "ppr": 1,
        "numQbs": 2,
        "numTeams": 12,
        "isDynasty": False,
        "scoring_format_label": "PPR",
        "reception_points_source": "YAHOO_SCORING_FORMAT",
        "league_key": "461.l.1000",
    }
    fc = [
        {
            "value": 3500,
            "player": {"sleeperId": "444", "name": "Daniel Jones", "position": "QB"},
        }
    ]
    with patch("yahoo_core.values.resolve_league_key", return_value="461.l.1000"):
        with patch("yahoo_core.values.league_format", return_value=fmt):
            with patch("yahoo_core.values.sleeper_values.fc_values", return_value=fc):
                result = values.analyze_trade(["2027 1st"], ["Daniel Jones"])
    assert result["get"]["total"] == 3500
    assert result["give"]["total"] == 0
    assert result["unpriced_assets"]
    assert result["unpriced_assets"][0]["reason"] == "yahoo_picks_unsupported"
    assert result["picks"]["give"] == []
    assert "never" not in (result["unpriced_assets"][0].get("value") or "")
    assert all("value" not in a or a.get("value") in (None, 0) for a in result["unpriced_assets"])

from __future__ import annotations

from unittest.mock import patch

import sleeper_core.league as sleeper_league


def test_list_user_leagues_formats_entries():
    user = {"user_id": "99", "username": "JarrodLee", "display_name": "Jarrod"}
    raw = [
        {
            "league_id": "1312218810614300672",
            "name": "Gridiron Time Machine",
            "season": "2025",
            "status": "in_season",
            "total_rosters": 12,
        },
        {
            "league_id": "other",
            "name": "Second League",
            "season": "2025",
            "status": "complete",
            "total_rosters": 10,
        },
    ]
    with patch("sleeper_core.league.resolve_user_id", return_value=user):
        with patch("sleeper_core.league.get_json", return_value=raw) as get_json:
            result = sleeper_league.list_user_leagues(season="2025")
    assert result["platform"] == "sleeper"
    assert result["username"] == "JarrodLee"
    assert len(result["leagues"]) == 2
    assert result["leagues"][0]["is_default"] is True
    assert result["leagues"][1]["is_default"] is False
    get_json.assert_called_once_with("/user/99/leagues/nfl/2025", cache=True)


def test_list_user_leagues_missing_user():
    with patch("sleeper_core.league.resolve_user_id", return_value=None):
        result = sleeper_league.list_user_leagues(username="nobody")
    assert result["error"]
    assert result["platform"] == "sleeper"

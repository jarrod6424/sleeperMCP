from __future__ import annotations

from unittest.mock import patch

import build_benchmarks as bb


def test_empty_participation_is_best_effort() -> None:
    with patch.object(bb, "nflverse_csv", return_value=[]):
        assert bb.load_route_participation(2024, "WR") == {}


def test_wr_on_field_for_pass_counts_toward_rate() -> None:
    """Synthetic: WR on 2/3 team pass plays → ~66.7%."""
    events = [
        {"player_key": "t.hill", "team": "MIA", "team_pass_plays": 3, "on_pass": 2},
    ]
    rates = bb._route_rates_from_events(events)
    assert rates["t.hill"] == 66.667


def test_loader_scopes_team_pass_denominator_to_wr_active_games() -> None:
    participation = [
        {"nflverse_game_id": "2024_01_MIA_BUF", "play_id": "1",
         "offense_players": "00-0031234;00-0030001"},
        {"nflverse_game_id": "2024_01_MIA_BUF", "play_id": "2",
         "offense_players": "00-0031234;00-0030001"},
        {"nflverse_game_id": "2024_01_MIA_BUF", "play_id": "3",
         "offense_players": "00-0030001"},
    ]
    pbp = [
        {"season_type": "REG", "play_type": "pass", "game_id": "2024_01_MIA_BUF",
         "play_id": "1", "posteam": "MIA"},
        {"season_type": "REG", "play_type": "pass", "game_id": "2024_01_MIA_BUF",
         "play_id": "2", "posteam": "MIA"},
        {"season_type": "REG", "play_type": "pass", "game_id": "2024_01_MIA_BUF",
         "play_id": "3", "posteam": "MIA"},
    ]
    stats = [{"player_id": "00-0031234", "player_display_name": "Tyreek Hill",
              "position": "WR", "team": "MIA"}]

    def load_rows(tag: str, *_args, **_kwargs) -> list[dict]:
        return {"pbp_participation": participation, "pbp": pbp, "stats_player": stats}[tag]

    with patch.object(bb, "nflverse_csv", side_effect=load_rows):
        assert bb.load_route_participation(2024, "WR") == {"thill": 66.667}

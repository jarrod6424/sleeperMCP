from __future__ import annotations

from unittest.mock import patch

import build_benchmarks as bb


def test_empty_participation_is_best_effort() -> None:
    with patch.object(bb, "nflverse_csv", return_value=[]):
        assert bb.load_te_route_participation(2024) == {}


def test_te_on_field_for_pass_counts_toward_rate() -> None:
    """Synthetic: TE on 2/2 team pass plays → 100%."""
    events = [
        {"player_key": "t.kelce", "team": "KC", "team_pass_plays": 2, "on_pass": 2},
        {"player_key": "n.gray", "team": "KC", "team_pass_plays": 2, "on_pass": 1},
    ]
    rates = bb._route_rates_from_events(events)
    assert rates["t.kelce"] == 100.0
    assert rates["n.gray"] == 50.0


def test_loader_uses_all_joined_team_pass_plays_as_denominator() -> None:
    participation = [
        {"nflverse_game_id": "2024_01_BAL_KC", "play_id": "1",
         "offense_players": "00-0030506;00-0030001"},
        {"nflverse_game_id": "2024_01_BAL_KC", "play_id": "2",
         "offense_players": "00-0030001"},
    ]
    pbp = [
        {"season_type": "REG", "play_type": "pass", "game_id": "2024_01_BAL_KC",
         "play_id": "1", "posteam": "KC"},
        {"season_type": "REG", "play_type": "pass", "game_id": "2024_01_BAL_KC",
         "play_id": "2", "posteam": "KC"},
    ]
    stats = [{"player_id": "00-0030506", "player_display_name": "Travis Kelce",
              "position": "TE", "team": "KC"}]

    def load_rows(tag: str, *_args, **_kwargs) -> list[dict]:
        return {"pbp_participation": participation, "pbp": pbp, "stats_player": stats}[tag]

    with patch.object(bb, "nflverse_csv", side_effect=load_rows):
        assert bb.load_te_route_participation(2024) == {"tkelce": 50.0}

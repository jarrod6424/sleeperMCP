from __future__ import annotations

from unittest.mock import patch

import build_factors as bf


def _season_row(name: str, points: float) -> dict:
    return {
        "name": name,
        "position": "WR",
        "season": 2025,
        "games": bf.FINISH_MIN_GAMES,
        "fp_ppr": points,
    }


def test_finishers_by_season_respects_requested_rank_cutoff() -> None:
    rows = [_season_row(f"Player {rank}", 100 - rank) for rank in range(1, 10)]

    with patch.object(bf, "load_player_seasons", return_value=rows):
        top5 = bf.finishers_by_season([2025], 5)
        top8 = bf.finishers_by_season([2025], 8)

    top5_keys = top5[(2025, "WR")]
    top8_keys = top8[(2025, "WR")]
    assert len(top5_keys) == 5
    assert len(top8_keys) == 8
    assert top5_keys <= top8_keys


def test_finish_history_reports_count_and_sorted_seasons() -> None:
    player_key = next(iter(bf.name_keys("Player One")))
    finishers = {
        (2023, "WR"): {player_key},
        (2024, "WR"): set(),
        (2025, "WR"): {player_key},
    }

    assert bf.finish_history(
        "Player One", "WR", finishers, [2025, 2023, 2024]
    ) == {
        "count": 2,
        "seasons": [2023, 2025],
    }

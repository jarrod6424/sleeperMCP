from __future__ import annotations

from unittest.mock import patch

import build_factors as bf


def test_out_is_serious() -> None:
    assert bf.classify_injury_concern(["Out", "Questionable"]) == "serious"


def test_three_questionable_weeks_escalates() -> None:
    # three distinct weeks of Questionable → some → concerned
    assert bf.classify_injury_concern(
        ["Questionable", "Questionable", "Questionable"]
    ) == "concerned"


def test_no_listings_is_minimal() -> None:
    assert bf.classify_injury_concern([]) == "minimal"


def test_empty_injury_file_returns_empty_map() -> None:
    with patch.object(bf, "nflverse_csv", return_value=[]):
        assert bf.load_injury_concern_season(2024) == {}

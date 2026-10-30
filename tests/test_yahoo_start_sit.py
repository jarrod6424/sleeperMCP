from __future__ import annotations

from unittest.mock import patch

from yahoo_core import start_sit


def test_scoring_field_defaults_and_aliases():
    assert start_sit.scoring_field("ppr")[0] == "pts_ppr"
    assert start_sit.scoring_field("half_ppr")[0] == "pts_half_ppr"
    assert start_sit.scoring_field("std")[0] == "pts_std"


def test_yahoo_start_sit_uses_sleeper_projections():
    report = {
        "owner": "Pine Bluff Escapees",
        "starters": [
            {
                "name": "Starter WR",
                "player_id": "1",
                "sleeper_id": "100",
                "position": "WR",
                "selected_position": "WR",
            },
            {
                "name": "Starter RB",
                "player_id": "2",
                "sleeper_id": "200",
                "position": "RB",
                "selected_position": "RB",
            },
        ],
        "bench": [
            {
                "name": "Bench RB",
                "player_id": "3",
                "sleeper_id": "300",
                "position": "RB",
                "selected_position": "BN",
            },
            {
                "name": "Unmatched",
                "player_id": "4",
                "sleeper_id": None,
                "position": "TE",
                "selected_position": "BN",
            },
        ],
    }
    league = {
        "current_week": 10,
        "season": "2025",
        "roster_positions": ["RB", "WR", "FLEX", "BN"],
    }
    proj = {
        "100": {"pts_ppr": 8.0},
        "200": {"pts_ppr": 6.0},
        "300": {"pts_ppr": 12.0},
    }

    with patch("yahoo_core.start_sit.resolve_league_key", return_value="461.l.1000"):
        with patch("yahoo_core.start_sit.compute_my_team", return_value=report):
            with patch("yahoo_core.start_sit.compute_league", return_value=league):
                with patch(
                    "yahoo_core.values.league_format",
                    return_value={"isDynasty": False, "numQbs": 1, "ppr": 1, "numTeams": 12},
                ):
                    with patch(
                        "yahoo_core.start_sit.sleeper_get_json",
                        return_value={"week": 10, "season": "2025"},
                    ):
                        with patch(
                            "yahoo_core.start_sit.sleeper_proj.projections_for",
                            return_value=proj,
                        ):
                            with patch(
                                "sleeper_core.start_sit.week_matchups",
                                return_value={},
                            ):
                                with patch(
                                    "sleeper_core.start_sit.recent_floor_map",
                                    return_value={},
                                ):
                                    advice = start_sit.start_sit_advice(
                                        week=10, scoring_format="ppr"
                                    )

    assert advice["platform"] == "yahoo"
    assert advice["team"] == "Pine Bluff Escapees"
    # Optimal should prefer Bench RB (12) over Starter RB (6) in RB/FLEX.
    assert advice["potential_point_gain"] > 0
    assert any(p["name"] == "Bench RB" for p in advice["consider_starting"])
    assert any(p["name"] == "Unmatched" for p in advice["unmatched_players"])
    for row in advice["consider_starting"]:
        assert row["reasons"] and row["reason_codes"]


def test_server_yahoo_start_sit_routing():
    import server

    with patch(
        "server._yahoo_start_sit.start_sit_advice",
        return_value={"platform": "yahoo", "team": "X"},
    ) as fn:
        result = server.start_sit_advice(platform="yahoo", week=10)
    assert result["platform"] == "yahoo"
    fn.assert_called_once()
    assert fn.call_args.kwargs.get("strategy") == "balanced"


def test_yahoo_empty_projections_structured_error():
    report = {"owner": "X", "starters": [], "bench": []}
    league = {"current_week": 1, "season": "2026", "roster_positions": ["RB"]}
    with patch("yahoo_core.start_sit.resolve_league_key", return_value="461.l.1000"):
        with patch("yahoo_core.start_sit.compute_my_team", return_value=report):
            with patch("yahoo_core.start_sit.compute_league", return_value=league):
                with patch(
                    "yahoo_core.start_sit.sleeper_get_json",
                    return_value={"week": 1, "season": "2026"},
                ):
                    with patch(
                        "yahoo_core.start_sit.sleeper_proj.projections_for",
                        return_value={},
                    ):
                        err = start_sit.start_sit_advice(week=1)
    assert err["error"] == "no projection data returned"
    assert "guidance" in err["fallback"]
    assert "optimal_lineup" not in err


def test_sleeper_roster_slots_maps_yahoo_flex_names():
    from yahoo_core.league import sleeper_roster_slots

    assert sleeper_roster_slots(["QB", "W/R/T", "w/r", "W/T", "Q/W/R/T", "BN", "IR"]) == [
        "QB", "FLEX", "WRRB_FLEX", "REC_FLEX", "SUPER_FLEX", "BN", "IR",
    ]
    assert sleeper_roster_slots(None) == []


def test_yahoo_start_sit_fills_wrt_flex():
    # Slot names exactly as a live Yahoo league reports them. Before the
    # mapping, W/R/T matched no position, the optimal lineup came up one
    # starter short, and the "gain" went negative.
    report = {
        "owner": "Pine Bluff Escapees",
        "starters": [
            {"name": "RB1", "player_id": "1", "sleeper_id": "100", "position": "RB", "selected_position": "RB"},
            {"name": "WR1", "player_id": "2", "sleeper_id": "200", "position": "WR", "selected_position": "WR"},
            {"name": "Flex WR", "player_id": "3", "sleeper_id": "300", "position": "WR", "selected_position": "W/R/T"},
        ],
        "bench": [
            {"name": "Bench WR", "player_id": "4", "sleeper_id": "400", "position": "WR", "selected_position": "BN"},
        ],
    }
    league = {"current_week": 8, "season": "2026", "roster_positions": ["RB", "WR", "W/R/T", "BN", "IR"]}
    proj = {"100": {"pts_ppr": 15.0}, "200": {"pts_ppr": 14.0}, "300": {"pts_ppr": 9.0}, "400": {"pts_ppr": 11.0}}

    with patch("yahoo_core.start_sit.resolve_league_key", return_value="461.l.1000"), patch(
        "yahoo_core.start_sit.compute_my_team", return_value=report
    ), patch("yahoo_core.start_sit.compute_league", return_value=league), patch(
        "yahoo_core.values.league_format",
        return_value={"isDynasty": False, "numQbs": 1, "ppr": 1, "numTeams": 12},
    ), patch(
        "yahoo_core.start_sit.sleeper_get_json", return_value={"week": 8, "season": "2026"}
    ), patch(
        "yahoo_core.start_sit.sleeper_proj.projections_for", return_value=proj
    ), patch("sleeper_core.start_sit.week_matchups", return_value={}), patch(
        "sleeper_core.start_sit.recent_floor_map", return_value={}
    ):
        advice = start_sit.start_sit_advice(week=8, scoring_format="ppr")

    assert [row["slot"] for row in advice["optimal_lineup"]].count("FLEX") == 1
    assert len(advice["optimal_lineup"]) == 3
    assert advice["unfilled_slots"] == []
    assert advice["current_projected"] == 38.0
    assert advice["optimal_projected"] == 40.0
    assert advice["potential_point_gain"] == 2.0
    assert [p["name"] for p in advice["consider_starting"]] == ["Bench WR"]

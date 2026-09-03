from __future__ import annotations

from unittest.mock import patch

from sleeper_core import projections
from sleeper_core import start_sit


def _player(pid, name, pos, proj, team="KC", injury=None):
    return {
        "player_id": pid,
        "name": name,
        "position": pos,
        "proj": float(proj),
        "team": team,
        "injury_status": injury,
    }


FMT = {"isDynasty": True, "numQbs": 2, "ppr": 1, "numTeams": 12}
SUBJECT = {"team_name": "Pine Bluff Escapees", "manager": "JarrodLee", "roster_id": 1}


def _build(slots, pool, starters, strategy="balanced", matchups=None):
    with patch("sleeper_core.start_sit.week_matchups", return_value=matchups or {}):
        with patch("sleeper_core.start_sit.recent_floor_map", return_value={}):
            return start_sit.build_from_pool(
                league_id="L1",
                platform="sleeper",
                fmt=FMT,
                season="2025",
                week=10,
                subject=SUBJECT,
                scoring_label="PPR",
                slots=slots,
                pool=pool,
                current_starter_ids=starters,
                strategy=strategy,
                source="test",
            )


def test_injury_haircut_and_lineup_score():
    assert start_sit.injury_haircut("OUT", 12.0) == 0.0
    assert start_sit.injury_haircut("DOUBTFUL", 10.0) == 3.5
    assert start_sit.injury_haircut("QUESTIONABLE", 10.0) == 7.5
    assert start_sit.injury_haircut(None, 10.0) == 10.0
    assert start_sit.lineup_score(10.0, 10.0, "ceiling") == 10.0
    assert start_sit.lineup_score(10.0, 4.0, "floor") == start_sit.lineup_score(10.0, 4.0, "floor")
    assert start_sit.normalize_strategy("FLOOR") == "floor"
    assert start_sit.normalize_strategy("nope") == "balanced"


def test_optimal_lineup_score_key_does_not_change_eligibility():
    pool = [
        {"player_id": "a", "position": "RB", "proj": 12.0, "lineup_score": 5.0},
        {"player_id": "b", "position": "RB", "proj": 10.0, "lineup_score": 9.0},
        {"player_id": "c", "position": "WR", "proj": 20.0, "lineup_score": 20.0},
    ]
    by_proj = projections.optimal_lineup(["RB"], pool, score_key="proj")
    by_floor = projections.optimal_lineup(["RB"], pool, score_key="lineup_score")
    assert by_proj[0]["player_id"] == "a"
    assert by_floor[0]["player_id"] == "b"
    # Restrictive slot still refuses the WR even with a huge score.
    assert all(p["player_id"] != "c" for p in by_proj + by_floor)


def test_every_swap_has_reasons_and_reason_codes():
    pool = [
        _player("wr_start", "Sit WR", "WR", 8.0),
        _player("wr_bench", "Start WR", "WR", 14.0),
        _player("rb1", "RB One", "RB", 12.0),
    ]
    out = _build(["WR", "RB"], pool, starters=["wr_start", "rb1"])
    assert out["potential_point_gain"] > 0
    assert out["consider_starting"]
    assert out["consider_benching"]
    for row in out["consider_starting"] + out["consider_benching"]:
        assert isinstance(row["reasons"], list) and len(row["reasons"]) >= 1
        assert isinstance(row["reason_codes"], list) and len(row["reason_codes"]) >= 1
        assert all(isinstance(s, str) and s for s in row["reasons"])
    assert "current_projected" in out
    assert out["consider_starting"][0]["name"] == "Start WR"
    sit = out["consider_benching"][0]
    assert sit["name"] == "Sit WR"
    assert any("sit behind Start WR" in r for r in sit["reasons"])
    # Sit rows must not inherit the starter's matchup line.
    assert not any(r.startswith("plays ") for r in sit["reasons"])


def test_projection_failure_does_not_invent_a_lineup():
    err = start_sit.projection_failure(
        league_id="L1",
        platform="sleeper",
        team_name="Pine Bluff Escapees",
        week=1,
        season="2026",
    )
    assert err["error"] == "no projection data returned"
    assert "guidance" in err["fallback"]
    assert "get_player_stats" in err["fallback"]["try"]
    assert "optimal_lineup" not in err
    assert "consider_starting" not in err
    assert err["unofficial"] is True


def test_start_sit_advice_empty_projections_use_structured_error():
    roster = {
        "roster": {"starters": ["1"], "players": ["1"], "roster_id": 1},
        "owner": {"team_name": "X", "display_name": "Y"},
    }
    with patch("sleeper_core.start_sit.resolve_league_id", return_value="L1"):
        with patch("sleeper_core.start_sit.resolve_my_roster", return_value=roster):
            with patch(
                "sleeper_core.start_sit.get_json",
                return_value={"week": 1, "season": "2026"},
            ):
                with patch(
                    "sleeper_core.start_sit.scoring_field",
                    return_value=("pts_ppr", "PPR"),
                ):
                    with patch(
                        "sleeper_core.start_sit.projections_for",
                        return_value={},
                    ):
                        err = start_sit.start_sit_advice(week=1)
    assert err["error"] == "no projection data returned"
    assert "fallback" in err
    assert "optimal_lineup" not in err


def test_superflex_second_qb_slot_preserved():
    pool = [
        _player("qb1", "QB One", "QB", 22.0),
        _player("qb2", "QB Two", "QB", 18.0),
        _player("wr1", "WR One", "WR", 15.0),
        _player("wr2", "WR Two", "WR", 6.0),
    ]
    # Currently starting one QB + two WRs; optimal should start both QBs.
    out = _build(
        ["QB", "SUPER_FLEX", "WR"],
        pool,
        starters=["qb1", "wr1", "wr2"],
    )
    slots = {p["slot"]: p for p in out["optimal_lineup"]}
    assert slots["QB"]["position"] == "QB"
    assert slots["SUPER_FLEX"]["position"] == "QB"
    assert slots["WR"]["position"] == "WR"
    sf = next(p for p in out["consider_starting"] if p["slot"] == "SUPER_FLEX")
    assert sf["position"] == "QB"
    assert start_sit.REASON_SUPERFLEX_QB in sf["reason_codes"]
    assert any("superflex_qb_slot" in r for r in sf["reasons"])


def test_floor_strategy_prefers_safer_player_in_close_call():
    # Healthy 10.0 vs Questionable 11.0: ceiling starts Q, floor starts healthy.
    healthy = _player("rb_safe", "Safe RB", "RB", 10.0)
    dinged = _player("rb_q", "Questionable RB", "RB", 11.0, injury="QUESTIONABLE")
    floor = _build(["RB"], [healthy, dinged], starters=["rb_q"], strategy="floor")
    ceiling = _build(["RB"], [healthy, dinged], starters=["rb_safe"], strategy="ceiling")
    assert floor["consider_starting"][0]["name"] == "Safe RB"
    assert start_sit.REASON_HIGHER_FLOOR in floor["consider_starting"][0]["reason_codes"]
    assert ceiling["consider_starting"][0]["name"] == "Questionable RB"
    gain = 11.0 - 10.0
    assert gain < start_sit.CLOSE_CALL_PTS


def test_injury_on_sit_flags_injury_risk():
    pool = [
        _player("sit", "Hurt WR", "WR", 9.0, injury="QUESTIONABLE"),
        _player("start", "Healthy WR", "WR", 13.0),
    ]
    out = _build(["WR"], pool, starters=["sit"])
    start = out["consider_starting"][0]
    sit = out["consider_benching"][0]
    assert start_sit.REASON_INJURY_RISK in start["reason_codes"]
    assert start_sit.REASON_INJURY_RISK in sit["reason_codes"]
    assert any("injury_risk" in r for r in start["reasons"])


def test_injury_risk_skips_when_both_are_equally_dinged():
    pool = [
        _player("sit", "Q Sit", "WR", 9.0, injury="QUESTIONABLE"),
        _player("start", "Q Start", "WR", 13.0, injury="QUESTIONABLE"),
    ]
    out = _build(["WR"], pool, starters=["sit"])
    assert start_sit.REASON_INJURY_RISK not in out["consider_starting"][0]["reason_codes"]
    assert start_sit.REASON_HIGHER_PROJECTION in out["consider_starting"][0]["reason_codes"]


def test_spread_favorite_sets_favorable_matchup():
    start = _player("a", "Fav WR", "WR", 12.0, team="KC")
    sit = _player("b", "Sit WR", "WR", 8.0, team="CHI")
    matchups = {
        "KC": {
            "opponent": "CHI",
            "home": True,
            "spread_line": -7.0,
            "favorite": True,
            "underdog_by": None,
        }
    }
    out = _build(["WR"], [start, sit], starters=["b"], matchups=matchups)
    codes = out["consider_starting"][0]["reason_codes"]
    assert start_sit.REASON_FAVORABLE_MATCHUP in codes
    assert start_sit.REASON_HIGHER_PROJECTION in codes


def test_rb_big_underdog_sets_negative_game_script_risk():
    start = _player("a", "Dog RB", "RB", 12.0, team="NYJ")
    sit = _player("b", "Sit RB", "RB", 8.0, team="KC")
    matchups = {
        "NYJ": {
            "opponent": "KC",
            "home": False,
            "spread_line": 7.5,
            "favorite": False,
            "underdog_by": 7.5,
        }
    }
    out = _build(["RB"], [start, sit], starters=["b"], matchups=matchups)
    codes = out["consider_starting"][0]["reason_codes"]
    assert start_sit.REASON_NEGATIVE_SCRIPT in codes


def test_no_spread_omits_matchup_and_script_codes():
    start = _player("a", "WR A", "WR", 12.0, team="KC")
    sit = _player("b", "WR B", "WR", 8.0, team="CHI")
    matchups = {
        "KC": {
            "opponent": "CHI",
            "home": True,
            "spread_line": None,
            "favorite": False,
            "underdog_by": None,
        }
    }
    out = _build(["WR"], [start, sit], starters=["b"], matchups=matchups)
    codes = out["consider_starting"][0]["reason_codes"]
    assert start_sit.REASON_FAVORABLE_MATCHUP not in codes
    assert start_sit.REASON_NEGATIVE_SCRIPT not in codes
    assert start_sit.REASON_HIGHER_PROJECTION in codes


def test_player_only_fields_still_present_when_no_swap():
    pool = [
        _player("qb", "QB", "QB", 20.0),
        _player("rb", "RB", "RB", 15.0),
    ]
    out = _build(["QB", "RB"], pool, starters=["qb", "rb"])
    assert out["consider_starting"] == []
    assert out["consider_benching"] == []
    assert isinstance(out["current_projected"], (int, float))
    assert isinstance(out["optimal_projected"], (int, float))
    assert out["potential_point_gain"] == 0
    assert "optimal_lineup" in out
    assert out["verdict"]
    assert out["team"] == "Pine Bluff Escapees"
    assert out["week"] == 10
    assert out["strategy"] == "balanced"

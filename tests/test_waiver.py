from __future__ import annotations

from sleeper_core import waiver


def _player(pid, pos, name, team="GB", injury=None, depth=1, active=True):
    return pid, {
        "first_name": name.split()[0],
        "last_name": " ".join(name.split()[1:]) or name,
        "position": pos,
        "team": team,
        "injury_status": injury,
        "depth_chart_order": depth,
        "active": active,
    }


PLAYERS = dict(
    [
        _player("qb1", "QB", "Starter QB", depth=1),
        _player("qb2", "QB", "Backup QB", depth=2),
        _player("rb1", "RB", "Starter RB", depth=1),
        _player("rb2", "RB", "Bench RB", depth=2),
        _player("wr1", "WR", "Lone WR", depth=1),
        _player("te1", "TE", "Starter TE", depth=1),
        _player("taxi1", "WR", "Taxi WR", depth=3),
        _player("fa_wr", "WR", "Free Agent WR", depth=1),
        _player("fa_rb", "RB", "Free Agent RB", depth=2),
        _player("rostered_other", "WR", "Already Rostered", depth=1),
        _player("elite", "RB", "Elite RB", depth=1),
    ]
)

ROSTER_POSITIONS = [
    "QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "SUPER_FLEX",
    "BN", "BN", "BN", "TAXI",
]


def _fc(pid, value, rank=50):
    return {
        "value": value,
        "overallRank": rank,
        "player": {"sleeperId": pid, "name": PLAYERS[pid]["first_name"], "position": PLAYERS[pid]["position"]},
    }


def test_needs_flag_thin_wr_in_superflex():
    owned = ["qb1", "qb2", "rb1", "rb2", "wr1", "te1", "taxi1"]
    needs = waiver.positional_needs(
        owned=owned,
        starters=["qb1", "rb1", "wr1", "te1"],
        taxi=["taxi1"],
        reserve=[],
        players_map=PLAYERS,
        roster_positions=ROSTER_POSITIONS,
        fc_by_sid={},
    )
    by_pos = {n["position"]: n for n in needs}
    assert by_pos["WR"]["severity"] in {"high", "medium"}
    assert by_pos["QB"]["slots"] >= 2


def test_never_recommend_rostered_and_drops_prefer_taxi():
    fc_by_sid = {
        "taxi1": _fc("taxi1", 50, rank=200),
        "rb2": _fc("rb2", 80, rank=180),
        "elite": _fc("elite", 5000, rank=4),
        "wr1": _fc("wr1", 900, rank=40),
    }
    drops = waiver.suggest_drops(
        add_pid="fa_wr",
        owned=["qb1", "rb1", "wr1", "te1", "taxi1", "elite"],
        starters=["qb1", "rb1", "wr1", "te1", "elite"],
        taxi=["taxi1"],
        reserve=[],
        players_map=PLAYERS,
        fc_by_sid=fc_by_sid,
        needs=waiver.positional_needs(
            owned=["qb1", "rb1", "wr1", "te1", "taxi1", "elite"],
            starters=["qb1", "rb1", "wr1", "te1", "elite"],
            taxi=["taxi1"],
            reserve=[],
            players_map=PLAYERS,
            roster_positions=ROSTER_POSITIONS,
            fc_by_sid=fc_by_sid,
        ),
    )
    assert all(d["player_id"] != "fa_wr" for d in drops)
    assert drops[0]["player_id"] == "taxi1"
    elite_drop = next((d for d in drops if d["player_id"] == "elite"), None)
    if elite_drop:
        assert elite_drop.get("risk") == "high"


def test_score_dynasty_puts_fc_ahead_of_proj_on_equal_inputs():
    rec = {"name": "Free Agent WR", "position": "WR", "team": "GB", "player_id": "fa_wr"}
    needs = [{"position": "WR", "severity": "high", "detail": "thin", "score": 80}]
    fc_row = {"value": 2000}
    high_fc = waiver.score_candidate(
        rec=rec,
        info=PLAYERS["fa_wr"],
        fc_row=fc_row,
        trend_count=0,
        max_trend=1,
        proj_pts=2.0,
        max_proj=20.0,
        max_fc=2000,
        needs=needs,
        weights=waiver.DYNASTY_WEIGHTS,
    )
    high_proj = waiver.score_candidate(
        rec=rec,
        info=PLAYERS["fa_wr"],
        fc_row={"value": 200},
        trend_count=0,
        max_trend=1,
        proj_pts=20.0,
        max_proj=20.0,
        max_fc=2000,
        needs=needs,
        weights=waiver.DYNASTY_WEIGHTS,
    )
    assert high_fc["components"]["trade_value"] > high_fc["components"]["projection"]
    # Same player with max FC beats max projection under dynasty weights.
    assert high_fc["score"] > high_proj["score"]


def test_envelope_fields_from_advice_helper():
    from sleeper_core import advice

    env = advice.advice_envelope(
        league_id="L",
        platform="sleeper",
        fmt={"isDynasty": True, "numQbs": 2, "ppr": 1, "numTeams": 12},
        season="2026",
        week=1,
        subject=advice.subject_block(team_name="Pine Bluff Escapees", manager="JarrodLee", roster_id=1),
        verdict="ok",
        reasons=["a"],
        data_sources=["fantasycalc"],
        limitations=["caveat"],
    )
    assert env["format"]["is_dynasty"] is True
    assert env["format"]["num_qbs"] == 2
    assert env["unofficial"] is True
    assert env["limitations"]
    assert env["data_sources"]
    assert "generated_at" in env["as_of"]


def test_waiver_advice_mocked_never_adds_rostered():
    from unittest.mock import patch

    league = {
        "roster_positions": ROSTER_POSITIONS,
        "settings": {"type": 2, "waiver_budget": 100},
        "scoring_settings": {"rec": 1},
        "total_rosters": 12,
    }
    roster = {
        "roster_id": 1,
        "players": ["qb1", "qb2", "rb1", "wr1", "te1", "taxi1"],
        "starters": ["qb1", "rb1", "wr1", "te1"],
        "taxi": ["taxi1"],
        "reserve": [],
        "settings": {"waiver_budget_used": 10},
    }
    fas = [
        {"player_id": "fa_wr", "name": "Free Agent WR", "position": "WR", "team": "GB", "injury_status": None},
        {"player_id": "wr1", "name": "Should Not Appear", "position": "WR", "team": "GB"},
    ]
    fc = [_fc("fa_wr", 1500, rank=60), _fc("taxi1", 40, rank=250), _fc("wr1", 900, rank=40)]

    with patch("sleeper_core.waiver.resolve_league_id", return_value="L1"):
        with patch("sleeper_core.waiver.get_json") as gj:
            def fake_get(path, cache=False):
                if path.startswith("/league/L1/rosters"):
                    return [roster, {"roster_id": 2, "players": ["rostered_other"]}]
                if path.startswith("/league/"):
                    return league
                if path.startswith("/state/"):
                    return {"week": 1, "season": "2026"}
                if "trending" in path:
                    return [{"player_id": "fa_wr", "count": 40}]
                return {}
            gj.side_effect = fake_get
            with patch(
                "sleeper_core.waiver.resolve_my_roster",
                return_value={"roster": roster, "owner": {"team_name": "Escapees", "display_name": "Jarrod"}},
            ):
                with patch("sleeper_core.waiver.load_players", return_value=PLAYERS):
                    with patch("sleeper_core.waiver.league_format", return_value={"ppr": 1, "numQbs": 2, "numTeams": 12, "isDynasty": True}):
                        with patch("sleeper_core.waiver.fc_values", return_value=fc):
                            with patch("sleeper_core.waiver.list_free_agents", return_value=fas):
                                with patch("sleeper_core.waiver.proj.scoring_field", return_value=("pts_ppr", "PPR")):
                                    with patch("sleeper_core.waiver.proj.projections_for", return_value={"fa_wr": {"pts_ppr": 8}}):
                                        with patch("sleeper_core.waiver.proj.proj_points", return_value=8.0):
                                            result = waiver.waiver_advice()

    assert result["data_sources"]
    assert result["limitations"]
    assert result["mode"] == "dynasty"
    rec_ids = {r["player"]["player_id"] for r in result["recommendations"]}
    assert "wr1" not in rec_ids
    assert "rostered_other" not in rec_ids
    assert "fa_wr" in rec_ids
    assert result["recommendations"][0]["suggested_drops"]
    assert result["recommendations"][0]["suggested_drops"][0]["player_id"] != "fa_wr"
    assert "verdict" in result
    assert result["needs"]

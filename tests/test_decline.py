"""Offline tests for sleeper_core.decline. No network: synthetic rows only.

The golden case (decline_risk_2025) pins live output for a completed season;
these pin the arithmetic underneath it, so a wrong number fails here with a
readable message instead of as a diff in a 70-player blob.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sleeper_core import decline as D  # noqa: E402


def play(**kw):
    base = {c: "" for c in D.PBP_COLS}
    base.update(season_type="REG", game_id="G1", posteam="DEN", week="1",
                pass_attempt="0", rush_attempt="0", sack="0", two_point_attempt="0",
                complete_pass="0", fumble_lost="0", yardline_100="50")
    base.update({k: str(v) for k, v in kw.items()})
    return base


def target(rid, **kw):
    return play(pass_attempt=1, receiver_player_id=rid, **kw)


# --------------------------------------------------------------------------
# aggregate_pbp
# --------------------------------------------------------------------------


def test_ppr_points_td_and_fumble():
    rows = [
        target("A", complete_pass=1, receiving_yards=25, epa=1.0,
               yards_after_catch=10, xyac_mean_yardage=6),
        target("A", complete_pass=1, receiving_yards=8, td_player_id="A", yardline_100=8, epa=2.0),
        target("A", epa=-1.0),  # incomplete
        play(rush_attempt=1, rusher_player_id="A", rushing_yards=12),
        play(fumble_lost=1, fumbled_1_player_id="A"),
    ]
    a = D.aggregate_pbp(rows)["players"]["A"]
    # 2 rec + 33 rec yds + 12 rush yds + 1 TD - 1 fumble lost
    assert a["pts"] == pytest.approx(2 + 3.3 + 1.2 + 6 - 2)
    assert (a["tgt"], a["rec"], a["car"], a["td"]) == (3, 2, 1, 1)
    assert a["epa_sum"] / a["epa_n"] == pytest.approx(2 / 3)
    assert a["yacoe_sum"] / a["yacoe_n"] == pytest.approx(4.0)


def test_sacks_two_point_and_postseason_excluded():
    rows = [
        target("A", complete_pass=1, receiving_yards=10),
        target("A", sack=1),
        target("A", complete_pass=1, receiving_yards=2, two_point_attempt=1, td_player_id="A"),
        target("A", complete_pass=1, receiving_yards=40, season_type="POST"),
    ]
    out = D.aggregate_pbp(rows)
    assert out["players"]["A"]["tgt"] == 1
    assert out["players"]["A"]["pts"] == pytest.approx(2.0)
    assert out["team_tgt"]["DEN"] == 1


def test_expected_td_uses_league_rate_by_bucket():
    # Bucket <=2 yds: 2 targets, 1 TD league-wide -> 0.5 each.
    rows = [
        target("A", yardline_100=1, td_player_id="A", complete_pass=1),
        target("B", yardline_100=2),
        target("A", yardline_100=60),  # deep bucket, 0 TDs league-wide
    ]
    p = D.aggregate_pbp(rows)["players"]
    assert p["A"]["xtd"] == pytest.approx(0.5)
    assert p["B"]["xtd"] == pytest.approx(0.5)


def test_team_games_and_max_week():
    rows = [target("A", game_id="G1", week=1), target("A", game_id="G2", week=3),
            target("C", posteam="KC", game_id="G1", week=1)]
    out = D.aggregate_pbp(rows)
    assert out["team_games"] == {"DEN": 2, "KC": 1}
    assert out["max_week"] == 3


# --------------------------------------------------------------------------
# aggregate_snaps
# --------------------------------------------------------------------------


def test_snaps_map_pfr_dedupe_and_skip_zero():
    rows = [
        {"game_id": "G1", "game_type": "REG", "pfr_player_id": "SuttCo00", "offense_snaps": "60", "offense_pct": "0.9"},
        {"game_id": "G1", "game_type": "REG", "pfr_player_id": "SuttCo00", "offense_snaps": "60", "offense_pct": "0.9"},
        {"game_id": "G2", "game_type": "REG", "pfr_player_id": "SuttCo00", "offense_snaps": "40", "offense_pct": "0.7"},
        {"game_id": "G3", "game_type": "REG", "pfr_player_id": "SuttCo00", "offense_snaps": "0", "offense_pct": "0"},
        {"game_id": "G4", "game_type": "POST", "pfr_player_id": "SuttCo00", "offense_snaps": "50", "offense_pct": "0.8"},
        {"game_id": "G1", "game_type": "REG", "pfr_player_id": "Unknown00", "offense_snaps": "10", "offense_pct": "0.1"},
    ]
    s = D.aggregate_snaps(rows, {"SuttCo00": "00-0034348"})
    assert s == {"00-0034348": {"g": 2, "snap_pct": pytest.approx(0.8), "snaps": 100.0}}


# --------------------------------------------------------------------------
# features
# --------------------------------------------------------------------------

FILLS = {"snap_pct": 0.77, "yacoe": 0.0, "epa_t": 0.2, "age": 26.0}


def prod(**kw):
    base = dict(gsis_id="X", sleeper_id="1", name="Test Player", position="WR", team="DEN",
                season=2025, age=28.0, g=16, team_games=17, tgt=120, rec=80, car=0,
                pts=240.0, td=8, xtd=6.0, ppg=15.0, snap_pct=0.85, tps=0.15, tshare=0.24,
                epa_t=0.3, yacoe=0.5, pos_rank=10, rank_frac=10 / 36)
    base.update(kw)
    return base


def test_jump_measured_against_best_of_two_prior_seasons():
    # 2024 was the high-water mark; 2025 an injury dip. 15 PPG is a recovery.
    f, prov = D.build_features(prod(ppg=15.0), {"ppg": 9.0, "g": 6}, {"ppg": 16.0, "g": 17}, FILLS)
    assert f["d_ppg_old"] == pytest.approx(-1.0)
    assert prov["best_prior_ppg"] == "measured"


def test_prior_season_with_too_few_games_is_ignored():
    f, _ = D.build_features(prod(ppg=15.0), {"ppg": 30.0, "g": 2}, None, FILLS)
    assert f["d_ppg_old"] == 0.0
    assert f["_best_prior_ppg"] is None


def test_young_jump_is_not_a_model_input():
    f, _ = D.build_features(prod(age=23.0, ppg=18.0), {"ppg": 5.0, "g": 17}, None, FILLS)
    assert f["d_ppg_old"] == 0.0
    assert "d_ppg_young" not in D.FEATURES


def test_missing_inputs_are_imputed_with_provenance():
    f, prov = D.build_features(prod(snap_pct=None, yacoe=None, age=None), None, None, FILLS)
    assert f["snap_pct"] == 0.77 and prov["snap_pct"] == "imputed:training_median"
    assert prov["age"] == "imputed:training_median"
    assert prov["d_tshare"] == "missing:no_prior_season" and f["d_tshare"] == 0.0


def test_td_fraction_and_td_over_expected():
    f, _ = D.build_features(prod(pts=200.0, td=10, xtd=6.0, g=16), None, None, FILLS)
    assert f["td_frac"] == pytest.approx(0.3)
    assert f["td_oe_g"] == pytest.approx(0.25)


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


def head(coef_age=1.0):
    coef = {k: 0.0 for k in D.FEATURES}
    coef["age_rel"] = coef_age
    return {"intercept": 0.0, "coef": coef,
            "mean": {k: 0.0 for k in D.FEATURES}, "std": {k: 1.0 for k in D.FEATURES}}


def test_score_clips_extreme_inputs():
    feats = {k: 0.0 for k in D.FEATURES}
    feats["age_rel"] = 50.0
    risk, contrib = D.score(feats, head())
    assert contrib["age_rel"] == pytest.approx(D.Z_CLIP)
    assert risk == pytest.approx(1 / (1 + math.exp(-D.Z_CLIP)))


def test_producers_cutoff_and_min_games():
    table = {}
    for i in range(40):
        table[f"W{i}"] = prod(gsis_id=f"W{i}", ppg=40 - i, g=10)
    table["inj"] = prod(gsis_id="inj", ppg=99.0, g=3)
    out = D.producers(table, min_games=7)
    ids = [r["gsis_id"] for r in out]
    assert len(ids) == 36 and "inj" not in ids
    assert out[0]["rank_frac"] == pytest.approx(1 / 36)


def test_min_games_in_progress_scales_with_team_games():
    assert D.min_games_for(17, complete=True) == 7
    assert D.min_games_for(9, complete=False) == 5
    assert D.min_games_for(2, complete=False) == 3


def test_attach_values_by_sleeper_id_and_explicit_gap():
    players = [{"sleeper_id": "5045"}, {"sleeper_id": None}, {"sleeper_id": "999"}]
    D.attach_values(players, [{"sleeper_id": "5045", "value": 1176, "overall_rank": 180,
                               "position_rank": 68, "trend_30_day": -33}])
    assert players[0]["trade_value"]["value"] == 1176
    assert players[1]["trade_value"] is None and "no sleeper_id" in players[1]["trade_value_note"]
    assert players[2]["trade_value"] is None and "not in FantasyCalc" in players[2]["trade_value_note"]


# --------------------------------------------------------------------------
# shipped model file
# --------------------------------------------------------------------------


def test_shipped_model_matches_code_and_beats_base_rate():
    m = D.load_model()
    assert m["features"] == list(D.FEATURES)
    for name in ("1yr", "2yr"):
        h = m["heads"][name]
        assert set(h["coef"]) == set(D.FEATURES)
        cal = h["calibration"]
        assert [b["quintile"] for b in cal] == [1, 2, 3, 4, 5]
        # A refit whose riskiest quintile no longer beats the base rate is not
        # a model. Fail loudly rather than ship it.
        assert cal[-1]["decline_rate"] > m["evaluation"][name]["base_rate"] + 0.1

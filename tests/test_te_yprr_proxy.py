from __future__ import annotations

import build_benchmarks as bb


def test_te_factors_drop_licensed_and_use_yprr():
    ids = [fid for fid, _ in bb.FACTORS["TE"]]
    assert "yprr" in ids
    assert "yprr_rank" not in ids
    assert "inline_pct" not in ids
    assert dict(bb.FACTORS["TE"])["yprr"] == "nflverse:participation"


def test_participation_loop_sets_te_yprr(monkeypatch):
    """load_player_seasons must attach yprr for TE from on_pass counts."""
    season = 2024
    te_with_routes = "Travis Kelce"
    te_no_routes = "No Routes TE"

    def fake_load_route_details(s, pos):
        if pos != "TE":
            return {}
        return {
            bb._qb_name_key(te_with_routes): {"rate": 80.0, "on_pass": 300},
            bb._qb_name_key(te_no_routes): {"rate": 50.0, "on_pass": 0},
        }

    stats_rows = []
    for name, yards in ((te_with_routes, 900.0), (te_no_routes, 500.0)):
        stats_rows.append({
            "week": "1",
            "position": "TE",
            "player_display_name": name,
            "team": "KC",
            "receiving_yards": yards,
            "fantasy_points": 10,
            "fantasy_points_ppr": 12,
            "targets": 5,
            "receptions": 4,
        })

    def fake_nflverse(tag, *_args, **_kwargs):
        if tag == "stats_player":
            return stats_rows
        return []

    monkeypatch.setattr(bb, "load_route_details", fake_load_route_details)
    monkeypatch.setattr(bb, "nflverse_csv", fake_nflverse)
    monkeypatch.setattr(bb, "load_team_wins_season", lambda _s: {})
    monkeypatch.setattr(bb, "load_qb_pbp_season", lambda _s: ({}, {}))
    monkeypatch.setattr(bb, "load_ol_proxy_season", lambda _s: ({}, {}))
    monkeypatch.setattr(bb, "load_pass_epa_ranks", lambda _s: {})
    monkeypatch.setattr(bb, "load_rb_pbp_season", lambda _s: ({}, {}, {}, {}))
    monkeypatch.setattr(bb, "load_espn_qbr_season", lambda _s: {})
    monkeypatch.setattr(bb, "load_ngs_catch_pct", lambda _s: {})

    rows = bb.load_player_seasons([season])
    by_name = {r["name"]: r for r in rows if r["position"] == "TE"}

    assert by_name[te_with_routes]["yprr"] == 3.0
    assert "yprr" not in by_name[te_no_routes]

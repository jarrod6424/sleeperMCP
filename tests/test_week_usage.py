"""Week-usage: snap share, rushes/targets, dropback rate, route proxy.

Synthetic rows only. No live nflverse.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from sleeper_core import usage


def test_as_pct_treats_fraction_and_already_percent() -> None:
    assert usage.as_pct(0.91) == 91.0
    assert usage.as_pct("0.44") == 44.0
    assert usage.as_pct(90) == 90.0
    assert usage.as_pct("1") == 100.0
    assert usage.as_pct("") is None
    assert usage.as_pct("NA") is None


def test_dropback_uses_qb_dropback_and_counts_sack_scramble() -> None:
    assert usage.is_dropback({"qb_dropback": "1", "play_type": "pass"})
    assert usage.is_dropback({"qb_dropback": "1", "play_type": "run", "qb_scramble": "1"})
    assert usage.is_dropback({"pass_attempt": "0", "sack": "1", "play_type": "pass"})
    assert not usage.is_dropback({"qb_dropback": "0", "play_type": "run", "rush_attempt": "1"})
    assert not usage.is_dropback({"play_type": "qb_kneel"})


def test_designed_run_excludes_scrambles() -> None:
    scramble = {"play_type": "run", "qb_dropback": "1", "qb_scramble": "1", "rush_attempt": "1"}
    designed = {"play_type": "run", "qb_dropback": "0", "rush_attempt": "1"}
    assert not usage.is_designed_run(scramble)
    assert usage.is_designed_run(designed)


def test_team_dropback_stats_rate_and_play_keys() -> None:
    rows = [
        _pbp("PHI", "1", dropback=True),
        _pbp("PHI", "2", dropback=True, sack=True),
        _pbp("PHI", "3", dropback=False),  # designed run
        _pbp("PHI", "4", dropback=True, scramble=True),
        _pbp("DAL", "1", dropback=True),
        _pbp("DAL", "2", dropback=False),
        _pbp("DAL", "3", dropback=False),
    ]
    stats = usage.team_dropback_stats(rows)
    assert stats["PHI"]["dropbacks"] == 3
    assert stats["PHI"]["designed_runs"] == 1
    assert stats["PHI"]["dropback_rate"] == 75.0
    assert len(stats["PHI"]["dropback_plays"]) == 3
    assert stats["DAL"]["dropbacks"] == 1
    assert stats["DAL"]["designed_runs"] == 2
    assert stats["DAL"]["dropback_rate"] == pytest.approx(33.3, abs=0.05)


def test_route_proxy_is_on_field_share_of_joined_dropbacks() -> None:
    dropbacks = {
        "PHI": {("2025_10_DAL_PHI", "1"), ("2025_10_DAL_PHI", "2"),
                ("2025_10_DAL_PHI", "4"), ("2025_10_DAL_PHI", "9")},
    }
    participation = [
        _part("1", "00-WR1;00-WR2;00-QB"),
        _part("2", "00-WR1;00-WR2;00-QB"),
        _part("4", "00-WR1;00-QB"),
        # play 9 is a dropback with no participation row
        _part("3", "00-WR1;00-WR2"),  # designed run — must not count
    ]
    counts = usage.route_on_dropbacks(participation, dropbacks)
    assert counts["00-WR1"]["on_dropbacks"] == 3
    assert counts["00-WR2"]["on_dropbacks"] == 2
    assert counts["00-QB"]["on_dropbacks"] == 3
    # denominator for PHI is 3 joined dropbacks, not 4
    assert counts["00-WR1"]["team"] == "PHI"


def test_headlines_two_wrs_over_90() -> None:
    players = [
        {"name": "A.J. Brown", "position": "WR", "snap_share": 95.0, "route_proxy": 94.0},
        {"name": "DeVonta Smith", "position": "WR", "snap_share": 91.0, "route_proxy": 92.0},
        {"name": "Dallas Goedert", "position": "TE", "snap_share": 80.0, "route_proxy": 70.0},
        {"name": "Jalen Hurts", "position": "QB", "snap_share": 100.0, "route_proxy": 100.0},
    ]
    notes = usage.position_notes(players)
    assert "2 WRs over 90% snap share" in notes
    assert "2 WRs over 90% route proxy" in notes
    assert not any("QBs over 90%" in n for n in notes)


def test_qb_play_stats_splits_dropbacks_and_rush_types() -> None:
    rows = [
        _pbp("PHI", "1", dropback=True, passer_id="00-QB1"),
        _pbp("PHI", "2", dropback=True, sack=True, passer_id="00-QB1"),
        _pbp("PHI", "3", dropback=True, scramble=True, rusher_id="00-QB1"),
        _pbp("PHI", "4", dropback=False, rusher_id="00-QB1"),
        _pbp("PHI", "5", dropback=False, rusher_id="00-RB1"),
        _pbp("PHI", "6", dropback=True, passer_id="00-QB2"),
    ]
    stats = usage.qb_play_stats(rows)
    assert stats["00-QB1"]["dropbacks"] == 3
    assert stats["00-QB1"]["scramble_rushes"] == 1
    assert stats["00-QB1"]["designed_rushes"] == 1
    assert stats["00-QB2"]["dropbacks"] == 1
    assert stats["00-QB2"]["designed_rushes"] == 0
    assert "00-RB1" not in stats or stats["00-RB1"]["dropbacks"] == 0


def test_qb_dropback_share_and_rush_split_on_player() -> None:
    stats_rows = [
        _stat("00-QB1", "Starter QB", "QB", "PHI", carries="5"),
        _stat("00-QB2", "Backup QB", "QB", "PHI", carries="1"),
        _stat("00-WR1", "A.J. Brown", "WR", "PHI", targets="6", share="0.3"),
    ]
    snap_rows = [
        _snap("Starter QB", "QB", "PHI", pct="0.62", snaps="40"),
        _snap("Backup QB", "QB", "PHI", pct="0.38", snaps="25"),
        _snap("A.J. Brown", "WR", "PHI", pct="0.95", snaps="60"),
    ]
    pbp_rows = [
        _pbp("PHI", "1", dropback=True, passer_id="00-QB1"),
        _pbp("PHI", "2", dropback=True, passer_id="00-QB1"),
        _pbp("PHI", "3", dropback=True, scramble=True, rusher_id="00-QB1"),
        _pbp("PHI", "4", dropback=False, rusher_id="00-QB1"),
        _pbp("PHI", "5", dropback=True, passer_id="00-QB2"),
        _pbp("PHI", "6", dropback=True, passer_id="00-QB2"),
        _pbp("PHI", "7", dropback=False, rusher_id="00-RB1"),
    ]
    participation = [
        _part("1", "00-QB1;00-WR1"),
        _part("2", "00-QB1;00-WR1"),
        _part("3", "00-QB1;00-WR1"),
        _part("5", "00-QB2;00-WR1"),
        _part("6", "00-QB2;00-WR1"),
    ]
    out = usage.assemble_week_usage(
        season="2025", week=10, team="PHI",
        stats_rows=stats_rows, snap_rows=snap_rows,
        pbp_rows=pbp_rows, participation_rows=participation,
        min_snap_share=0,
    )
    team = out["teams"][0]
    starter = next(p for p in team["players"] if p["name"] == "Starter QB")
    backup = next(p for p in team["players"] if p["name"] == "Backup QB")
    wr = next(p for p in team["players"] if p["position"] == "WR")
    assert starter["qb_dropbacks"] == 3
    assert starter["dropback_share"] == 60.0  # 3/5
    assert starter["scramble_rushes"] == 1
    assert starter["designed_rushes"] == 1
    assert starter["rushes"] == 5  # box-score carries stay
    assert backup["qb_dropbacks"] == 2
    assert backup["dropback_share"] == 40.0
    assert backup["designed_rushes"] == 0
    assert backup["scramble_rushes"] == 0
    assert starter["qb_dropbacks"] + backup["qb_dropbacks"] == team["dropbacks"]
    assert "dropback_share" not in wr
    assert "qb_dropbacks" not in wr
    assert "PHI: QB snap split: Starter QB 62%, Backup QB 38%" in out["headlines"]
    assert "PHI: QB dropback split: Starter QB 60%, Backup QB 40%" in out["headlines"]
    assert not any("QBs over 90%" in h for h in out["headlines"])


def test_single_qb_does_not_get_a_split_headline() -> None:
    notes = usage.qb_notes([
        {"name": "Jalen Hurts", "position": "QB", "snap_share": 100.0, "dropback_share": 100.0},
    ])
    assert notes == []


def test_name_key_strips_punctuation_not_substring() -> None:
    assert usage.name_key("A.J. Brown") == usage.name_key("AJ Brown")
    assert usage.name_key("Amon-Ra St. Brown") != usage.name_key("A.J. Brown")
    assert usage.name_key("Michael Pittman Jr.") == usage.name_key("Michael Pittman")


def test_colliding_snap_names_leave_snap_share_unset() -> None:
    stats_rows = [
        _stat("00-1", "John Smith", "WR", "PHI", targets="4", share="0.2"),
        _stat("00-2", "John Smith", "RB", "PHI", carries="8", share="0.05"),
    ]
    snap_rows = [
        _snap("John Smith", "WR", "PHI", pct="0.9", snaps="50"),
    ]
    out = usage.assemble_week_usage(
        season="2025", week=10, team="PHI",
        stats_rows=stats_rows, snap_rows=snap_rows,
        pbp_rows=[], participation_rows=[],
        min_snap_share=0,
    )
    by_id = {p["name"] + p["position"]: p for t in out["teams"] for p in t["players"]}
    wr = by_id["John SmithWR"]
    rb = by_id["John SmithRB"]
    assert wr["snap_share"] is None
    assert wr["snap_share_provenance"] == "missing:not_recorded"
    assert rb["snap_share"] is None
    assert rb["snap_share_provenance"] == "missing:not_recorded"


def test_missing_participation_is_null_not_zero() -> None:
    stats_rows = [_stat("00-WR1", "A.J. Brown", "WR", "PHI", targets="8", share="0.28")]
    snap_rows = [_snap("A.J. Brown", "WR", "PHI", pct="0.95", snaps="60")]
    pbp_rows = [_pbp("PHI", "1", dropback=True), _pbp("PHI", "2", dropback=False)]
    out = usage.assemble_week_usage(
        season="2025", week=10, team="PHI",
        stats_rows=stats_rows, snap_rows=snap_rows,
        pbp_rows=pbp_rows, participation_rows=[],
        min_snap_share=0,
    )
    player = out["teams"][0]["players"][0]
    assert player["route_proxy"] is None
    assert player["route_proxy_provenance"] == "missing:not_recorded"
    assert player["targets"] == 8
    assert player["targets_provenance"] == "measured"
    assert player["rushes"] == 0
    assert player["rushes_provenance"] == "measured"
    assert player["snap_share"] == 95.0
    assert player["target_share"] == 28.0
    team = out["teams"][0]
    assert team["dropbacks"] == 1
    assert team["designed_runs"] == 1
    assert team["dropback_rate"] == 50.0
    assert team["dropbacks_with_participation"] == 0


def test_zero_on_dropbacks_is_measured() -> None:
    stats_rows = [_stat("00-RB1", "Backup Back", "RB", "PHI", carries="3", share="0.04")]
    snap_rows = [_snap("Backup Back", "RB", "PHI", pct="0.20", snaps="12")]
    pbp_rows = [_pbp("PHI", "1", dropback=True), _pbp("PHI", "2", dropback=True)]
    participation = [_part("1", "00-WR1;00-QB"), _part("2", "00-WR1;00-QB")]
    out = usage.assemble_week_usage(
        season="2025", week=10, team="PHI",
        stats_rows=stats_rows, snap_rows=snap_rows,
        pbp_rows=pbp_rows, participation_rows=participation,
        min_snap_share=0,
    )
    player = next(p for t in out["teams"] for p in t["players"] if p["name"] == "Backup Back")
    assert player["route_proxy"] == 0.0
    assert player["route_proxy_provenance"] == "measured"
    assert player["on_dropbacks"] == 0
    assert player["rushes"] == 3


def test_phi_two_wrs_end_to_end_from_rows() -> None:
    stats_rows = [
        _stat("00-WR1", "A.J. Brown", "WR", "PHI", targets="8", share="0.30"),
        _stat("00-WR2", "DeVonta Smith", "WR", "PHI", targets="7", share="0.25"),
        _stat("00-TE1", "Dallas Goedert", "TE", "PHI", targets="4", share="0.15"),
        _stat("00-QB1", "Jalen Hurts", "QB", "PHI", carries="8", share="0.02"),
    ]
    snap_rows = [
        _snap("A.J. Brown", "WR", "PHI", pct="0.95", snaps="62"),
        _snap("DeVonta Smith", "WR", "PHI", pct="0.92", snaps="60"),
        _snap("Dallas Goedert", "TE", "PHI", pct="0.81", snaps="53"),
        _snap("Jalen Hurts", "QB", "PHI", pct="1.0", snaps="65"),
    ]
    pbp_rows = [
        _pbp("PHI", "1", dropback=True, passer_id="00-QB1"),
        _pbp("PHI", "2", dropback=True, passer_id="00-QB1"),
        _pbp("PHI", "3", dropback=True, passer_id="00-QB1"),
        _pbp("PHI", "4", dropback=False, rusher_id="00-QB1"),
        _pbp("PHI", "5", dropback=False, rusher_id="00-RB"),
    ]
    participation = [
        _part("1", "00-WR1;00-WR2;00-TE1;00-QB1"),
        _part("2", "00-WR1;00-WR2;00-TE1;00-QB1"),
        _part("3", "00-WR1;00-WR2;00-QB1"),
    ]
    out = usage.assemble_week_usage(
        season="2025", week=10, team="PHI",
        stats_rows=stats_rows, snap_rows=snap_rows,
        pbp_rows=pbp_rows, participation_rows=participation,
        min_snap_share=0,
    )
    assert out["week"] == 10
    assert out["season"] == "2025"
    assert "rates are 0-100 percentages" in out["units"]
    assert any("not official PFF" in line for line in out["limitations"])
    assert "PHI: 2 WRs over 90% snap share" in out["headlines"]
    assert "PHI: 2 WRs over 90% route proxy" in out["headlines"]
    team = out["teams"][0]
    assert team["team"] == "PHI"
    assert team["dropbacks"] == 3
    assert team["designed_runs"] == 2
    assert team["dropback_rate"] == 60.0
    assert team["dropback_rate_provenance"] == "measured"
    # 3 dropbacks / (3+2) = 60; 3 of 5 offensive plays. Reconcile.
    assert team["dropbacks"] + team["designed_runs"] == 5
    wrs = [p for p in team["players"] if p["position"] == "WR"]
    assert {p["name"] for p in wrs} == {"A.J. Brown", "DeVonta Smith"}
    assert all(p["route_proxy"] == 100.0 for p in wrs)
    assert all(p["snap_share"] >= 90.0 for p in wrs)
    qb = next(p for p in team["players"] if p["position"] == "QB")
    assert qb["rushes"] == 8
    assert qb["route_proxy"] == 100.0
    assert qb["qb_dropbacks"] == 3
    assert qb["dropback_share"] == 100.0
    assert qb["designed_rushes"] == 1
    assert qb["scramble_rushes"] == 0
    assert not any("QB snap split" in h for h in out["headlines"])


def test_lar_alias_and_week_isolation() -> None:
    stats_rows = [
        _stat("00-WR1", "Puka Nacua", "WR", "LA", targets="9", share="0.32", week="10"),
        _stat("00-WR1", "Puka Nacua", "WR", "LA", targets="99", share="0.99", week="9"),
    ]
    snap_rows = [
        _snap("Puka Nacua", "WR", "LA", pct="0.88", snaps="55", week="10"),
        _snap("Puka Nacua", "WR", "LA", pct="0.10", snaps="5", week="9"),
    ]
    pbp_rows = [
        _pbp("LA", "1", dropback=True, week="10"),
        _pbp("LA", "2", dropback=True, week="9"),
    ]
    participation = [_part("1", "00-WR1")]
    out = usage.assemble_week_usage(
        season="2025", week=10, team="LAR",
        stats_rows=stats_rows, snap_rows=snap_rows,
        pbp_rows=pbp_rows, participation_rows=participation,
        min_snap_share=0,
    )
    assert out["teams"][0]["team"] == "LA"
    puka = out["teams"][0]["players"][0]
    assert puka["targets"] == 9
    assert puka["snap_share"] == 88.0
    assert out["teams"][0]["dropbacks"] == 1


def test_roster_pfr_joins_zonovan_snap_to_bam_stats() -> None:
    """Snap counts use birth name; weekly stats use the football name.

    Without a roster id join this looks like a two-back committee.
    """
    stats_rows = [_stat("00-0037157", "Bam Knight", "RB", "ARI", carries="10", targets="4", share="0.09")]
    snap_rows = [_snap("Zonovan Knight", "RB", "ARI", pct="0.46", snaps="35", pfr="KnigZo00")]
    roster_rows = [{
        "gsis_id": "00-0037157",
        "pfr_id": "KnigZo00",
        "full_name": "Bam Knight",
        "first_name": "Zonovan",
        "last_name": "Knight",
        "football_name": "Bam",
        "team": "ARI",
        "position": "RB",
    }]
    pbp_rows = [_pbp("ARI", "1", dropback=True, game="2025_10_ARI_SEA")]
    participation = [_part("1", "00-0037157;00-QB", game="2025_10_ARI_SEA")]
    out = usage.assemble_week_usage(
        season="2025", week=10, team="ARI",
        stats_rows=stats_rows, snap_rows=snap_rows,
        pbp_rows=pbp_rows, participation_rows=participation,
        roster_rows=roster_rows,
        min_snap_share=0,
    )
    rbs = [p for t in out["teams"] for p in t["players"] if p["position"] == "RB"]
    assert len(rbs) == 1
    bam = rbs[0]
    assert bam["name"] == "Bam Knight"
    assert bam["snap_share"] == 46.0
    assert bam["offense_snaps"] == 35
    assert bam["rushes"] == 10
    assert bam["targets"] == 4
    assert bam["route_proxy"] == 100.0


def test_empty_data_is_structured_error() -> None:
    out = usage.assemble_week_usage(
        season="2025", week=10, team="PHI",
        stats_rows=[], snap_rows=[], pbp_rows=[], participation_rows=[],
    )
    assert out["error"] == "no usage data"
    assert out["team"] == "PHI"
    assert out["week"] == 10
    assert "hint" in out


def test_defense_and_ol_are_omitted() -> None:
    stats_rows = [_stat("00-DT", "Jalen Carter", "DT", "PHI", targets="0", share="0")]
    snap_rows = [_snap("Jalen Carter", "DT", "PHI", pct="0.98", snaps="70")]
    out = usage.assemble_week_usage(
        season="2025", week=10, team="PHI",
        stats_rows=stats_rows, snap_rows=snap_rows,
        pbp_rows=[_pbp("PHI", "1", dropback=True)],
        participation_rows=[],
        min_snap_share=0,
    )
    assert out["error"] == "no usage data" or not out.get("teams", [{}])[0].get("players")


def test_week_usage_filters_csv_during_parse() -> None:
    """row_filter must be passed for the large pbp / participation files."""
    calls: list[tuple] = []

    def fake_csv(tag, filename, row_filter=None, ttl=None, **_kwargs):
        calls.append((tag, filename, row_filter is not None))
        if tag == "stats_player":
            return [_stat("00-WR1", "A.J. Brown", "WR", "PHI", targets="8", share="0.3")]
        if tag == "snap_counts":
            return [_snap("A.J. Brown", "WR", "PHI", pct="0.95", snaps="60")]
        if tag == "pbp":
            rows = [
                _pbp("PHI", "1", dropback=True, week="10"),
                _pbp("PHI", "9", dropback=True, week="11"),
            ]
            return [r for r in rows if row_filter is None or row_filter(r)]
        if tag == "pbp_participation":
            rows = [
                {**_part("1", "00-WR1"), "nflverse_game_id": "2025_10_DAL_PHI"},
                {**_part("9", "00-WR1"), "nflverse_game_id": "2025_11_PHI_GB"},
            ]
            return [r for r in rows if row_filter is None or row_filter(r)]
        return []

    with patch("sleeper_core.usage.nflverse_csv", side_effect=fake_csv):
        with patch("sleeper_core.usage.current_season", return_value="2025"):
            out = usage.week_usage(week=10, season="2025", team="PHI", min_snap_share=0)

    by_tag = {c[0]: c[2] for c in calls}
    assert by_tag["pbp"] is True
    assert by_tag["pbp_participation"] is True
    assert out["teams"][0]["dropbacks"] == 1
    player = out["teams"][0]["players"][0]
    assert player["on_dropbacks"] == 1
    assert player["route_proxy"] == 100.0


# --------------------------------------------------------------------------
# row builders
# --------------------------------------------------------------------------

GAME = "2025_10_DAL_PHI"


def _pbp(team, play_id, *, dropback, sack=False, scramble=False, week="10",
         game=GAME, passer_id="", rusher_id=""):
    row = {
        "posteam": team,
        "play_id": str(play_id),
        "game_id": game,
        "week": week,
        "play_type": "run" if (scramble or not dropback) else "pass",
        "qb_dropback": "1" if dropback else "0",
        "pass_attempt": "1" if dropback and not sack and not scramble else "0",
        "sack": "1" if sack else "0",
        "qb_scramble": "1" if scramble else "0",
        "rush_attempt": "1" if (scramble or not dropback) else "0",
        "season_type": "REG",
        "passer_player_id": passer_id if dropback and not scramble else "",
        "passer_id": passer_id if dropback and not scramble else "",
        "rusher_player_id": rusher_id if (scramble or not dropback) else "",
        "rusher_id": rusher_id if (scramble or not dropback) else "",
    }
    if scramble:
        row["play_type"] = "run"
        if not rusher_id and passer_id:
            row["rusher_player_id"] = passer_id
            row["rusher_id"] = passer_id
    return row


def _part(play_id, offense_players, game=GAME):
    return {
        "nflverse_game_id": game,
        "play_id": str(play_id),
        "offense_players": offense_players,
        "possession_team": "PHI",
    }


def _stat(gsis, name, pos, team, *, targets="0", carries="0", share="0", week="10"):
    return {
        "player_id": gsis,
        "player_display_name": name,
        "position": pos,
        "team": team,
        "week": week,
        "targets": targets,
        "carries": carries,
        "target_share": share,
        "season_type": "REG",
    }


def _snap(name, pos, team, *, pct, snaps, week="10", pfr=""):
    return {
        "player": name,
        "position": pos,
        "team": team,
        "week": week,
        "offense_pct": pct,
        "offense_snaps": snaps,
        "pfr_player_id": pfr,
    }

from __future__ import annotations

from sleeper_core import grade


def test_owned_picks_applies_trades():
    # Roster 1 originally owns every 2027 pick; traded away the 1st to 11;
    # received 11's 2027 3rd.
    traded = [
        {"season": "2027", "round": 1, "roster_id": 1, "owner_id": 11, "previous_owner_id": 1},
        {"season": "2027", "round": 3, "roster_id": 11, "owner_id": 1, "previous_owner_id": 11},
    ]
    owned = grade.owned_picks(1, traded, ["2027"], rounds=4)
    firsts = [p for p in owned if p["round"] == 1]
    thirds = [p for p in owned if p["round"] == 3]
    assert not any(p["original_roster_id"] == 1 for p in firsts)
    assert any(p["original_roster_id"] == 11 and p["round"] == 3 for p in thirds)
    # Still own original 2nd and 4th.
    assert any(p["round"] == 2 and p["original_roster_id"] == 1 for p in owned)


def test_every_fixture_team_classified():
    n = 12
    labels = []
    for rank in range(1, n + 1):
        labels.append(
            grade.classify(
                value_rank=rank,
                starter_rank=rank,
                pick_rank=n - rank + 1,
                n=n,
                horizon="dynasty",
                pick_capital_value=5000 if rank >= 10 else 1000,
                median_picks=2000,
            )
        )
    assert len(labels) == 12
    assert all(lab in grade.CLASSIFICATIONS for lab in labels)
    assert labels[0] == "championship_contender"
    assert labels[-1] in {"tank", "rebuilder"}


def test_horizon_win_now_uses_starter_rank():
    # Great starters, terrible rest-of-roster / picks → still a contender win-now,
    # rebuilder on dynasty if value rank is also bad? value_rank 10 with starter 1.
    win_now = grade.classify(
        value_rank=10,
        starter_rank=1,
        pick_rank=12,
        n=12,
        horizon="win_now",
        pick_capital_value=0,
        median_picks=1000,
    )
    dynasty = grade.classify(
        value_rank=10,
        starter_rank=1,
        pick_rank=12,
        n=12,
        horizon="dynasty",
        pick_capital_value=0,
        median_picks=1000,
    )
    assert win_now in {"championship_contender", "playoff_hopeful"}
    assert dynasty in {"rebuilder", "tank", "mid_pack"}


def test_next_moves_length_and_reasons():
    snap = {
        "total_value": 40000,
        "pick_capital": 2800,
        "pos_value": {"QB": 10, "RB": 8, "WR": 2, "TE": 5},
        "pos_names": {"QB": ["Q"], "RB": ["R1", "R2"], "WR": ["W"], "TE": ["T"]},
        "roster_id": 1,
    }
    pos_grades = {
        "QB": {"grade": "A", "score": 90, "players": ["Q"], "detail": "Q"},
        "RB": {"grade": "A-", "score": 85, "players": ["R1", "R2"], "detail": "R"},
        "WR": {"grade": "D", "score": 20, "players": ["W"], "detail": "W"},
        "TE": {"grade": "B", "score": 70, "players": ["T"], "detail": "T"},
        "picks": {"grade": "B", "score": 70, "detail": "own 2027 1st"},
    }
    moves = grade._next_moves(snap, "playoff_hopeful", pos_grades, "dynasty")
    assert 1 <= len(moves) <= 3
    for m in moves:
        assert m["reasons"]
        assert m["advice"]
        assert m["type"] in {"trade", "fa", "hold"}

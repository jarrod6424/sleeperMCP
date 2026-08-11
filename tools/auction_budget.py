"""
Auction dollar targets from FantasyCalc values (superflex-aware).

    python tools/auction_budget.py
    python tools/auction_budget.py --league 1385305178642608128
    python tools/auction_budget.py --league 1385305178642608128 --limit 40
    python tools/auction_budget.py --league 1385305178642608128 --position QB

Writes nothing by default — prints name|pos|fair|max to stdout.

Also exposed as MCP tool get_auction_budgets in server.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sleeper_core.auction import price_board

# Lazy: auction_budgets pulls FantasyCalc/httpx; only needed for live --league.
def _auction_budgets(*args, **kwargs):
    from sleeper_core.auction import auction_budgets

    return auction_budgets(*args, **kwargs)

# Snapshot used when --offline is set (The Dropouts SF PPR board, Aug 2026).
# Prefer live --league fetches; this is a fallback for offline smoke checks.
_OFFLINE_PLAYERS = [
    ("Josh Allen", "QB", 10423),
    ("Bijan Robinson", "RB", 9965),
    ("Jahmyr Gibbs", "RB", 9783),
    ("Ja'Marr Chase", "WR", 9762),
    ("Lamar Jackson", "QB", 9377),
    ("Joe Burrow", "QB", 9115),
    ("Drake Maye", "QB", 9086),
    ("Jayden Daniels", "QB", 8046),
    ("Puka Nacua", "WR", 7961),
    ("Caleb Williams", "QB", 7844),
    ("Jaxon Smith-Njigba", "WR", 7825),
    ("Amon-Ra St. Brown", "WR", 7799),
    ("Patrick Mahomes", "QB", 7351),
    ("Justin Herbert", "QB", 7245),
    ("Justin Jefferson", "WR", 7236),
    ("Jalen Hurts", "QB", 6888),
    ("CeeDee Lamb", "WR", 6745),
    ("Trey McBride", "TE", 6656),
    ("Jonathan Taylor", "RB", 6649),
    ("Christian McCaffrey", "RB", 6585),
    ("Ashton Jeanty", "RB", 6195),
    ("James Cook", "RB", 6175),
    ("Brock Bowers", "TE", 6105),
    ("De'Von Achane", "RB", 6010),
    ("Dak Prescott", "QB", 5943),
    ("Trevor Lawrence", "QB", 5893),
    ("Bo Nix", "QB", 5531),
    ("Omarion Hampton", "RB", 5512),
    ("Brock Purdy", "QB", 5281),
    ("Drake London", "WR", 5205),
    ("Saquon Barkley", "RB", 5081),
    ("Jeremiyah Love", "RB", 5003),
    ("Jaxson Dart", "QB", 4962),
    ("Jared Goff", "QB", 4917),
    ("Malik Nabers", "WR", 4799),
    ("Jordan Love", "QB", 4793),
    ("Baker Mayfield", "QB", 4676),
    ("Derrick Henry", "RB", 4603),
    ("Kenneth Walker", "RB", 4479),
    ("Chase Brown", "RB", 4311),
    ("Matthew Stafford", "QB", 4262),
    ("Nico Collins", "WR", 4142),
    ("A.J. Brown", "WR", 4033),
    ("George Pickens", "WR", 3992),
    ("Tyler Shough", "QB", 3451),
    ("Breece Hall", "RB", 3381),
    ("Tyler Warren", "TE", 3376),
    ("Garrett Wilson", "WR", 3366),
    ("Cam Ward", "QB", 3363),
    ("Chris Olave", "WR", 3363),
    ("DeVonta Smith", "WR", 3230),
    ("Tetairoa McMillan", "WR", 2990),
    ("Colston Loveland", "TE", 2983),
    ("Emeka Egbuka", "WR", 2913),
    ("Kyren Williams", "RB", 2821),
    ("Rashee Rice", "WR", 2819),
    ("Kyler Murray", "QB", 2775),
    ("Jaylen Waddle", "WR", 2726),
    ("C.J. Stroud", "QB", 2705),
    ("Sam Darnold", "QB", 2686),
    ("Josh Jacobs", "RB", 2664),
    ("Javonte Williams", "RB", 2626),
    ("Zay Flowers", "WR", 2555),
    ("Tee Higgins", "WR", 2540),
    ("Ladd McConkey", "WR", 2519),
    ("TreVeyon Henderson", "RB", 2386),
    ("Cam Skattebo", "RB", 2368),
    ("Quinshon Judkins", "RB", 2238),
    ("Travis Etienne", "RB", 2192),
    ("Bucky Irving", "RB", 2028),
    ("Davante Adams", "WR", 2028),
    ("Tucker Kraft", "TE", 1985),
    ("Daniel Jones", "QB", 1982),
    ("David Montgomery", "RB", 1790),
    ("George Kittle", "TE", 1743),
    ("Bryce Young", "QB", 1720),
    ("Jadarian Price", "RB", 1718),
    ("Malik Willis", "QB", 1706),
]

# The Dropouts 2026 auction (darknegan).
DEFAULT_LEAGUE_ID = "1385305178642608128"


def _print_table(players: list[dict]) -> None:
    for p in players:
        print(f"{p['name']}|{p['position']}|{p['fair']}|{p['max']}")
    print(f"SUM_FAIR|{sum(p['fair'] for p in players)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="FantasyCalc → auction $ fair/max bid targets",
    )
    parser.add_argument(
        "--league",
        default=DEFAULT_LEAGUE_ID,
        help=f"Sleeper league id (default: The Dropouts {DEFAULT_LEAGUE_ID})",
    )
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--position", default=None, help="QB / RB / WR / TE")
    parser.add_argument(
        "--ceiling-pct",
        type=float,
        default=0.12,
        help="Max bid stretch above fair (default 0.12)",
    )
    parser.add_argument(
        "--tail-mass",
        type=float,
        default=0.18,
        help="Assumed value mass of unlisted depth (default 0.18)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use the hardcoded Dropouts snapshot instead of live APIs",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full result dict as JSON",
    )
    args = parser.parse_args()

    if args.offline:
        rows = [
            {"name": n, "position": p, "value": v, "team": None, "sleeper_id": None}
            for n, p, v in _OFFLINE_PLAYERS
        ]
        if args.position:
            pos = args.position.upper()
            rows = [r for r in rows if r["position"] == pos]
        priced = price_board(
            rows,
            budget=200,
            num_teams=12,
            roster_spots=15,
            ceiling_pct=args.ceiling_pct,
            tail_mass=args.tail_mass,
            limit=args.limit,
        )
        result = {
            "league_id": None,
            "league_name": "offline snapshot (The Dropouts shape)",
            "budget": 200,
            "num_teams": 12,
            "roster_spots": 15,
            "players": priced,
            "sum_fair": sum(p["fair"] for p in priced),
        }
    else:
        result = _auction_budgets(
            args.league,
            limit=args.limit,
            ceiling_pct=args.ceiling_pct,
            tail_mass=args.tail_mass,
            position=args.position,
        )
        if result.get("error"):
            print(json.dumps(result, indent=2), file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        meta = (
            f"# {result.get('league_name')}  "
            f"budget=${result.get('budget')}  "
            f"teams={result.get('num_teams')}  "
            f"spots={result.get('roster_spots')}"
        )
        print(meta, file=sys.stderr)
        _print_table(result["players"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

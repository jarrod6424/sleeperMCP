"""
Refuse to publish player_factors.json on a sharp universe drop.

    python tools/check_artifact_count.py \\
      --new artifacts/player_factors.json \\
      --baseline previous.json

Exit 0 if OK (or no baseline). Exit 1 if new count < ratio * baseline count.
Default ratio: 0.85 (same as data_api MIN_PLAYER_COUNT_RATIO).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def player_count(doc: dict) -> int | None:
    counts = doc.get("counts") or {}
    if isinstance(counts.get("players"), int):
        return counts["players"]
    players = doc.get("players")
    if isinstance(players, list):
        return len(players)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", required=True, type=Path)
    ap.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Previous artifact (R2 download or committed artifacts/). "
             "If missing, the check is skipped.",
    )
    ap.add_argument("--ratio", type=float, default=0.85)
    args = ap.parse_args()

    if not args.new.is_file():
        print(f"ERROR: new artifact not found: {args.new}", file=sys.stderr)
        return 1

    new_doc = json.loads(args.new.read_text(encoding="utf-8"))
    new_n = player_count(new_doc)
    if new_n is None:
        print("ERROR: new artifact has no player count", file=sys.stderr)
        return 1

    if args.baseline is None or not args.baseline.is_file():
        print(f"OK: no baseline; new count={new_n} (sanity check skipped)")
        return 0

    prev_doc = json.loads(args.baseline.read_text(encoding="utf-8"))
    prev_n = player_count(prev_doc)
    if prev_n is None or prev_n <= 0:
        print(f"OK: baseline unreadable/empty; new count={new_n}")
        return 0

    floor = prev_n * args.ratio
    if new_n < floor:
        print(
            f"ERROR: refusing publish — player count dropped "
            f"{prev_n} -> {new_n} (min {floor:.0f} at ratio {args.ratio})",
            file=sys.stderr,
        )
        return 1

    print(f"OK: player count {prev_n} -> {new_n} (ratio floor {floor:.0f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

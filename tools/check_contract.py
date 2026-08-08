"""
Has DraftLab's factor contract drifted from what we export?

    python tools/check_contract.py ../fantasy-football-draft-optimizer
    python tools/check_contract.py C:\\Code\\draftlab --verbose

Exit code 0 if the contract still holds, 1 if anything moved.

WHY
---
Everything in build_factors.py targets the factor IDs in FACTORS, which were
read out of DraftLab's engine at a point in time and never confirmed since. If
he renames `off_ppg_rank` to `offense_ppg_rank`, nothing here errors — the
export simply produces a field his engine ignores, and his engine reads a field
we never send. Both sides look healthy and the player grades silently lose a
factor.

That is the same failure this project keeps hitting: plausible output, no
error, wrong answer. This turns "I should ask him what changed" into a check
that takes a second and can run before every export.

HEURISTIC, AND SAYS SO
----------------------
It does not parse TypeScript. It scans the repo for our known factor IDs, and
separately harvests identifier-shaped strings that appear near factor-ish
context, so a renamed or added factor shows up as "in his repo, not in ours".
That will produce some noise. Noise you can skim beats a silent mismatch, but
do not treat the NEW list as authoritative — read it, do not act on it blindly.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_benchmarks import COMPUTABLE, FACTORS      # noqa: E402

CODE_EXT = {".ts", ".tsx", ".js", ".jsx", ".json", ".mts", ".cts"}
SKIP_DIR = {"node_modules", ".git", "dist", "build", ".next", ".angular",
            "coverage", "__pycache__", ".venv", "out"}

# Identifier-shaped strings sitting next to something factor-related. Loose on
# purpose: missing a rename is far worse than printing a few false candidates.
CONTEXT = re.compile(
    r"""(?:factor|benchmark|ceiling|weight)[A-Za-z_]*\s*[:=(\[]|['"]factorId['"]\s*:""",
    re.I)
IDENT = re.compile(r"""['"]([a-z][a-z0-9]*(?:_[a-z0-9]+){1,4})['"]""")


def scan(root: Path) -> tuple[dict, dict, int]:
    """-> (our_id -> [files], candidate_id -> [files], files_scanned)"""
    ours = {f for fs in FACTORS.values() for f, _ in fs}
    found: dict[str, list[str]] = defaultdict(list)
    candidates: dict[str, list[str]] = defaultdict(list)
    n = 0

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in CODE_EXT:
            continue
        if any(part in SKIP_DIR for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        n += 1
        rel = str(path.relative_to(root))

        for fid in ours:
            if re.search(rf"['\"]{re.escape(fid)}['\"]", text):
                found[fid].append(rel)

        for line in text.splitlines():
            if not CONTEXT.search(line):
                continue
            for m in IDENT.finditer(line):
                ident = m.group(1)
                if ident not in ours:
                    candidates[ident].append(rel)

    return found, candidates, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", help="path to a local clone of DraftLab")
    ap.add_argument("--verbose", action="store_true", help="show file locations")
    args = ap.parse_args()

    root = Path(args.repo).expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return 2

    found, candidates, n = scan(root)
    print(f"Scanned {n} source files under {root}\n")

    drift = False
    for pos, factors in FACTORS.items():
        ids = [f for f, _ in factors]
        missing = [f for f in ids if f not in found]
        print(f"{pos}  {len(ids) - len(missing)}/{len(ids)} factor ids present")
        if missing:
            drift = True
            for f in missing:
                tag = "  (we export this)" if f in COMPUTABLE else ""
                print(f"     NOT FOUND  {f}{tag}")

    if candidates:
        print(f"\nfactor-ish ids in his repo that are not in ours ({len(candidates)}):")
        for ident, files in sorted(candidates.items(),
                                   key=lambda kv: -len(kv[1]))[:25]:
            where = f"  [{files[0]}]" if args.verbose else ""
            print(f"   {ident}{where}")
        print("   Heuristic — read these, do not act on them blindly.")

    print()
    if drift:
        print("DRIFT: at least one factor id we export was not found in his repo.")
        print("Either it was renamed, or the engine no longer uses it. Until that")
        print("is resolved, player_factors.json may contain fields his engine")
        print("ignores and omit fields it expects — with no error on either side.")
        return 1

    print("Contract holds: every factor id we export appears in his repo.")
    print("Presence is not the same as identical semantics, but a rename or a")
    print("removal would have shown up here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

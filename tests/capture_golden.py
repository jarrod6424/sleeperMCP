"""
Capture the golden baseline for every tool in server.py.

    python tests/capture_golden.py

Writes tests/golden.json. Commit that file — it is the baseline every later
run is compared against.

Requires live network access to Sleeper, FantasyCalc and GitHub (nflverse).
On a corporate network you will also need USE_OS_TRUSTSTORE=1.

Re-capture (and re-commit) whenever you intentionally change a tool's output,
or when the underlying league data has moved on enough that the old baseline
is no longer a fair comparison. Do NOT re-capture to make a failing diff go
away — that is the diff doing its job.
"""

from __future__ import annotations

import json
import sys
import time

from cases import (  # noqa: E402  (sys.path is set up inside cases)
    GOLDEN_PATH,
    PLAYER_A,
    PLAYER_B,
    SEASON,
    TEAM,
    WEEK,
    build_cases,
    normalize,
    resolve_fixtures,
    run_case,
)


def main() -> int:
    print("Resolving fixtures (draft_id, player_id)...")
    fixtures = resolve_fixtures()
    for key, value in fixtures.items():
        print(f"  {key}: {value}")

    cases = build_cases(fixtures)
    print(f"\nCapturing {len(cases)} cases "
          f"(season={SEASON} week={WEEK} team={TEAM})\n")

    results: dict[str, dict] = {}
    errors = 0
    started = time.time()

    for case_id, tool_name, kwargs, mode in cases:
        t0 = time.time()
        raw = run_case(tool_name, kwargs)
        elapsed = time.time() - t0

        failed = isinstance(raw, dict) and "__error__" in raw
        errors += failed

        results[case_id] = {
            "tool": tool_name,
            "kwargs": kwargs,
            "mode": mode,
            "value": normalize(raw, mode),
        }

        flag = "ERR " if failed else "    "
        print(f"{flag}{case_id:<24} {mode:<6} {elapsed:5.2f}s"
              + (f"  {raw['__error__']}" if failed else ""))

    payload = {
        "_meta": {
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "season": SEASON,
            "week": WEEK,
            "team": TEAM,
            "player_a": PLAYER_A,
            "player_b": PLAYER_B,
            "fixtures": fixtures,
            "case_count": len(cases),
        },
        "cases": results,
    }

    GOLDEN_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )

    size_kb = GOLDEN_PATH.stat().st_size / 1024
    print(f"\nWrote {GOLDEN_PATH} ({size_kb:.0f} KB) in {time.time() - started:.1f}s")

    if errors:
        print(f"\n{errors} case(s) errored. That is not necessarily a problem —")
        print("an error captured now is compared against the same error later.")
        print("But check they are the endpoints you expect to be unavailable.")

    print("\nNext: git add tests/golden.json && git commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Compare current tool output against the committed golden baseline.

    pytest tests/test_golden.py            # all cases
    pytest tests/test_golden.py -k rosters # one case
    pytest tests/test_golden.py -x         # stop at first regression

Run this after every commit of the sleeper_core extraction. An empty diff
means the move did not change behaviour and you can keep going.

If golden.json does not exist yet, run tests/capture_golden.py first.
"""

from __future__ import annotations

import json

import pytest

from cases import (
    GOLDEN_PATH,
    SHAPE,
    build_cases,
    find_diffs,
    mask_volatile,
    normalize,
    run_case,
)

if not GOLDEN_PATH.exists():
    pytest.skip(
        "No tests/golden.json baseline. Run: python tests/capture_golden.py",
        allow_module_level=True,
    )

GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
CASES = GOLDEN["cases"]

# Reuse the fixtures the baseline was captured with rather than re-resolving.
# Re-resolving could pick a different draft and produce a diff that is about
# the fixture, not about your refactor.
FIXTURES = GOLDEN["_meta"]["fixtures"]
CASE_IDS = [cid for cid, *_ in build_cases(FIXTURES) if cid in CASES]


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_tool_output_unchanged(case_id: str) -> None:
    expected_entry = CASES[case_id]
    tool_name = expected_entry["tool"]
    kwargs = expected_entry["kwargs"]
    mode = expected_entry["mode"]

    actual = normalize(run_case(tool_name, kwargs), mode)

    # Round-trip through JSON so tuples/ints/etc. compare the way they were
    # stored, not the way Python happens to hold them in memory.
    actual = json.loads(json.dumps(actual, sort_keys=True, default=str))
    expected = expected_entry["value"]

    # Applied to both sides so a baseline captured before this existed still
    # compares correctly — no re-capture needed.
    if mode == SHAPE:
        expected = mask_volatile(expected)
        actual = mask_volatile(actual)

    if actual == expected:
        return

    diffs = find_diffs(expected, actual)
    detail = "\n".join(f"  {d}" for d in diffs) or "  (structures differ)"
    pytest.fail(
        f"{case_id} ({tool_name}, mode={mode}) changed:\n{detail}\n\n"
        f"If this change is intentional, re-run tests/capture_golden.py "
        f"and commit the new baseline.",
        pytrace=False,
    )


def test_all_cases_covered() -> None:
    """Guard against a tool silently dropping out of the case list."""
    import server

    tools = {
        name
        for name in dir(server)
        if name.startswith(("get_", "search_", "scout_", "recent_", "value_",
                            "analyze_", "start_", "score_"))
        and callable(getattr(server, name))
        and not name.startswith("_")
    }
    covered = {entry["tool"] for entry in CASES.values()}
    missing = tools - covered

    assert not missing, (
        f"Tools with no golden case: {sorted(missing)}. "
        f"Add them to build_cases() in tests/cases.py."
    )

"""Unit tests for tools/check_artifact_count.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "check_artifact_count.py"


def _write(path: Path, players: int) -> None:
    path.write_text(
        json.dumps({"counts": {"players": players}, "players": [{}] * players}),
        encoding="utf-8",
    )


def test_ok_when_no_baseline(tmp_path: Path):
    new = tmp_path / "new.json"
    _write(new, 100)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--new", str(new)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0


def test_ok_when_count_stable(tmp_path: Path):
    new = tmp_path / "new.json"
    base = tmp_path / "base.json"
    _write(new, 180)
    _write(base, 200)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--new", str(new), "--baseline", str(base)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0


def test_fail_on_sharp_drop(tmp_path: Path):
    new = tmp_path / "new.json"
    base = tmp_path / "base.json"
    _write(new, 50)
    _write(base, 200)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--new", str(new), "--baseline", str(base)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 1
    assert "refusing publish" in r.stderr

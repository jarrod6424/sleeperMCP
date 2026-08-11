"""
DraftLab data job — authenticated HTTP over the same builders as the CLIs.

    uvicorn data_api.app:app --host 0.0.0.0 --port 8080

Not the Horizon MCP server. DraftLab calls this, caches in R2 for ~7 days.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

# Opt-in corporate TLS trust (same as builders / server.py).
if os.environ.get("USE_OS_TRUSTSTORE"):
    import truststore

    truststore.inject_into_ssl()

from build_benchmarks import build_benchmarks_artifact  # noqa: E402
from build_factors import (  # noqa: E402
    EmptyUniverseError,
    build_player_factors_artifact,
)

ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", str(ROOT / "artifacts")))
FACTORS_PATH = ARTIFACTS_DIR / "player_factors.json"
BENCHMARKS_PATH = ARTIFACTS_DIR / "benchmarks.json"

# Refuse to publish if the new universe is below this fraction of the previous.
MIN_PLAYER_COUNT_RATIO = float(os.environ.get("MIN_PLAYER_COUNT_RATIO", "0.85"))

_lock = threading.Lock()

app = FastAPI(
    title="sleeperMCP DraftLab data job",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)


def _expected_token() -> str:
    token = os.environ.get("DRAFTLAB_DATA_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="DRAFTLAB_DATA_TOKEN is not configured on this host",
        )
    return token


def require_bearer(authorization: str | None = Header(default=None)) -> None:
    expected = _expected_token()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    got = authorization.removeprefix("Bearer ").strip()
    if got != expected:
        raise HTTPException(status_code=401, detail="invalid bearer token")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tmp.replace(path)


def _player_count(doc: dict[str, Any] | None) -> int | None:
    if not doc:
        return None
    counts = doc.get("counts") or {}
    if isinstance(counts.get("players"), int):
        return counts["players"]
    players = doc.get("players")
    if isinstance(players, list):
        return len(players)
    return None


def _assert_player_count_ok(new_doc: dict[str, Any], previous: dict[str, Any] | None) -> None:
    prev_n = _player_count(previous)
    new_n = _player_count(new_doc)
    if prev_n is None or new_n is None or prev_n <= 0:
        return
    if new_n < prev_n * MIN_PLAYER_COUNT_RATIO:
        raise HTTPException(
            status_code=409,
            detail=(
                f"refusing to publish player_factors: count dropped "
                f"{prev_n} -> {new_n} (min ratio {MIN_PLAYER_COUNT_RATIO})"
            ),
        )


def _get_or_build(
    *,
    path: Path,
    refresh: bool,
    builder: Callable[[], dict[str, Any]],
    validate: Callable[[dict[str, Any], dict[str, Any] | None], None] | None = None,
) -> dict[str, Any]:
    with _lock:
        existing = _read_json(path)
        if existing is not None and not refresh:
            return existing
        try:
            doc = builder()
        except EmptyUniverseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — surface upstream failures
            raise HTTPException(
                status_code=502,
                detail=f"rebuild failed: {type(exc).__name__}: {exc}",
            ) from exc
        if validate is not None:
            validate(doc, existing)
        _write_json(path, doc)
        return doc


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}


@app.get("/v1/player-factors")
def player_factors(
    refresh: bool = Query(default=False, description="Force rebuild"),
    _: None = Depends(require_bearer),
) -> JSONResponse:
    doc = _get_or_build(
        path=FACTORS_PATH,
        refresh=refresh,
        builder=lambda: build_player_factors_artifact(report=False),
        validate=_assert_player_count_ok,
    )
    return JSONResponse(content=doc)


@app.get("/v1/benchmarks")
def benchmarks(
    refresh: bool = Query(default=False, description="Force rebuild"),
    _: None = Depends(require_bearer),
) -> JSONResponse:
    doc = _get_or_build(
        path=BENCHMARKS_PATH,
        refresh=refresh,
        builder=build_benchmarks_artifact,
    )
    return JSONResponse(content=doc)

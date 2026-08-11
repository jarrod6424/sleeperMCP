"""Unit tests for the DraftLab data job (no live nflverse)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DRAFTLAB_DATA_TOKEN", "test-token")
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("MIN_PLAYER_COUNT_RATIO", "0.85")
    import importlib
    import data_api.app as app_mod

    importlib.reload(app_mod)
    return app_mod


@pytest.fixture()
def client(api_env):
    return TestClient(api_env.app)


def test_healthz_no_auth(client: TestClient):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_factors_requires_auth(client: TestClient):
    r = client.get("/v1/player-factors")
    assert r.status_code == 401


def test_factors_serves_disk_without_rebuild(
    client: TestClient, api_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    doc = {
        "schema_version": 4,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "counts": {"players": 100},
        "players": [{"name": "A"}] * 100,
    }
    (tmp_path / "player_factors.json").write_text(json.dumps(doc), encoding="utf-8")

    def boom(**_kwargs):
        raise AssertionError("should not rebuild when disk cache exists")

    monkeypatch.setattr(api_env, "build_player_factors_artifact", boom)

    r = client.get(
        "/v1/player-factors",
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 200
    assert r.json()["counts"]["players"] == 100


def test_factors_refresh_refuses_sharp_drop(
    client: TestClient, api_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    previous = {
        "schema_version": 4,
        "counts": {"players": 200},
        "players": [{"name": "A"}] * 200,
    }
    (tmp_path / "player_factors.json").write_text(json.dumps(previous), encoding="utf-8")

    def tiny(**_kwargs):
        return {
            "schema_version": 4,
            "counts": {"players": 50},
            "players": [{"name": "B"}] * 50,
        }

    monkeypatch.setattr(api_env, "build_player_factors_artifact", tiny)

    r = client.get(
        "/v1/player-factors",
        params={"refresh": "1"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 409
    kept = json.loads((tmp_path / "player_factors.json").read_text(encoding="utf-8"))
    assert kept["counts"]["players"] == 200


def test_benchmarks_builds_when_missing(
    client: TestClient, api_env, monkeypatch: pytest.MonkeyPatch
):
    payload = {
        "schema_version": 2,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "benchmarks": {"QB": {"computed": 1, "total": 1, "factors": []}},
    }

    monkeypatch.setattr(api_env, "build_benchmarks_artifact", lambda: payload)

    r = client.get(
        "/v1/benchmarks",
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 200
    assert r.json()["schema_version"] == 2
    assert (Path(api_env.BENCHMARKS_PATH)).is_file()

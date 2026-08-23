"""Web dashboard: token auth is mandatory, data routes serve the state
dir read-only, control routes are the same file-writes as the MCP
monitor (kill / full-size / policy freeze)."""

from __future__ import annotations

import json

from starlette.testclient import TestClient

from skyfire_sol.monitoring.web import build_app

TOKEN = "test-token-0123456789"


def client(tmp_path) -> TestClient:
    (tmp_path / "heartbeat.json").write_text(json.dumps(
        {"ts": "2026-08-23T00:00:00+00:00", "state": "live", "nav_usd": 100.0}))
    (tmp_path / "snapshot.json").write_text(json.dumps(
        {"nav_usd": 100.0, "allocations_target": {"MEME_ROTATION": 25.0},
         "safety": {"breaker": "armed"}, "positions": []}))
    return TestClient(build_app(str(tmp_path), TOKEN))


def auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_api_requires_token(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/api/kill", json={"reason": "x"}).status_code == 401
    assert c.get("/").status_code == 200            # shell page itself is inert


def test_status_and_positions(tmp_path):
    c = client(tmp_path)
    s = c.get("/api/status", headers=auth()).json()
    assert s["nav_usd"] == 100.0
    assert s["safety"]["breaker"] == "armed"
    assert s["kill_engaged"] is False
    assert c.get("/api/positions", headers=auth()).json() == []


def test_controls_write_the_state_files(tmp_path):
    c = client(tmp_path)
    r = c.post("/api/kill", json={"reason": "test halt"}, headers=auth())
    assert r.status_code == 200 and (tmp_path / "KILL").exists()
    assert "test halt" in (tmp_path / "KILL").read_text()

    c.post("/api/go_full_size", headers=auth())
    assert (tmp_path / "FULL_SIZE").exists()
    c.post("/api/back_to_probation", headers=auth())
    assert not (tmp_path / "FULL_SIZE").exists()

    c.post("/api/freeze_policy", headers=auth())
    assert (tmp_path / "POLICY_FREEZE").exists()
    c.post("/api/unfreeze_policy", headers=auth())
    assert not (tmp_path / "POLICY_FREEZE").exists()

    s = c.get("/api/status", headers=auth()).json()
    assert s["kill_engaged"] is True                # kill persists until deleted

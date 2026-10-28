"""Gap 3: the ClauseOS UI (`apps/clauseos_web/static/`) mounted onto the
FastAPI app at `/ui` via `fastapi.staticfiles.StaticFiles`. These tests
prove the mount actually works and serves real files -- not just that the
directory exists -- using FastAPI's TestClient, matching the brief's
"or write a lightweight test using FastAPI's TestClient hitting the
mounted static route" option (chosen over spinning up a real uvicorn
process for the automated suite; a live uvicorn/manual-fetch check was
also performed separately, see product README)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from clauseos_api.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_ui_index_loads_and_references_the_real_api():
    client = _client()
    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "ClauseOS" in resp.text
    assert "app.js" in resp.text
    assert "styles.css" in resp.text


def test_ui_javascript_asset_serves_and_targets_the_mounted_api_paths():
    client = _client()
    resp = client.get("/ui/app.js")
    assert resp.status_code == 200
    body = resp.text
    # Spot-check that the JS actually calls the real, mounted API endpoints
    # (relative paths, since the UI and API share one origin) rather than
    # some placeholder/mock URL.
    for expected_path in (
        "/v1/bundles",
        "/v1/assertions",
        "/v1/policy-releases",
        ":analyze",
        "/documents",
        "maker-decision",
        "checker-decision",
        "audit-events",
    ):
        assert expected_path in body, f"expected {expected_path!r} to appear in app.js"


def test_ui_stylesheet_serves():
    client = _client()
    resp = client.get("/ui/styles.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]


def test_ui_mount_does_not_shadow_or_break_existing_api_routes():
    client = _client()
    resp = client.post("/v1/bundles", json={"scenario_id": "SIM-SCEN-CLAUSEOS-0001", "actor_id": "maker.alice@sim.cassandre.test"})
    assert resp.status_code == 200
    assert "bundle_id" in resp.json()

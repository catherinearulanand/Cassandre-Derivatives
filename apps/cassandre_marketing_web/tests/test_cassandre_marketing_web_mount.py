"""Verifies the Cassandre marketing site is correctly mounted into its own
FastAPI app (`cassandre_marketing_web.main.app`). This app has no business
API of its own — only that the static brand page is served — so there is no
equivalent of the other `_web` suites' "API routes still win" test.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from cassandre_marketing_web.main import app


def test_index_html_served_at_root():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Cassandr" in resp.text
    assert "See the evidence." in resp.text


def test_static_assets_served():
    client = TestClient(app)
    css = client.get("/style.css")
    js = client.get("/app.js")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]


def test_all_four_pillars_present():
    client = TestClient(app)
    body = client.get("/").text
    for pillar in ("ClauseOS", "RiskOS", "CollateralOS", "Cassandr"):
        assert pillar in body


def test_page_carries_no_network_calls_to_any_product_api():
    """The marketing site must never talk to clauseos/riskos/collateralos/
    cassandre_control APIs directly — it is presentation only."""
    client = TestClient(app)
    js_text = client.get("/app.js").text
    assert "fetch(" not in js_text
    assert "XMLHttpRequest" not in js_text

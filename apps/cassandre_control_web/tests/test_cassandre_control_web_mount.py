"""Verifies the Cassandre control UI is correctly mounted into the real
FastAPI app (`cassandre_control_api.main.app`) and that its JS's fetch target
paths match the API's actual endpoints. Does not re-test the API's own
business logic (that's `apps/cassandre_control_api/tests/test_api.py`'s job)
-- only that the static UI is served and wired up to real, existing routes.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from cassandre_control_api.main import app


def test_index_html_served_at_root():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Cassandre control" in resp.text


def test_static_assets_served():
    client = TestClient(app)
    css = client.get("/style.css")
    js = client.get("/app.js")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]


def test_api_routes_still_win_over_static_mount():
    """The static mount is registered last and must never shadow an API
    route: submitting an envelope to /v1/envelopes:submit must still reach
    the real handler, not a 404 from StaticFiles."""
    client = TestClient(app)
    resp = client.post("/v1/envelopes:submit", json={})
    # Malformed body -> FastAPI/Pydantic validation error (422), which only
    # happens if the real route handled the request at all.
    assert resp.status_code == 422


def test_unknown_correlation_key_still_returns_api_404_json():
    client = TestClient(app)
    resp = client.get("/v1/correlated-cases/does-not-exist-anywhere")
    assert resp.status_code == 404


_TEMPLATE_LITERAL = re.compile(r"`(/v1/[^`]*)`")
_QUOTED_LITERAL = re.compile(r"""['"](/v1/[^'"]*)['"]""")


def _normalize(path: str) -> str:
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    path = re.sub(r"\{[^}]*\}", "{}", path)
    return path


def test_js_fetch_paths_reference_real_api_routes():
    client = TestClient(app)
    js_text = client.get("/app.js").text

    real_paths = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path and path.startswith("/v1/"):
            real_paths.add(_normalize(path))

    referenced = set()
    for match in _TEMPLATE_LITERAL.findall(js_text) + _QUOTED_LITERAL.findall(js_text):
        referenced.add(_normalize(match))

    assert referenced, "expected app.js to reference at least one /v1/ path"
    for path in referenced:
        assert path in real_paths, f"{path!r} referenced by app.js does not match any real API route in {real_paths}"

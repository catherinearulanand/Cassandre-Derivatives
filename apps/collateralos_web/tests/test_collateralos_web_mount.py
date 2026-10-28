"""Verifies the CollateralOS control UI is correctly mounted into the real
FastAPI app (`collateralos_api.main.create_app()`) and that its JS's fetch
target paths match the API's actual endpoints. Does not re-test the API's
own business logic (that's `apps/collateralos_api/tests/test_api.py`'s job)
-- only that the static UI is served and wired up to real, existing routes.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from collateralos_api.main import create_app


def test_index_html_served_at_root():
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "CollateralOS" in resp.text


def test_static_assets_served():
    client = TestClient(create_app())
    css = client.get("/style.css")
    js = client.get("/app.js")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]


def test_api_routes_still_win_over_static_mount():
    """The static mount is registered last and must never shadow an API route:
    an existing API test path (health check) must still return the API's own
    JSON, not a 404 from StaticFiles."""
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_unknown_plan_still_returns_api_404_json_not_static_404():
    client = TestClient(create_app())
    resp = client.get("/v1/plans/SIM-PLAN-DOES-NOT-EXIST")
    assert resp.status_code == 404
    assert resp.json()["error"] == "NOT_FOUND"


def test_each_app_instance_gets_isolated_store_with_static_mounted():
    """create_app() must still produce fresh, isolated stores per call (the
    existing test-isolation contract) even though it now also mounts static
    files -- mounting must not become shared/global state across instances."""
    app_a = create_app()
    app_b = create_app()
    assert app_a.state.store is not app_b.state.store


_TEMPLATE_LITERAL = re.compile(r"`(/v1/[^`]*)`")
_QUOTED_LITERAL = re.compile(r"""['"](/v1/[^'"]*)['"]""")


def _normalize(path: str) -> str:
    # JS template-literal interpolation, e.g. "${encodeURIComponent(planId)}".
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    # FastAPI path placeholders, e.g. "{plan_id}".
    path = re.sub(r"\{[^}]*\}", "{}", path)
    return path


def test_js_fetch_paths_reference_real_api_routes():
    """Extract every /v1/... literal referenced by app.js (both plain-quoted
    strings and template literals with ${...} interpolation) and confirm each
    one matches a route actually registered on the real app, once both sides
    are normalized to the same {}-placeholder shape."""
    client = TestClient(create_app())
    js_text = client.get("/app.js").text

    real_paths = set()
    for route in create_app().routes:
        path = getattr(route, "path", None)
        if path and path.startswith("/v1/"):
            real_paths.add(_normalize(path))

    referenced = set()
    for match in _TEMPLATE_LITERAL.findall(js_text) + _QUOTED_LITERAL.findall(js_text):
        referenced.add(_normalize(match))

    assert referenced, "expected app.js to reference at least one /v1/ path"
    for path in referenced:
        assert path in real_paths, f"{path!r} referenced by app.js does not match any real API route in {real_paths}"

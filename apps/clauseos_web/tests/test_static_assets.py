"""Lightweight, dependency-free checks on the ClauseOS UI's static assets
themselves (no FastAPI/TestClient needed here -- that integration-level
check, which proves the files are actually served correctly when mounted
into `clauseos_api`, lives in
`apps/clauseos_api/tests/test_web_ui.py`). This file just guards against
the static files going missing or becoming internally inconsistent (e.g. a
page id referenced in `app.js` with no corresponding nav entry)."""

from __future__ import annotations

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

# Kept in sync with the `PAGES` array in app.js -- plan Section 10.8's page
# list for the ClauseOS UI.
EXPECTED_PAGE_IDS = {
    "inventory", "graph", "evidence", "maker", "checker", "conflicts", "diff", "releases", "audit",
}


def test_static_files_exist_and_are_non_empty():
    for name in ("index.html", "app.js", "styles.css"):
        path = STATIC_DIR / name
        assert path.is_file(), f"missing static file: {path}"
        assert path.stat().st_size > 0, f"static file is empty: {path}"


def test_index_html_references_app_js_and_styles():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert '<script src="app.js">' in html
    assert 'href="styles.css"' in html
    assert "<title>ClauseOS</title>" in html


def test_app_js_declares_every_plan_section_10_8_page():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    declared_ids = set(re.findall(r'\["([a-z]+)",\s*"[^"]+"\]', js))
    assert declared_ids == EXPECTED_PAGE_IDS, (
        f"app.js PAGES list {declared_ids} does not match the expected plan "
        f"Section 10.8 page set {EXPECTED_PAGE_IDS}"
    )


def test_app_js_never_renders_a_bare_confidence_percentage():
    """Plan Section 10.8: "The UI must never show a scalar confidence as
    proof." This checks for the actual violation pattern -- a numeric
    confidence/likelihood field interpolated next to a '%' sign in a
    rendered template -- rather than banning the word "confidence"
    outright, since the UI's own hint text legitimately *explains* this
    design rule to the person using it ("...never a confidence score.")."""
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for forbidden in ("confidence_score", "confidenceScore", "% confident", "confidencePercent"):
        assert forbidden not in js
    # No template interpolation immediately followed by a literal '%' --
    # the pattern a scalar-percentage badge would use.
    assert not re.search(r"\$\{[^}]*\}\s*%", js)


def test_app_js_wires_every_declared_page_to_a_renderer():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for page_id in EXPECTED_PAGE_IDS:
        assert f"RENDERERS.{page_id} =" in js, f"no RENDERERS.{page_id} assignment found in app.js"

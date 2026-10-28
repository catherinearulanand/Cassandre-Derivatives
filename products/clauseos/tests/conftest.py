"""Shared test fixtures for ClauseOS.

Docker Desktop is not functional on this development machine (confirmed
broken WSL2 backend, see `docs/INTERPRETATION.md` DEC-ENV-01), so any test
needing a real Postgres connection must go through `clauseos_db_engine`,
which skips cleanly if `CASSANDRE_TEST_DATABASE_URL` is unset or
unreachable. Domain-layer tests never depend on this fixture.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def clauseos_db_engine():
    database_url = os.environ.get("CASSANDRE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("CASSANDRE_TEST_DATABASE_URL not set; skipping DB-backed ClauseOS test")

    from sqlalchemy import create_engine, text

    try:
        engine = create_engine(database_url, future=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any connectivity failure skips
        pytest.skip(f"could not connect to CASSANDRE_TEST_DATABASE_URL: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture
def ollama_reachable() -> bool:
    """Mirrors the `clauseos_db_engine` skip pattern above, but for the
    optional local Ollama model adapter (Gap CLO-X01): any connectivity
    failure -- server not running, model not pulled yet, network error --
    is never required for ClauseOS's critical path (ADR-006), so tests
    that depend on this fixture should skip cleanly rather than fail.
    The actual reachability probe lives in `clauseos.core.model_adapter.
    is_ollama_reachable` (production code, not test-only) so both this
    fixture and `test_model_adapter.py`'s module-level `skipif` can use the
    identical check without importing across this directory's own
    `conftest` module name -- every product's `tests/` package shares that
    bare module name under this repo's `--import-mode=prepend` setup (see
    product README's "cross-product test-collection issue"), so importing
    a helper *from* conftest across test modules is unreliable; importing
    from the properly-namespaced `clauseos.core.model_adapter` is not."""
    from clauseos.core.model_adapter import is_ollama_reachable

    return is_ollama_reachable()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "requires_ollama: skips cleanly if the local Ollama server/model is unreachable"
    )

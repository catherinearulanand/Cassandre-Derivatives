"""Shared test fixtures for `products/collateralos`.

Per `docs/adr/ADR-002-database-per-product.md`: Docker Desktop is not
runnable on this machine this session, so any DB-dependent test must try
`CASSANDRE_TEST_DATABASE_URL` and skip cleanly (not report a false pass) when
it is unset or unreachable. Domain/core/application tests (everything else
in this suite, including the threading-based concurrency test) have zero DB
dependency and always run.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture()
def collateralos_db_engine():
    database_url = os.environ.get("CASSANDRE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("CASSANDRE_TEST_DATABASE_URL not set; Docker Desktop is unavailable this session (ADR-002)")

    from sqlalchemy import text

    from collateralos.adapters.db import make_engine

    engine = make_engine(database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on external DB state
        pytest.skip(f"CASSANDRE_TEST_DATABASE_URL set but unreachable: {exc}")

    yield engine
    engine.dispose()


@pytest.fixture()
def collateralos_sql_engine():
    """Like `collateralos_db_engine` above, but also creates every table in
    `collateralos.adapters.models.Base.metadata` before yielding and drops
    them all afterwards -- mirrors `products/riskos/tests/conftest.py`'s
    `sql_session` fixture (create tables, yield, drop tables), except this
    one yields the `Engine` itself rather than a single bound `Session`:
    `test_sql_store.py` needs multiple independent `Session`/connections
    against the *same* schema (one per `SqlAlchemyCollateralOSStore` call,
    plus two genuinely separate connections for the row-locking concurrency
    test), which a single shared `Session` fixture couldn't provide.

    Dropping (not just leaving data behind) after every test, combined with
    every test using its own `SIM-*`-prefixed, per-test-unique identifiers
    (never a shared fixed ID), is what makes the whole suite safe to run
    twice in a row against the same live Postgres without resetting it.
    """
    database_url = os.environ.get("CASSANDRE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("CASSANDRE_TEST_DATABASE_URL not set; Docker Desktop is unavailable this session (ADR-002)")

    from sqlalchemy import text

    from collateralos.adapters.db import make_engine
    from collateralos.adapters.models import Base

    # `cassandre_security.revocation.SecurityFenceRow` lives under its own
    # `DeclarativeBase` (see that module), not `collateralos.adapters.
    # models.Base` -- `adapters/sql_store.py`'s SEC-14 wiring
    # (`ensure_fence_exists`/`lock_fences_for_mutation`) needs its table
    # too, mirroring `migrations/versions/0002_security_fence.py` for the
    # create_all/drop_all-based test fixture path used here (no alembic in
    # this workflow, see the product README).
    from cassandre_security.revocation import Base as FenceBase

    engine = make_engine(database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on external DB state
        pytest.skip(f"CASSANDRE_TEST_DATABASE_URL set but unreachable: {exc}")

    Base.metadata.create_all(engine)
    FenceBase.metadata.create_all(engine)
    try:
        yield engine
    finally:
        FenceBase.metadata.drop_all(engine)
        Base.metadata.drop_all(engine)
        engine.dispose()

"""Shared test fixtures for `cassandre_security`.

`sqlite_session` runs the revocation/witness logic tests fast with no
external dependency (SQLite honors `SELECT ... FOR UPDATE` syntactically
but does not provide real multi-connection row locking -- fine for
single-threaded correctness tests). `postgres_engine` is the
`CASSANDRE_TEST_DATABASE_URL`-gated real-Postgres fixture, mirroring the
exact pattern already proven in `products/riskos/tests/conftest.py` and
`products/collateralos/tests/conftest.py` in this repository, used only by
the genuine multi-connection concurrency test that needs real row locking.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from cassandre_security.revocation import Base as RevocationBase
from cassandre_security.witness import Base as WitnessBase


@pytest.fixture
def sqlite_session():
    engine = create_engine("sqlite:///:memory:")
    RevocationBase.metadata.create_all(engine)
    WitnessBase.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def admin_postgres_engine():
    """A cluster-superuser-level connection, needed only by the
    `db_lockdown` integration test to CREATE ROLE/TABLE from scratch.
    `CASSANDRE_TEST_DATABASE_URL` is scoped to one product's own
    least-privilege role elsewhere in this repo and cannot create roles --
    this is a deliberately separate, optional env var so the ordinary test
    run (unset) skips this one deeper test cleanly without affecting any
    other fixture."""
    database_url = os.environ.get("CASSANDRE_TEST_ADMIN_DATABASE_URL")
    if not database_url:
        pytest.skip("CASSANDRE_TEST_ADMIN_DATABASE_URL not set; skipping DB-lockdown superuser integration test")
    try:
        engine = create_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"could not connect to CASSANDRE_TEST_ADMIN_DATABASE_URL: {exc}")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def postgres_engine():
    database_url = os.environ.get("CASSANDRE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("CASSANDRE_TEST_DATABASE_URL not set; skipping real-Postgres concurrency test")

    try:
        engine = create_engine(database_url, future=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any connectivity failure means "skip", not "fail"
        pytest.skip(f"could not connect to CASSANDRE_TEST_DATABASE_URL: {exc}")

    RevocationBase.metadata.create_all(engine)
    WitnessBase.metadata.create_all(engine)
    try:
        yield engine
    finally:
        RevocationBase.metadata.drop_all(engine)
        WitnessBase.metadata.drop_all(engine)
        engine.dispose()

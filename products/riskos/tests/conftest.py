"""Shared test fixtures for `products/riskos`. Domain/core/application tests
(items 1-7 of the session's slice) must run with zero DB dependency and
always pass. Anything that genuinely needs Postgres uses `sql_session`
below, which skips cleanly when `CASSANDRE_TEST_DATABASE_URL` is unset or
the database is unreachable (Docker Desktop is not functional on this build
machine)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# The repo root's pytest config runs with `--import-mode=importlib` (chosen
# to avoid cross-product `tests` package name collisions). That mode does
# not add a test file's own directory to `sys.path`, so sibling-module
# imports like `import riskos_fixtures` in this directory's test files would
# otherwise fail. Make this directory importable regardless of import mode.
_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)


@pytest.fixture
def sql_session():
    database_url = os.environ.get("CASSANDRE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("CASSANDRE_TEST_DATABASE_URL not set; skipping DB-dependent test")

    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from cassandre_security.revocation import Base as SecurityFenceBase
        from cassandre_security.witness import Base as SecurityWitnessBase

        from riskos.adapters.models import Base

        engine = create_engine(database_url)
        with engine.connect():
            pass
    except Exception as exc:  # noqa: BLE001 - any connection failure means "skip", not "fail"
        pytest.skip(f"riskos test database unreachable: {exc}")

    # This fixture creates tables via each SQLAlchemy model's own
    # `DeclarativeBase.metadata` directly (not by running Alembic
    # migrations), matching how `packages/cassandre_security/tests/
    # conftest.py`'s own `sqlite_session`/`postgres_engine` fixtures already
    # provision `SecurityFenceBase`/`SecurityWitnessBase`'s tables in that
    # package's own tests. RiskOS's production deployment instead runs
    # `migrations/versions/0003_security_integration.py` (see
    # `products/riskos/README.md`'s "Security integration" section for why
    # both exist).
    Base.metadata.create_all(engine)
    SecurityFenceBase.metadata.create_all(engine)
    SecurityWitnessBase.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        SecurityWitnessBase.metadata.drop_all(engine)
        SecurityFenceBase.metadata.drop_all(engine)
        Base.metadata.drop_all(engine)
        engine.dispose()

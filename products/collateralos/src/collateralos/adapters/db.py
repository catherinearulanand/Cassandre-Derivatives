"""Engine/session helpers for the `collateralos` Postgres database (ADR-002).

Deliberately thin: this module only creates an engine/sessionmaker from a
connection URL. It never chooses that URL itself -- callers (Alembic env.py,
`conftest.py`'s `CASSANDRE_TEST_DATABASE_URL`-gated fixture, the FastAPI
composition root) own that decision, keeping this module usable against any
of them without a hidden default.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)

"""Alembic environment for the `collateralos` database (ADR-002).

The connection URL comes only from `CASSANDRE_COLLATERALOS_DATABASE_URL` (or
`CASSANDRE_TEST_DATABASE_URL` as a test-time fallback) -- never a hard-coded
default, per plan Section 25.3 / ADR-014 (fail closed rather than silently
pointing at some assumed local database).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from collateralos.adapters.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("CASSANDRE_COLLATERALOS_DATABASE_URL") or os.environ.get("CASSANDRE_TEST_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "CASSANDRE_COLLATERALOS_DATABASE_URL (or CASSANDRE_TEST_DATABASE_URL) must be set; "
            "this migration environment never assumes a default database"
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

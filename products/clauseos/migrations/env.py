"""Alembic environment for ClauseOS. Reads the database URL from
`CASSANDRE_CLAUSEOS_DATABASE_URL` (falling back to
`CASSANDRE_TEST_DATABASE_URL` for local/test runs) rather than hard-coding
one -- this product's database is owned solely by `clauseos_api`, per the
database ownership map in `docs/INTERPRETATION.md`.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from clauseos.adapters.db.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_DATABASE_URL = os.environ.get("CASSANDRE_CLAUSEOS_DATABASE_URL") or os.environ.get(
    "CASSANDRE_TEST_DATABASE_URL"
)
if _DATABASE_URL:
    config.set_main_option("sqlalchemy.url", _DATABASE_URL)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""Real `docker exec pg_dump`/`psql` integration test for `backup_restore.py`.

Separate from `test_backup_restore.py` (pure logic, no Docker) because this
one shells out to the actual `cassandre_postgres` container this session's
`deploy/compose/docker-compose.yml` brings up. Skips cleanly if that
container isn't running -- mirrors this repo's `CASSANDRE_TEST_DATABASE_URL`
skip pattern, but checks container reachability directly since this
module talks to `docker exec`, not a SQLAlchemy URL.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile

import pytest

from cassandre_security.backup_restore import restore_snapshot_into_quarantine, snapshot_database

CONTAINER_NAME = "cassandre_postgres"


def _container_running() -> bool:
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@pytest.fixture
def running_postgres_container():
    if not _container_running():
        pytest.skip(f"{CONTAINER_NAME} container is not running; skipping real docker-exec backup/restore test")
    return CONTAINER_NAME


PRODUCT_CASES = [
    # (product, database, role, table_to_check)
    ("clauseos", "clauseos", "clauseos_app", "agreement_bundle"),
    ("riskos", "riskos", "riskos_app", "legal_entity_version"),
]


def _seed_clauseos(database_url: str) -> None:
    import datetime as dt

    from clauseos.adapters.db.models import Base
    from clauseos.adapters.db.repository import SqlAlchemyBundleRepository, make_session_factory
    from clauseos.domain.bundle import AgreementBundle
    from clauseos.domain.status import BundleStatus
    from sqlalchemy import create_engine

    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    repo = SqlAlchemyBundleRepository(make_session_factory(engine))
    repo.save_bundle(
        AgreementBundle(
            bundle_id="SIM-BACKUP-TEST-BUNDLE",
            scenario_id="SIM-SCEN-BACKUP-TEST",
            status=BundleStatus.CREATED,
            created_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        )
    )
    engine.dispose()


def _seed_riskos(database_url: str) -> None:
    import sys

    sys.path.insert(0, str(_repo_root() / "products" / "riskos" / "tests"))
    import riskos_fixtures as fx  # type: ignore[import-not-found]
    from riskos.adapters.models import Base
    from riskos.adapters.sql_repository import SqlAlchemyRiskOSRepository
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repo = SqlAlchemyRiskOSRepository(session)
        scenario = fx.build_golden_scenario()
        repo.save_legal_entity(scenario.calculation_entity)
        session.commit()
    engine.dispose()


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[3]


_SEED_FUNCTIONS = {"clauseos": _seed_clauseos, "riskos": _seed_riskos}
_DATABASE_URLS = {
    "clauseos": "postgresql+psycopg2://clauseos_app:clauseos_dev_only_change_me@localhost:55432/clauseos",
    "riskos": "postgresql+psycopg2://riskos_app:riskos_dev_only_change_me@localhost:55432/riskos",
}


@pytest.fixture(params=PRODUCT_CASES, ids=[c[0] for c in PRODUCT_CASES])
def product_case(request, running_postgres_container):
    """Self-contained: seeds its own minimal real data via each product's
    own repository/model layer before snapshotting, rather than depending
    on ambient state left by some other test run -- a prior version of
    this test relied on data seeded by a one-off manual script, which a
    LATER unrelated test run's `Base.metadata.drop_all` teardown wiped out
    from under it. That was a real bug in this test, found by running the
    whole package suite end to end."""
    product, database, role, table = request.param
    _SEED_FUNCTIONS[product](_DATABASE_URLS[product])

    db_name = f"{database}_backup_restore_test_quarantine"
    subprocess.run(
        ["docker", "exec", running_postgres_container, "psql", "-U", "postgres",
         "-c", f"DROP DATABASE IF EXISTS {db_name};",
         "-c", f"CREATE DATABASE {db_name} OWNER {role};"],
        capture_output=True, timeout=20,
    )
    yield product, database, role, table, db_name
    subprocess.run(
        ["docker", "exec", running_postgres_container, "psql", "-U", "postgres", "-c", f"DROP DATABASE IF EXISTS {db_name};"],
        capture_output=True, timeout=20,
    )


def _row_count(container: str, role: str, database: str, table: str) -> int:
    result = subprocess.run(
        ["docker", "exec", container, "psql", "-U", role, "-d", database, "-t", "-c", f"SELECT count(*) FROM {table};"],
        capture_output=True, text=True, timeout=20,
    )
    return int(result.stdout.strip())


def test_real_snapshot_and_quarantine_restore_produces_matching_row_counts(running_postgres_container, product_case):
    """Parametrized across two independent products (ClauseOS, RiskOS) to
    prove `backup_restore.py` is genuinely product-agnostic -- the same
    functions, only the container/role/database/table parameters differ."""
    import datetime as dt

    product, database, role, table, quarantine_db = product_case
    now = dt.datetime.now(dt.timezone.utc)
    tmp_dir = tempfile.mkdtemp(prefix="cassandre_backup_test_")
    try:
        snapshot = snapshot_database(
            container_name=running_postgres_container,
            postgres_role=role,
            database_name=database,
            product=product,
            output_dir=tmp_dir,
            now=now,
        )
        assert snapshot.dump_sha256
        assert snapshot.product == product

        restore_snapshot_into_quarantine(
            container_name=running_postgres_container,
            postgres_role=role,
            snapshot=snapshot,
            quarantine_database_name=quarantine_db,
        )

        original_count = _row_count(running_postgres_container, role, database, table)
        restored_count = _row_count(running_postgres_container, role, quarantine_db, table)
        assert restored_count == original_count
        assert restored_count > 0  # proves the table isn't just coincidentally empty in both
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

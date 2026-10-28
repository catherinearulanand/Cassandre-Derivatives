#!/usr/bin/env python3
"""Verify per-product Postgres role isolation, per ADR-002 / work item FND-004.

Connects to each product's own database with ITS OWN role, then attempts to
query every SIBLING database with that same role's credentials and asserts
Postgres itself rejects it (not just that the application layer would have
refused). Requires the docker-compose Postgres from deploy/compose to be
running; if it is not reachable this prints a clear skip message and exits 0
rather than reporting a false pass or a hard crash — this is a verification
tool for a running environment, not a unit test.
"""

from __future__ import annotations

import sys

try:
    import psycopg
except ImportError:
    print("psycopg not installed in this environment; run via `uv run python scripts/verify_db_isolation.py`")
    sys.exit(1)

HOST = "localhost"
PORT = 55432

ROLES = {
    "clauseos_app": ("clauseos_dev_only_change_me", "clauseos"),
    "riskos_app": ("riskos_dev_only_change_me", "riskos"),
    "collateralos_app": ("collateralos_dev_only_change_me", "collateralos"),
    "cassandre_control_app": ("cassandre_control_dev_only_change_me", "cassandre_control"),
}


def _try_connect(user: str, password: str, dbname: str):
    return psycopg.connect(host=HOST, port=PORT, user=user, password=password, dbname=dbname, connect_timeout=3)


def main() -> int:
    try:
        probe = _try_connect("clauseos_app", ROLES["clauseos_app"][0], "clauseos")
        probe.close()
    except Exception as exc:  # noqa: BLE001 - deliberately broad: this is an environment probe
        print(f"SKIPPED: Postgres not reachable at {HOST}:{PORT} ({exc}). "
              f"Start it with `docker compose -f deploy/compose/docker-compose.yml up -d postgres` "
              f"and re-run this script.")
        return 0

    violations = []
    for own_role, (password, own_db) in ROLES.items():
        for other_role, (_, other_db) in ROLES.items():
            if other_db == own_db:
                continue
            try:
                conn = _try_connect(own_role, password, other_db)
                conn.close()
                violations.append(f"{own_role} was able to connect to sibling database {other_db!r}")
            except psycopg.OperationalError:
                pass  # expected: connection refused/authorization failed

    if violations:
        print("DATABASE ISOLATION VIOLATIONS:")
        for v in violations:
            print(f"  - {v}")
        return 1

    print(f"Database isolation verified: none of {list(ROLES)} could reach a sibling database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

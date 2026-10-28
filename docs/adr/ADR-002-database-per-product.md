# ADR-002: PostgreSQL, separate database and role per product

## Decision
PostgreSQL 16 (via Docker) for all transactional/workflow/reservation state.
Databases: `clauseos`, `riskos`, `collateralos`, `cassandre_control`, plus
Keycloak's own database. One least-privilege role per database; no role has
grants on a sibling database.

## Rationale
Serializable reservation transactions and `SELECT ... FOR UPDATE` locking
(Section 12.4) require a real relational engine with row locks — not SQLite.
Per-product database isolation is what makes "Cassandre may not share mutable
domain tables with a product" (Section 3.4) and "product tokens cannot access
sibling databases" (Section 13) testable rather than aspirational.

## Enforcement
`scripts/verify_db_isolation.py` connects with each product's own role and
asserts every sibling-database query is rejected by Postgres, not just by
application code.

## Known deviation this session
A single Postgres *server* hosts all four databases (plan Section 6.1 already
flags this as "a common infrastructure failure domain, not proof of physical
isolation" — carried forward honestly, not hidden).

## Known gap: Docker Desktop is not runnable on this machine
`deploy/compose/docker-compose.yml` defines the intended Postgres + Keycloak
topology, but this session found Docker Desktop's engine (WSL2 `docker-desktop`
distro) stopped and no Desktop launcher installed — only leftover
`docker-secrets-engine_broken_*` directories from an earlier failed install.
Fixing a third-party desktop application install is outside this session's
scope. Consequence: every persistence-adapter test in this build is written
against real SQLAlchemy/Postgres code paths but **skips itself** (does not
report a false pass) when `CASSANDRE_TEST_DATABASE_URL` is unset or
unreachable — see each product's `tests/conftest.py`. Domain/core logic has
full coverage with no such gap. Get Docker Desktop running, then
`docker compose -f deploy/compose/docker-compose.yml up -d` and re-run the
suite to exercise the skipped tests for real.

# Running the initial slice end-to-end

This is **not** the Section 21 daily simulated run (that requires the full
simulation factory, all four products' UIs, and Phase 7 operating
maturity — none of which exist yet). This is the smallest thing that
exercises all four services together, per plan Section 25.5.

## Prerequisites

```
uv sync
```

Docker (Postgres + Keycloak) is optional for this slice — every service can
run its domain logic and in-memory-backed API without a database; DB-backed
adapter tests skip themselves cleanly if `CASSANDRE_TEST_DATABASE_URL` is
unset or unreachable. To exercise the real Postgres path once Docker Desktop
is working on this machine again:

```
docker compose -f deploy/compose/docker-compose.yml up -d
```

## Run every test in the workspace

```
uv run pytest -q
```

## Run the import-boundary and public-schema checks

```
uv run python scripts/check_import_graph.py
uv run python scripts/export_public_schemas.py
```

## Generate the release manifest

```
uv run python scripts/generate_release_manifest.py
```

Inspect `release-manifest.yaml` afterward — every field is either a real
measured value or an explicit `not_yet_implemented` reason, never a
hand-typed claim (ADR-015).

## Start the four services locally (in-memory mode, no Postgres required)

```
uv run uvicorn clauseos_api.app:app --port 8001
uv run uvicorn riskos_api.main:app --port 8002
uv run uvicorn collateralos_api.main:create_app --factory --port 8003
uv run uvicorn cassandre_control_api.main:app --port 8004
```

(ClauseOS's entrypoint module is `app.py`, not `main.py`; CollateralOS's
`main.py` exposes an app *factory* `create_app()` rather than a bare `app`
object, so it needs uvicorn's `--factory` flag — see each product's own
README for details.)

Then: create a ClauseOS bundle and publish a signed policy release; run a
RiskOS margin calculation against manually supplied terms/MTMs/collateral;
run a CollateralOS plan/reserve/approve/preflight/export cycle; register
each product's signing key with `POST /v1/trust-keys` on the Cassandre
control API and submit each resulting signed envelope to
`POST /v1/envelopes:submit`; confirm `GET /v1/correlated-cases/{scenario_id}`
shows `is_cross_product_complete: true`.

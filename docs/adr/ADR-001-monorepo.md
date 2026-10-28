# ADR-001: Monorepo, four independently deployable applications

## Decision
One Git repository. Four independently deployable applications
(`clauseos_api`, `riskos_api`, `collateralos_api`, `cassandre_control_api`, each
with a paired `_web`). Public contracts (`schemas/public/`) are versioned
separately from application code.

## Rationale
Development efficiency (shared tooling, one CI, atomic cross-cutting changes
to shared packages) without coupling deployability: each `apps/*_api` has its
own Dockerfile, its own Compose service, and can be built/deployed without the
others existing.

## Enforcement
`scripts/check_import_graph.py` (CI) fails the build if any `products/<x>/{domain,core}`
imports `fastapi`, `sqlalchemy`, a sibling product package, or `apps/cassandre_control_api`.

## Packaging layout note
Each package/product/app is laid out as `<root>/src/<import_name>/...` (a
`src/` layer under `products/<x>/` and `packages/<x>/`) rather than the bare
`products/clauseos/domain/` shown in plan Section 7's illustrative tree, so
that every workspace member installs as an isolated, independently
versionable Python package under `uv`'s workspace resolver. The logical
separation the plan actually cares about — `domain`/`core`/`application`/
`adapters` as distinct subpackages, no product importing a sibling — is
unchanged; only the packaging convenience wrapper differs.

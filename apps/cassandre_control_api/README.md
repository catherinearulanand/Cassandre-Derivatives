# Cassandre control API (optional federation layer)

Implements plan Section 3.4 / Section 25.5's Cassandre bullet: "validate and
correlate the three signed envelopes without calling internal code." Maps to
work items `AET-001` (client-side envelope handling — inbound only in this
slice), `AET-002` (signed-envelope validation), `AET-003` (correlated case
view), `AET-006` (independence: this app imports nothing from `clauseos`,
`riskos`, or `collateralos` — enforced by `scripts/check_import_graph.py`).

## What this is

- `domain.py`: pure logic — `record_envelope` verifies a `cassandre_contracts.Envelope`
  and raises `RejectedEnvelopeError` (never silently accepts a bad envelope);
  `CorrelatedCase` groups receipts by `simulation_provenance.scenario_id`;
  `assemble_federated_audit_export` builds a read-only bundle from already-
  verified receipts only.
- `store.py`: process-local, in-memory `TrustRegistry` (key_id -> public key,
  plus revocation) and `CorrelationStore`. Thread-safe via a lock, but **not**
  durable — restarting the process loses all state. This is a known, honest
  gap: the plan's `aether_control` (now `cassandre_control`) Postgres database
  is not implemented in this session.
- `main.py`: FastAPI app — `POST /v1/trust-keys`, `POST /v1/trust-keys/{id}:revoke`,
  `POST /v1/envelopes:submit`, `GET /v1/correlated-cases/{key}`,
  `GET /v1/correlated-cases/{key}/federated-audit-export`.

## What's deferred

- Persistent storage (Postgres `cassandre_control` database + Alembic
  migration) — everything here is in-memory for this slice.
- `AET-004` full federated audit *export format* beyond the simple JSON
  produced here.
- `AET-005` end-to-end scenario UI.
- Real key-lifecycle management (offline trust root, rotation schedule,
  signed revocation lists per plan Section 8.1) — `TrustRegistry` only
  tracks a flat key_id/public_key/revoked-set map.
- OIDC/authentication on these endpoints (no auth wired this session).

## Web UI

`apps/cassandre_control_web/` is a plain HTML/CSS/vanilla-JS control UI (no
build step, no node_modules) mounted into this app's module-level FastAPI
`app` object via `fastapi.staticfiles.StaticFiles` (registered last, after
every `@app.get`/`@app.post` route in `main.py`, so it never shadows an API
route) -- one `uvicorn cassandre_control_api.main:app` process serves both
the API and the UI. Skipped gracefully if
`apps/cassandre_control_web/static/` doesn't exist.

Pages, each driving the real API:

- **Trust-key registration/revocation** -- `POST /v1/trust-keys` and
  `POST /v1/trust-keys/{id}:revoke`, with a session-local log (no key-listing
  endpoint exists on the API).
- **Envelope submission** -- paste or upload a signed
  `cassandre_contracts.Envelope` JSON document and `POST /v1/envelopes:submit`;
  on rejection, every exact failure reason from `verify_envelope`'s
  `VerificationResult.failures` closed set is rendered (e.g.
  `CONTENT_HASH_MISMATCH`, `MISSING_SIGNATURE`, `UNKNOWN_OR_REVOKED_KEY_ID`,
  `SIGNATURE_INVALID`, `AUDIENCE_MISMATCH`, `PURPOSE_NOT_PERMITTED`, `STALE`,
  `AUTHORITY_STATUS_NOT_PERMITTED`) -- never collapsed to a generic error.
- **Correlated case view** -- look up by correlation key (each envelope's
  `simulation_provenance.scenario_id`) via
  `GET /v1/correlated-cases/{key}`; shows which of ClauseOS/RiskOS/CollateralOS
  have an accepted envelope for that case and whether it's
  `is_cross_product_complete`.
- **Federated audit export** -- `GET /v1/correlated-cases/{key}/federated-audit-export`,
  rendered as a readable table (producer_product, object_id, object_version,
  content_hash, authority_status, received_at) with a "view raw JSON" toggle.

Tests: `apps/cassandre_control_web/tests/test_cassandre_control_web_mount.py`
confirms the UI is served at `/`, that API routes still win over the static
mount, and that every `/v1/...` path referenced by the UI's `app.js` matches
a real registered API route (parsed from the app's own route table).

## Tests

```
uv run pytest apps/cassandre_control_api apps/cassandre_control_web -q
```
10 API tests + 5 web-UI tests, all passing, zero database dependency (this
app has none yet).

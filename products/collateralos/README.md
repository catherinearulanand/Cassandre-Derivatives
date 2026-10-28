# CollateralOS

Collateral obligation intake, eligibility/policy compilation, planning
(LP-preview + MIP + independently re-verified allocation), reservation,
maker/checker approval, and idempotent internal export -- the Section 25.5
"S1 slice" of the CollateralOS product described in the Cassandre platform
plan. Every identifier in this codebase and its fixtures is synthetic and
namespaced `SIM-*` (plan Section 9.1); nothing here is real institution,
account, or security data, and (per ADR-012) nothing this product produces
is ever transmitted to an external settlement system -- `export-internal`
produces a validated, signed internal record only.

## Architecture

```
products/collateralos/src/collateralos/
  domain/        pure data + business rules, no I/O, no framework imports
    obligation.py     Obligation aggregate
    policy.py          PolicyBundle / AssetClass / haircuts / min-lot / wrong-way map
    inventory.py       InventoryPosition / InventorySnapshot
    eligibility.py     static per-position eligibility compiler
    reservation.py     reservation-ledger arithmetic (InventoryBalanceState, attempt_reserve)
    plan.py            Plan / AllocationLine / SolverEvidence + plan_hash()
    verifier.py         independent domain verifier (ADR-008) -- the final gate
    approval.py        maker/checker binding-hash protocol
    settlement.py      SettlementInstruction / SettlementPreflight
    status.py          closed status/enum vocabulary (plan Section 12.6)
    identifiers.py     SIM-* namespace guard re-export

  core/          the only place Decimal->float conversion is allowed (ADR-008)
    model_inputs.py    builds solver-facing PositionModelInput from eligible positions
    lp_preview.py      LP relaxation (diagnostic only, not gating)
    mip_planner.py     MILP solver (scipy/HiGHS)
    planner.py         orchestrates LP preview -> MIP -> independent verifier ->
                       verified-greedy fallback -> Plan, never trusting solver status alone

  application/   composition layer -- turns domain+core into a runnable workflow
    inventory_repository.py  lock-protected in-memory reservation ledger
                              (stand-in for Postgres SELECT ... FOR UPDATE;
                              Docker Desktop is unavailable this session --
                              see docs/adr/ADR-002-database-per-product.md)
    idempotency.py            IdempotencyConflictError / PreflightFailedError
    store.py                  CollateralOSStore: obligation -> policy -> snapshot ->
                              plan -> reserve -> maker/checker -> preflight -> export,
                              plus audit-chain recording for every mutation

  adapters/      SQLAlchemy schema (models.py) + engine helpers (db.py) + the
                 real Postgres-backed store (sql_store.py, sql_reservation.py,
                 codec.py) -- exercised by tests/test_sql_store.py against a
                 real Postgres when CASSANDRE_TEST_DATABASE_URL is set; not
                 wired into apps/collateralos_api by default (see "Database
                 tests" below)

apps/collateralos_api/src/collateralos_api/
  main.py        FastAPI wrapper around one CollateralOSStore instance per app,
                 injected via a dependency so tests get full isolation with
                 zero Postgres/Docker dependency
```

## Work-item traceability

| Work item | What it is | Where |
|---|---|---|
| COL-001 | Obligation ingest | `domain/obligation.py`, `application/store.py::add_obligation` |
| COL-002 | Inventory snapshot ingest | `domain/inventory.py`, `application/store.py::add_snapshot` |
| COL-003 | Inventory ledger (balance derivation: `gross_settled - encumbered - pending_outbound - blocked_or_recalled - reserved`) | `domain/reservation.py::InventoryBalanceState`, `balances_from_positions` |
| COL-004 | Policy bundle lifecycle (DRAFT -> VALIDATED -> ACTIVE) | `domain/policy.py`, `application/store.py::add_draft_policy/validate_policy/activate_policy` |
| COL-005 | Eligibility compiler (complete ordered rejection set, never a bare boolean) | `domain/eligibility.py` |
| COL-006 | LP preview (diagnostic, non-gating) | `core/lp_preview.py` |
| COL-007 | MIP solver | `core/mip_planner.py` (scipy/HiGHS backend -- installed cleanly this session, so this slice runs the real MILP, not an LP-only fallback) |
| COL-008 | Independent domain verifier (ADR-008: re-derives coverage/eligibility/haircuts/concentration/min-lot/availability in exact `Decimal`, never trusts solver status) | `domain/verifier.py` |
| COL-009 | Solver evidence record | `domain/plan.py::SolverEvidence`, assembled in `core/planner.py::plan_obligation` |
| COL-010 | Reservation transaction (all-or-nothing, deterministic lock ordering, idempotent release) | `domain/reservation.py::attempt_reserve`, `application/inventory_repository.py`, `application/store.py::reserve_plan` |
| COL-011 | Maker/checker approval binding (content-hash-bound; fails closed on drift or self-approval) | `domain/approval.py`, `application/store.py::maker_approve/checker_approve` |
| COL-012 | Preflight | `domain/settlement.py::SettlementPreflight`, `application/store.py::run_preflight` |
| COL-013 | Idempotent internal export (same key+body replays; same key+different body is refused; signed `cassandre_contracts.Envelope`) | `application/idempotency.py`, `application/store.py::export_internal` |
| COL-014 | Persistence schema + minimal HTTP API | `adapters/models.py` (schema), `adapters/sql_store.py`+`adapters/sql_reservation.py`+`adapters/codec.py` (real Postgres-backed store), `apps/collateralos_api/src/collateralos_api/main.py` (API, still wired to the in-memory store by default) |

COL-003's mapping above is inferred from the plan's milestone description
("inventory ledger" as a step distinct from ingest itself); the source
files for COL-002/004/005/010/011/012/013 carry their work-item ID directly
in an in-code comment, which is the more authoritative source if the two
ever appear to disagree.

## Test suite

`products/collateralos/tests/`:

| File | Covers |
|---|---|
| `test_eligibility.py` | Every rejection reason category, combined-reason ordering, inactive-policy fail-closed |
| `test_verifier.py` | Independent verifier, including `test_verifier_rejects_hand_built_concentration_violation` -- a candidate built by hand (no solver call anywhere in the test) that delivers 100% of its value from one issuer group, asserted rejected on the 40% post-transfer concentration cap |
| `test_reservation_concurrency.py` | 35 concurrent `threading.Thread` reservation attempts against one lock-protected `InMemoryInventoryRepository`; asserts the successful-reservation count is exactly what the arithmetic predicts (no over-reservation) regardless of thread interleaving, and every attempt beyond capacity fails closed with `ReservationConflictError` |
| `test_approval.py` | Maker/checker binding at both the pure-domain level and the store level: checker approval succeeds when nothing changed, fails closed with `APPROVAL_INVALIDATED` when the plan changes between maker and checker approval, and rejects maker == checker via `SelfApprovalError` |
| `test_preflight_export.py` | Fresh preflight immediately before export; same idempotency key + same request body replays the exact prior result (no re-export, no duplicate audit event); same key + a different body is refused with `IdempotencyConflictError` (the 409-equivalent); export fails closed with `PreflightFailedError` when preflight itself would fail |
| `test_signed_export_envelope.py` | Wraps the exported instruction record as a signed `cassandre_contracts.Envelope` (`producer_product=ProducerProduct.COLLATERALOS`), verifies it independently against a `TrustBundle` built only from the public key, and proves a tampered payload is caught by `verify_envelope` via `CONTENT_HASH_MISMATCH` -- mirrors `packages/cassandre_contracts/tests/test_envelope.py`'s pattern |
| `test_sql_store.py` | `SqlAlchemyCollateralOSStore` against a real Postgres (skips cleanly without one -- see "Database tests" below): full workflow round trip, idempotent-export/self-approval/stale-binding parity with the in-memory store, and the genuine two-connection row-locking concurrency proof |

`apps/collateralos_api/tests/test_api.py` exercises the same workflow over
HTTP with FastAPI's `TestClient` against a fresh, isolated in-memory store
per test: the full obligation -> policy -> snapshot -> plan -> reserve ->
maker/checker -> preflight -> export happy path (including the idempotency
replay/conflict behavior over HTTP), a 422 on maker == checker, a 409 on a
plan-binding drift between maker and checker approval, a 409 on a genuine
`RESERVATION_CONFLICT`, a 404 on an unknown plan, and a 422
`PREFLIGHT_FAILED` on export attempted before approval.

Run everything:

```
uv run pytest products/collateralos apps/collateralos_api -q
```

### Database tests

Docker is now available, and `adapters/sql_store.py`'s
`SqlAlchemyCollateralOSStore` is a real, exercised SQLAlchemy + Postgres
implementation of the exact same public surface as the in-memory
`CollateralOSStore` -- same method names, signatures, return types, and
exceptions (`NotFoundError`, `PlanNotActionableError`,
`IdempotencyConflictError`, `PreflightFailedError`, `ReservationConflictError`,
`SelfApprovalError`, all reused, never redefined). `tests/test_sql_store.py`
runs the full obligation -> policy -> snapshot -> plan -> reserve ->
maker/checker approve -> preflight -> export workflow through it against a
real Postgres, verifies the exported envelope with
`cassandre_contracts.verify_envelope`, checks `verify_audit_chain()`, and
re-runs the in-memory store's own idempotent-export-replay,
same-key-different-body-conflict, self-approval-rejection, and
stale-approval-binding-invalidation cases against the SQL store for parity
-- reading assertions back from Postgres directly wherever practical, not
from the in-process objects a call happened to return.

`tests/conftest.py::collateralos_db_engine` and the newer
`collateralos_sql_engine` (which additionally runs `Base.metadata.
create_all`/`drop_all` around the test, mirroring `products/riskos/tests/
conftest.py`'s `sql_session` fixture) both look for
`CASSANDRE_TEST_DATABASE_URL` and skip cleanly (not a false pass) if it is
unset or unreachable, so every other test in this suite still has zero DB
dependency and always runs. Run the DB-backed tests:

```
CASSANDRE_TEST_DATABASE_URL=postgresql+psycopg2://collateralos_app:collateralos_dev_only_change_me@localhost:55432/collateralos \
  uv run pytest products/collateralos -q
```

Migrations: `products/collateralos/migrations/versions/0001_initial.py` is
kept in sync with `adapters/models.py` by hand (there is no autogenerate
step in this workflow); `alembic upgrade head` against the URL above has
been run and applies cleanly against a fresh database. Two columns were
added to the schema beyond what was originally declared, because the
in-memory `CollateralOSStore`'s behavior genuinely can't be reproduced
without them:

- `inventory_snapshot.positions_json` (`Text`, not null) -- a full
  serialization of every ingested `InventoryPosition` (asset class, issuer
  group, quantity, market value, staleness/encumbrance/recall/self-issued/
  affiliate-issued flags). The original schema's `inventory_balance` table
  only carries the *mutable ledger* fields the reservation protocol needs
  (`gross_settled`/`encumbered`/.../`reserved`); the *immutable eligibility*
  data `create_plan` re-derives on every call (the same thing
  `core/planner.py` consumes from `InventorySnapshot.positions` in the
  in-memory path) had nowhere to live at all.
- `reservation.approval_json` (`Text`, nullable) -- a serialization of the
  reservation's 1:1 `domain.approval.PlanApproval` (maker_id/checker_id/
  binding_hash/status). There is no separate approval table in this schema,
  and approval is intrinsically owned by exactly one reservation, so it
  lives on the row it belongs to.

Both follow the same pattern the schema already used for `PlanRow.
evidence_json` and `PolicyBundleRow.definition_json`: JSON in a `Text`
column for data that isn't broken out relationally, rather than inventing
several new narrowly-typed columns. `PlanRow.evidence_json` itself now also
carries `obligation_version`/`policy_version`/`inventory_snapshot_version`
(needed to reconstruct a `Plan` exactly, but likewise never given dedicated
columns) alongside the solver evidence it already held. No other table
changed, and every `Numeric`/`Decimal` column already in `models.py` was
left untouched -- nothing here is stored as `float`.

**Reservation concurrency, now proven against real Postgres.**
`adapters/sql_reservation.py::reserve_positions_for_update` replaces
`InMemoryInventoryRepository`'s single process-wide `threading.Lock` with a
real `SELECT ... FOR UPDATE` against `inventory_balance` rows (locked in the
same deterministic sorted order `domain.reservation.sorted_position_keys`
already documents), and delegates the actual reserve-or-conflict arithmetic
to the same pure, already-tested `domain.reservation.attempt_reserve` the
in-memory repository uses -- only the storage/locking underneath changed.

`tests/test_sql_store.py::
test_two_separate_connections_race_to_reserve_the_same_position_under_real_postgres_locking`
is a **genuine** multi-connection race, not simulated or mocked: two
independent `sqlalchemy.orm.Session` objects (each its own real connection
and transaction against the same live Postgres instance -- never a shared
session, never a shared transaction) both attempt to reserve 60 units of
one 100-unit position at the same instant (synchronized with a
`threading.Barrier`). Postgres's own row lock is what decides the outcome:
whichever transaction's `SELECT ... FOR UPDATE` acquires the lock first
proceeds and commits; the other's `SELECT ... FOR UPDATE` genuinely blocks
on the database until the winner commits and releases the lock, only then
reads the *post-commit* balance, and correctly raises
`ReservationConflictError` with a real shortfall (40, not 100 and not 0) --
proof it saw fresh state after being unblocked, not stale state or a
race decided by Python-level timing. The test asserts all three invariants
this closes out of `InMemoryInventoryRepository`'s "NOT a substitute for a
real multi-connection Postgres concurrency test" deferred-scope callout:
exactly one of the two racing attempts wins, the loser fails closed with
the correct shortfall, and the final `inventory_balance.reserved` value
persisted in Postgres reflects only the winner's reservation (no lost
update, no double-reservation).

`apps/collateralos_api` is unchanged and still wires the in-memory
`CollateralOSStore` by default; switching it to the SQL store is a separate
decision, not made this session.

## API

FastAPI app: `apps/collateralos_api/src/collateralos_api/main.py::create_app()`.
`app = create_app()` (module level) is the `uvicorn collateralos_api.main:app`
entry point; tests call `create_app()` directly to get a fresh, isolated
in-memory `CollateralOSStore` per test.

| Method | Path | Work item |
|---|---|---|
| POST | `/v1/obligations` | COL-001 |
| POST | `/v1/policy-bundles:validate` | COL-004 |
| POST | `/v1/policy-bundles/{policy_id}:activate` | COL-004 |
| POST | `/v1/inventory-snapshots` | COL-002/003 |
| POST | `/v1/plans` | COL-006..009 |
| GET | `/v1/plans/{plan_id}` | COL-006..009 |
| GET | `/v1/plans/{plan_id}/evidence` | COL-009 |
| POST | `/v1/plans/{plan_id}:reserve` | COL-010 |
| POST | `/v1/reservations/{reservation_id}:maker-approve` | COL-011 |
| POST | `/v1/reservations/{reservation_id}:checker-approve` | COL-011 |
| POST | `/v1/instructions/{instruction_id}:preflight` | COL-012 |
| POST | `/v1/instructions/{instruction_id}:export-internal` | COL-013 |

Errors map to conventional HTTP status codes (`NotFoundError`->404,
`ReservationConflictError`->409 with `error: "RESERVATION_CONFLICT"`,
`SelfApprovalError`->422, an `APPROVAL_INVALIDATED` `ValueError`->409,
`IdempotencyConflictError`->409, `PreflightFailedError`->422); the response
body always carries `{"error": "<CODE>", "detail": "<message>"}` plus any
error-specific extra fields (e.g. `shortfalls` on a reservation conflict,
`reasons` on a failed preflight).

## Security integration

`packages/cassandre_security` (the shared "foundational P0 slice" package:
SEC-01/02/04/05/08/10/14/15, classical crypto only -- AES-256-GCM +
Ed25519, no PQC this pass) is wired into this product's own application
layer via `products/collateralos/src/collateralos/application/security.py`.
`cassandre_security` itself is a read-only dependency of this product --
everything below is CollateralOS's own integration code, not a change to
that package.

**SEC-04, deny-by-default authorization.** `security.py::build_policy_engine`
builds one `cassandre_security.authorization.DenyByDefaultPolicyEngine` per
store instance, with a `PolicyRule` for exactly the eight mutating actions
named in this integration's brief: `ADD_OBLIGATION`, `ACTIVATE_POLICY`,
`ADD_SNAPSHOT`, `CREATE_PLAN`, `RESERVE_PLAN`, `MAKER_APPROVE`,
`CHECKER_APPROVE`, `EXPORT_INTERNAL`. Both `application/store.py::
CollateralOSStore` (in-memory) and `adapters/sql_store.py::
SqlAlchemyCollateralOSStore` call `security.py::authorize(...)` at the top
of every one of those methods; a DENY raises `AuthorizationDeniedError`
before any state is touched -- never just a log line -- and
`apps/collateralos_api`'s `create_app()` maps that to HTTP 403
(`{"error": "AUTHORIZATION_DENIED", "reason_code": "..."}"`). CollateralOS
has no identity-provider or RBAC integration of its own yet (a residual
already recorded in `packages/cassandre_security/THREAT_REGISTER.md`), so
every `PolicyRule` declares `required_roles=frozenset()` (no role gating
this pass) and `TENANT_ID` is a fixed constant (this product has no
multi-tenant model). The one concrete DENY path wired up this pass is
`security.py::InMemoryRevocationRegistry` -- a simple in-process
revoked-actor set (`store.authz_engine.revocation_checker.revoke(actor_id)`)
that `tests/test_security_authorization.py` exercises against both stores.

**SEC-05, maker/checker defense-in-depth.** CollateralOS already has its
own maker/checker binding protocol (`domain/approval.py::PlanApproval`,
COL-011), which independently rejects `checker_id == maker_id` as its very
first check inside `checker_approve` (`SelfApprovalError`) -- this already
fully covers the distinct-actor invariant. Both stores' `checker_approve`
now also call `cassandre_security.maker_checker.
enforce_distinct_natural_persons(maker_id, checker_id)` immediately before
that domain check, as an explicitly additional, independent
implementation of the same rule (belt-and-suspenders, not a second design
this product needed). Its `DistinctActorViolation` is caught and re-raised
as `SelfApprovalError` so the store's existing public exception contract
(and the API's 422 mapping) is unchanged.

**SEC-14, revocation fencing.** `security.py::fence_scope_key`/
`peek_fence_epoch` and `cassandre_security.revocation`'s own
`ensure_fence_exists`/`lock_fences_for_mutation` are wired into
`adapters/sql_store.py::SqlAlchemyCollateralOSStore` ONLY -- not the
in-memory `CollateralOSStore`. This is a deliberate scope decision: real
revocation fencing is a transactional-row-locking guarantee (`SELECT ...
FOR UPDATE` racing against a concurrent revocation's own lock on the same
row), which the in-memory store's plain Python dicts cannot honestly
provide; wiring a symbolic version in would either do nothing real or
require inventing a second ad hoc locking substrate distinct from the
one already proven for reservations. `add_obligation` provisions one fence
row per obligation (`scope_key = "product=collateralos;tenant=...;
obligation=<id>"`) the moment the obligation exists;
`reserve_plan`/`export_internal` each peek the current epoch and then
lock-and-verify it in the same transaction as their own commit, exactly
mirroring the `SELECT ... FOR UPDATE` reservation pattern
`adapters/sql_reservation.py` already established. The
`cassandre_security_fence` table (schema owned by `cassandre_security.
revocation.SecurityFenceRow`) is created by a new
`migrations/versions/0002_security_fence.py`, chained after
`0001_initial`; the `collateralos_sql_engine` test fixture
(`tests/conftest.py`) additionally creates/drops it via
`cassandre_security.revocation.Base.metadata` for the same
create-all/drop-all-per-test pattern the rest of this suite already uses
(no live alembic run in the test path, matching how `0001_initial` is
exercised today). `tests/test_security_revocation_fence.py` proves: a scope
marked `REVOCATION_EFFECTIVE` deterministically blocks a subsequent
`reserve_plan` call (`RevokedScopeError`, no reservation/balance mutation
left behind); a stale presented epoch is independently rejected
(`EpochMismatchError`); and a genuine two-connection race -- a real
`reserve_plan` call against one `Session`/connection racing a real
`advance_revocation` call against a second, independent one, synchronized
with `threading.Barrier` exactly like `test_sql_store.py`'s own
reservation-concurrency proof -- resolves to a DB-consistent outcome
under either legitimate commit ordering (JPMC-FINAL-01).

**SEC-10, canonical evidence v2 (additive, optional item).**
`security.py::build_export_evidence_v2` seals a `cassandre.evidence.v2`
(SHA-384, Ed25519) container for each real (non-replay) `export_internal`
call, using the same Ed25519 signing key the existing `cassandre_contracts`
envelope export already uses. This is purely additive: the existing
signed `Envelope` export (`test_signed_export_envelope.py`) is completely
unchanged. `CollateralOSStore.evidence_v2_records` (in-memory store) and
`SqlAlchemyCollateralOSStore.get_export_evidence_v2(...)` (SQL store, an
in-process cache -- no new DB column was added for this optional item, a
scope call recorded here rather than silently dropped) expose the sealed
container. `tests/test_security_evidence_v2.py` covers sealing, a
successful `verify_evidence` round trip, that a replay does not reseal a
new container, and that a tampered body is caught by `verify_evidence`.

New tests: `tests/test_security_authorization.py`,
`tests/test_security_revocation_fence.py`,
`tests/test_security_evidence_v2.py`. `cassandre_security` was added to
this product's own `pyproject.toml` dependencies only (not the workspace
root).

## Known simplifications and deferred scope (recorded, not silently dropped)

- **Single-currency valuation.** `core/planner.py` assumes every inventory
  position's `market_value` is already expressed in USD (the S1 CSA
  valuation currency); a `CASH_EUR` position's `quantity.unit` records its
  native face amount but no `money_types.FXConversion` lineage is modeled
  for inventory valuation this slice.
- **Objective simplification.** The plan's three-stage lexicographic
  objective (tail-liquidity/settlement risk, then cost, then churn) is
  simplified to a single declared per-lot cost-rate proxy.
- **`BANK_DELIVERS` only.** `domain/obligation.py::Obligation.__post_init__`
  raises `NotImplementedError` for `COUNTERPARTY_DELIVERS`; the receivable-call
  path (plan Section 12.7) is an explicit deferred scope gap.
- **Policy bundle is PROVISIONAL** (`docs/DECISION_REGISTER.md` DEC-COL-01):
  `SIM-POLICY-001` is a synthetic placeholder convention for this slice, not
  an approved production eligibility policy.
- **20-position corpus, not the full 600-position Section 9.5 corpus**
  (`tests/fixtures/inventory_corpus.py`); full breadth (SIM-002..009) is
  out of scope for this session per `docs/INTERPRETATION.md`.
- **No settlement transport** (ADR-012): `export-internal` never transmits
  anything anywhere; every exported record's payload carries an explicit
  `conformance_scope` note saying so, and `transmitted` is always `False`.
- ~~Reservation concurrency is proven at the application layer, not against
  real concurrent Postgres connections~~ **Closed this session**: Docker is
  now available, `adapters/sql_store.py` + `adapters/sql_reservation.py`
  implement the real `SELECT ... FOR UPDATE` path, and
  `tests/test_sql_store.py`'s two-connection test proves it against a real,
  live Postgres. See "Database tests" above. `InMemoryInventoryRepository`'s
  `threading.Lock` proxy is untouched and still what `apps/collateralos_api`
  runs on by default -- the SQL store is proven, not yet wired as the
  default.

## Web UI

`apps/collateralos_web/` is a plain HTML/CSS/vanilla-JS control UI (no build
step, no node_modules) mounted into `apps/collateralos_api`'s FastAPI app via
`fastapi.staticfiles.StaticFiles`, so one `uvicorn collateralos_api.main:app`
process serves both the API and the UI. The mount happens inside
`create_app()` itself (registered last, after every `@app.get`/`@app.post`
route, so it never shadows an API route) and is skipped gracefully if
`apps/collateralos_web/static/` doesn't exist -- `create_app()` still returns
a fresh, isolated in-memory store per call exactly as before, so
`apps/collateralos_api/tests/test_api.py`'s test-isolation pattern is
unaffected.

Pages (plan Section 12.9's list), each driving the real API end to end
(obligation -> policy validate/activate -> inventory snapshot -> plan ->
evidence -> reserve -> maker/checker approve -> preflight -> export):

- **Obligation queue** -- `POST /v1/obligations`, with a session-local log
  (the API has no obligation-list endpoint).
- **Inventory freshness and reconciliation** -- `POST /v1/inventory-snapshots`;
  staleness/encumbrance/recall flags for a given position surface once a plan
  has been run against that snapshot (read from the plan's verifier evidence).
- **Eligibility matrix and rule evidence** -- there is no standalone
  eligibility-preview endpoint in this slice, so this page parses
  `INELIGIBLE_POSITION:<id>:<reasons>` tokens out of
  `GET /v1/plans/{plan_id}/evidence`'s `verifier_notes` (COL-005/COL-008) and
  renders the full ordered rejection-reason list per position -- never a bare
  boolean.
- **Planner results with feasibility/gap** -- `POST /v1/plans`,
  `GET /v1/plans/{plan_id}`; renders the closed `SolverStatus` vocabulary
  (`FEASIBLE_OPTIMAL` / `FEASIBLE_BOUNDED_GAP` / `FEASIBLE_HEURISTIC_VERIFIED`
  / `INFEASIBLE_SOLVER_CONFIRMED` / `TIMEOUT_NO_FEASIBLE`) plus
  objective/gap/runtime.
- **Rejected assets and infeasibility diagnosis** -- same `verifier_notes`
  evidence, grouped by violation category (ineligible position, exceeds
  available inventory, minimum-lot violation, concentration cap exceeded,
  insufficient coverage, etc.) so an infeasible plan's root cause is legible.
- **Reservation/conflict view** -- `POST /v1/plans/{plan_id}:reserve`; a 409
  `RESERVATION_CONFLICT` renders the exact per-position `shortfalls`, not a
  generic error.
- **Maker/checker approval** -- `POST /v1/reservations/{id}:maker-approve`
  and `:checker-approve`.
- **Preflight checklist** -- `POST /v1/instructions/{id}:preflight`; shows
  `passed` plus the exact `failure_reasons` list.
- **Export history** -- `POST /v1/instructions/{id}:export-internal`, with a
  session-local export log (no export-history list endpoint exists either).

Two pages are explicit placeholders, per this session's check of
`products/collateralos/src/collateralos/` (see "Known simplifications" above)
rather than fabricated data:

- **Simulated settlement lifecycle** -- not implemented in this S1 slice
  (no settlement-transport component or post-export event model; ADR-012).
- **Counterfactual analysis and audit export** -- not implemented; there is
  no counterfactual re-solve endpoint or capability anywhere in this product
  or its API.

Tests: `apps/collateralos_web/tests/test_collateralos_web_mount.py` confirms
the UI is served at `/`, that API routes still win over the static mount,
that `create_app()` still isolates stores per instance, and that every
`/v1/...` path referenced by the UI's `app.js` matches a real registered API
route (parsed from the app's own route table, not hand-maintained).

Run everything (existing + web UI tests):

```
uv run pytest products/collateralos apps/collateralos_api apps/collateralos_web -q
```

## Session note

Partway through this session the host machine's `C:` drive was driven to
0 bytes free by an unrelated disk-space emergency (this repository had been
checked out under `C:\Users\...\OneDrive\Desktop\Cassandre-Platform`). The
repository was moved to `D:\Cassandre-Platform` (200GB+ free) and `uv`'s
cache redirected there to resolve it. This interruption did not require
simplifying or skipping any of the work described above -- every test file,
the FastAPI app, and this README were completed (and the full
`products/collateralos` + `apps/collateralos_api` suite, and the whole
monorepo suite, run green) after the move, from the code that already
existed at the old location.

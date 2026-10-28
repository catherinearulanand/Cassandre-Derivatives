# RiskOS

Margin-call calculation engine for the Cassandre Platform's S0-grade
demonstration slice. RiskOS resolves a calculation entity, counterparty,
agreement, and netting set from approved masters; reconciles a trade
population against a source-control manifest; aggregates exposure and
derives a Credit Support Balance strictly through a named method pack;
computes a margin call (`DELIVER`/`RETURN`/`NO_TRANSFER`); reconciles that
call against a counterparty-reported view; and exports a signed, hash-chained
result package. It implements plan Section 11's twelve-step workflow and
Section 11.5's reconciliation/dispute rules for one synthetic bilateral CSA
convention (`DEC-RSK-01`).

**STATUS: PROVISIONAL / SIMULATION-ONLY.** Every numeric convention (zero
threshold, USD 10,000 MTA, round-half-up whole-USD rounding, MTA-before-
rounding gating) is a synthetic placeholder recorded in
`docs/DECISION_REGISTER.md` (`DEC-RSK-01`) and `docs/methods/METHOD-PACK-001-provisional.md`,
never a real, approved production margin methodology. Do not present any
output of this module as a live risk figure.

## What was built, mapped to work items

This session's target slice was `RSK-001, RSK-002, RSK-003, RSK-004,
RSK-006, RSK-007, RSK-008, RSK-009, RSK-011, RSK-013` (`docs/INTERPRETATION.md`
Section 7.4) — "masters, manual term ingest, portfolio/MTM/collateral
packages, one method pack, exposure kernel, waterfall, reconciliation,
status, minimal API." Concretely:

| Work item | What it is | Where |
|---|---|---|
| RSK-001 | Legal entity master versions (calculation entity / counterparty identity, home currency, active-calculation-entity flag) | `src/riskos/domain/entities.py::LegalEntityMasterVersion`, `RiskOSService.register_legal_entity` |
| RSK-002 | Netting set master versions, resolved deterministically from calculation entity + counterparty + governing agreement (zero or multiple matches blocks the run) | `src/riskos/domain/entities.py::NettingSetVersion`, `src/riskos/core/netting.py` |
| RSK-003 | Manual agreement-terms ingest: validate a terms package against the implemented method pack and call currency, then approve it | `src/riskos/domain/entities.py::AgreementTermsPackage`, `RiskOSService.validate_agreement_terms` / `approve_agreement_terms` |
| RSK-004 | Portfolio (trade/MTM) snapshots and collateral-state packages (held/posted positions, pending/failed/cancelled transfers, disputed/undisputed amounts) | `src/riskos/domain/entities.py::PortfolioSnapshot`, `TradeMTM`, `CollateralStatePackage`, `CollateralPosition`, `PendingTransfer` |
| RSK-006 | The one implemented method pack: `SIM-CSA-SYMMETRIC-USD-001` — symmetric bilateral CSA, USD 0 threshold/IA both sides, USD 10,000 MTA, MTA-before-rounding, round-half-up whole USD | `src/riskos/core/method_pack.py` (`DEC-RSK-01`) |
| RSK-007 | Exposure kernel: trade-population reconciliation against the source-control manifest, then deterministic exposure aggregation and Credit Support Balance derivation (never a naked scalar balance) within one approved netting set | `src/riskos/core/population.py`, `src/riskos/core/exposure.py`, `src/riskos/core/gates.py` |
| RSK-008 | The twelve-step authoritative workflow that composes the above into a `CalculationRun` + `MarginWaterfall`, fail-closed (`COMPLETE`/`PARTIAL`/`FAILED`, never a silently weaker result) | `src/riskos/core/workflow.py` |
| RSK-009 | Reconciliation against a counterparty-reported call: exact-match tolerance (`DEC-OPS-02`), break classification (`TRADE_POPULATION`/`MTM`/`COLLATERAL_STATE`/`TIMING`/`ROUNDING`/`CALL_LOGIC`/`UNCLASSIFIED`), the six dispute-agreement cases, and the undisputed-amount rule | `src/riskos/core/reconciliation.py` |
| RSK-011 | Calculation-run status/lifecycle tracking (`NOT_RUN`/`COMPLETE`/`PARTIAL`/`FAILED`), retrievable by run id, including its waterfall | `src/riskos/domain/enums.py::CalculationStatus`, `RiskOSService.get_calculation_run` |
| RSK-013 | Minimal HTTP API over the above, backed by an in-memory repository for this slice | `apps/riskos_api/src/riskos_api/` |

Supporting infrastructure (not its own work item, but load-bearing):

- `src/riskos/application/service.py::RiskOSService` — the composition layer
  the API calls into; appends a hash-chained `AuditEvent` (`audit_protocol`)
  for every mutation and holds no HTTP/SQL specifics itself.
- `src/riskos/application/repositories.py::RiskOSRepository` — the
  repository protocol, implemented by `src/riskos/adapters/in_memory.py`
  (used by the API and tests) and `src/riskos/adapters/sql_repository.py`
  (SQLAlchemy-backed, for a real Postgres deployment; not exercised end to
  end in this slice — Docker Desktop was not functional on the build
  machine, see below).
- `src/riskos/application/result_package.py` / `envelope.py` /
  `serialization.py` — builds the exported result package and wraps it in a
  signed `cassandre_contracts.Envelope` (`RiskOSService.export_result_package`).
- `src/riskos/adapters/codec.py` — JSON-safe payload translation for the API
  layer.
- `products/riskos/migrations/` — Alembic migration for the SQL schema
  (`0001_initial_schema.py`), written but not run against a live database in
  this slice for the same Docker reason.

## RSK-X01: native valuation challenger (additive, `CHALLENGER` purpose)

Previously deferred (see "Explicitly deferred" below in earlier sessions);
now built as an explicitly **additive**, `CHALLENGER`-purpose-only
capability that never replaces, gates, or blocks the authoritative
`MARGIN_CALL` workflow above (plan Section 11.6). **STATUS: PROVISIONAL /
ADDITIVE**, `DEC-RSK-02` — see `docs/methods/RSK-X01-challenger-
provisional.md` and `docs/DECISION_REGISTER.md`.

- `src/riskos/domain/entities.py` — `MarketSnapshot` (pinned `as_of` date,
  `calendars` labels, one `DiscountCurve` per currency, `FxSpotRate`s),
  `InterestRateSwapTerms`, `FxForwardTerms`, `ChallengerComparison`,
  `ChallengerRun`. `ChallengerRun` is a structurally separate aggregate from
  `CalculationRun`/`MarginWaterfall` — different repository slot, different
  SQL table, its own `run_id` namespace.
- `src/riskos/core/challenger_pricing.py` — pure `Decimal` pricing math:
  single-curve simple-interest discounting (`DF(t) = 1/(1+z(t)*t)`), IRS
  fixed-leg PV plus a "floating leg reprices to par" simplification, and an
  FX forward priced by covered interest rate parity (plan 11.6's formula,
  used verbatim). Shares nothing with, and is not imported by,
  `riskos.core.workflow`/`method_pack`/`exposure`/`gates`.
- `src/riskos/core/challenger_workflow.py` — `execute_challenger_run`:
  compares each priced trade to its synthetic incumbent `TradeMTM` (when one
  exists) under `DEC-RSK-02`'s tolerance (greater of USD 25.00 absolute or
  10 bps of `abs(incumbent_pv)`), and assembles a `ChallengerRun` whose
  `actionability` is always `INDICATIVE_ONLY`.
- Only two trade families are priced: a single-currency fixed-for-floating
  IRS, and one deliverable FX forward. No options, exotic payoffs, credit
  derivatives, cross-currency swaps, or multi-curve/OIS discounting.
- `products/riskos/tests/test_challenger.py` — three hand-derived golden
  cases (a receive-fixed and a pay-fixed 2-year IRS against a flat 3%
  curve, and a deliverable EUR/USD FX forward), the full `MATCHED`/
  `WITHIN_TOLERANCE`/`BREAK`/`NOT_AVAILABLE` comparison-status matrix, and
  `test_challenger_run_does_not_affect_margin_call_run` — the test that
  proves running a `CHALLENGER` valuation never alters an existing
  `MARGIN_CALL` `CalculationRun`.
- API: `POST /v1/market-snapshots`, `GET /v1/market-snapshots/{id}`,
  `POST /v1/challenger-runs`, `GET /v1/challenger-runs/{run_id}` — entirely
  separate routes from the `MARGIN_CALL` calculation-run routes.
- No new dependency was needed (pure `Decimal`/stdlib math), so
  `products/riskos/pyproject.toml` is unchanged.

## RiskOS web UI (`apps/riskos_web`)

Previously "no frontend" (see "Explicitly deferred" below in earlier
sessions); now a plain HTML/CSS/vanilla-JS static UI (no build step, no
`node_modules`), covering plan Section 11.9's page list: input-package
readiness and freshness; entity/netting-set membership; margin run and
waterfall (rendering all five plan-11.7 status dimensions side by side,
never collapsed into one indicator); trade-to-total drilldown;
reconciliation breaks and undisputed amount; method pack/support matrix;
RSK-X01 challenger comparison; evidence export. The historical "known then/
corrected now" replay page is a clearly labeled **not-yet-implemented**
placeholder: plan Section 8.3's bitemporal `known_from`/`known_to` dimension
does not exist on any aggregate in `src/riskos/domain/entities.py` in this
slice (every aggregate is an immutable versioned snapshot with at most
`effective_from`/`effective_to`), so a real replay page would have to
fabricate a second timeline — this page says so instead.

Every action in the UI calls a real `riskos_api` endpoint via `fetch`; there
is no mocked data. `apps/riskos_api/src/riskos_api/main.py` mounts
`apps/riskos_web` at `/ui` via `fastapi.staticfiles.StaticFiles`, so
`uvicorn riskos_api.main:app` serves both the API and the UI from one
process. `apps/riskos_api/tests/test_api.py` verifies the mount serves
`index.html`/`app.js`/`style.css` and cross-checks every `/v1/...` path
literal in `app.js` against the app's actually-registered routes.

## API surface (`apps/riskos_api`)

`apps/riskos_api/src/riskos_api/main.py` is a thin FastAPI composition root —
request/response translation only; all business logic stays in
`riskos.core`/`riskos.application`. Routes:

- `POST /v1/legal-entity-versions`
- `GET /v1/legal-entity-versions`
- `GET /v1/legal-entity-versions/{entity_id}`
- `POST /v1/netting-set-versions`
- `GET /v1/netting-set-versions`
- `POST /v1/agreement-terms:validate`
- `POST /v1/agreement-terms:approve`
- `GET /v1/agreement-terms/{agreement_id}`
- `POST /v1/portfolio-snapshots`
- `GET /v1/portfolio-snapshots/{snapshot_id}`
- `POST /v1/collateral-state-packages`
- `GET /v1/collateral-state-packages/{package_id}`
- `GET /v1/method-packs`
- `POST /v1/calculation-runs`
- `GET /v1/calculation-runs/{run_id}`
- `GET /v1/calculation-runs/{run_id}/waterfall`
- `POST /v1/reconciliations`
- `GET /v1/reconciliation-cases/{case_id}`
- `POST /v1/result-packages:export`
- `POST /v1/market-snapshots` (RSK-X01)
- `GET /v1/market-snapshots/{snapshot_id}` (RSK-X01)
- `POST /v1/challenger-runs` (RSK-X01)
- `GET /v1/challenger-runs/{run_id}` (RSK-X01)

`apps/riskos_api/src/riskos_api/dependencies.py` wires a process-wide
`InMemoryRiskOSRepository` (no Postgres required to run the API in this
slice); swapping in `SqlAlchemyRiskOSRepository` needs no route changes,
since every route depends only on the `RiskOSRepository` protocol.
`apps/riskos_api/src/riskos_api/main.py` also mounts `apps/riskos_web` at
`/ui` via `fastapi.staticfiles.StaticFiles`, so one `uvicorn
riskos_api.main:app` process serves both the API and the web UI.

`apps/riskos_api/tests/test_api.py` (new this session) exercises the API
over real HTTP via FastAPI's `TestClient`: the full `MARGIN_CALL` workflow
and the new read endpoints above, the RSK-X01 market-snapshot/challenger-run
endpoints (reproducing the IRS golden case's `19967.03` PV through the wire
format), the `/ui` static mount, and the independence property (a
`CHALLENGER` run over HTTP does not alter an existing `MARGIN_CALL` run).

## Running the tests

```
uv run pytest products/riskos apps/riskos_api apps/riskos_web -q
```

Run from the repository root (`D:\Cassandre-Platform`). Result, as of this
session's security-integration pass: **74 passed, 8 skipped** with
`CASSANDRE_TEST_DATABASE_URL` unset (the `sql_session`-fixture-backed tests
in `test_db_repository.py` and `test_security_integration.py` skip cleanly —
see below); **82 passed, 0 skipped** with it set to a reachable RiskOS
Postgres instance, e.g.:

```
CASSANDRE_TEST_DATABASE_URL="postgresql+psycopg2://riskos_app:riskos_dev_only_change_me@localhost:55432/riskos" \
  uv run pytest products/riskos apps/riskos_api apps/riskos_web -q
```

Either way, **no test failures**. (`apps/riskos_web` has no Python tests of
its own — its static assets are exercised through
`apps/riskos_api/tests/test_api.py`'s static-mount checks.)

Test files, by what they pin down:

- `test_method_pack.py` — the golden-case delivery amount, the MTA-boundary
  inclusive/exclusive convention, and the method pack's provisional flag.
- `test_gates.py` — authority/completeness/freshness/currency/duplicate/
  support-matrix gates.
- `test_population.py` — trade-population-vs-manifest reconciliation.
- `test_reconciliation.py` — break classification and all six dispute cases,
  including the undisputed-amount rule and its `lower_common_amount`
  exception (gated off by this method pack).
- `test_workflow.py` — the twelve-step workflow end to end: the golden
  matched scenario, a reconciliation-break scenario, and every blocking path
  (unsupported agreement, unresolved method pack, trade-population mismatch,
  zero/multiple netting-set matches, unsupported calculation purpose).
- `test_result_package_envelope.py` — signed envelope verification and
  tamper rejection.
- `test_service.py` — the full `RiskOSService` workflow against the
  in-memory repository, including a second reconciliation submission against
  a later-arriving counterparty call, and the not-found error path.
- `test_challenger.py` — the RSK-X01 native valuation
  challenger: hand-derived IRS (receive-fixed and pay-fixed) and FX-forward
  golden cases, the `MATCHED`/`WITHIN_TOLERANCE`/`BREAK`/`NOT_AVAILABLE`
  comparison-status matrix, the no-supported-trades `FAILED` path, the
  missing-discount-curve `NOT_AVAILABLE` (never a crash) path, and
  `test_challenger_run_does_not_affect_margin_call_run` — the independence
  proof (re-verified unchanged by this session's security-integration pass).
- `test_db_repository.py` — `RiskOSService` round-tripped through the real
  `SqlAlchemyRiskOSRepository`/Postgres instead of the in-memory adapter;
  skips cleanly without `CASSANDRE_TEST_DATABASE_URL`.
- `test_security_integration.py` (new this session — see "Security
  integration" below) — SEC-04 authorization DENY actually blocks
  `run_calculation`/`export_result_package`/`approve_agreement_terms`, a
  revoked actor is blocked even with a correctly-granted role, a
  non-default actor with a custom grant can be authorized (proving this is
  a real per-action check, not a rubber stamp); SEC-14 revocation fencing
  genuinely blocks a mutation presenting a stale epoch against real
  Postgres, and accepts the current one; SEC-10 canonical evidence v2
  round-trips for a `ChallengerRun` and rejects a mutated body/stripped
  signature, and is proven independent of the existing V1 export path;
  SEC-15 witness checkpoints are gapless and reject a forked extension.
  The DB-dependent cases skip cleanly without `CASSANDRE_TEST_DATABASE_URL`.
  `apps/riskos_api/tests/test_api.py` also has one HTTP-level test proving
  a SEC-04 DENY maps to a real `403` response, not just a service-layer
  exception.

## Security integration (`cassandre_security`, this session)

RiskOS wires in the shared `packages/cassandre_security` package's
foundational P0 slice (SEC-01/02/04/05/08/10/14/15 — see that package's own
`README.md`/`COMPONENTS.md`/`THREAT_REGISTER.md`), matching the plan this
was scoped against. `cassandre_security` itself was treated as a read-only
shared dependency throughout (other agents were integrating it into
ClauseOS/CollateralOS concurrently this session) — nothing under
`packages/cassandre_security/` was modified. All RiskOS-local naming,
allowlist, and wiring decisions live in the new
`src/riskos/application/security.py` module; read its module docstring for
the full rationale, most of which is only summarized below.

- **SEC-04 (deny-by-default authorization)** — `RiskOSService` now calls
  `cassandre_security.authorization.DenyByDefaultPolicyEngine.decide(...)`
  before `register_legal_entity`, `approve_agreement_terms`,
  `run_calculation`, `submit_reconciliation`, `export_result_package`, and
  `run_challenger_valuation` execute. A DENY raises
  `RiskOSAuthorizationDeniedError` (a real, blocking exception, never a
  logged warning), which `apps/riskos_api` maps to HTTP `403`. RiskOS has
  no OIDC/session-identity layer yet (unchanged — see "Explicitly
  deferred" below), so each gated method takes an optional
  `actor: RiskOSActorContext | None` that defaults to a fixed
  simulation-constant actor pre-granted every role this pass defines — the
  same kind of fixed constant `RiskOSService._actor_id` already used for
  audit events, so every existing caller that never passes `actor=` (the
  API, every `riskos_fixtures`-based test) is unaffected. `run_calculation`'s
  authorization purpose is the caller's own `CalculationPurpose` value
  (all five members are allowlisted), deliberately kept independent of
  "is this purpose implemented" (`riskos.core.gates`'s job) — gating only
  `MARGIN_CALL` would have turned the already-tested unsupported-purpose
  `FAILED` outcome into an authorization DENY instead, and that
  observable behavior was left unchanged.
- **SEC-14 (revocation fencing)** — `run_calculation` and
  `export_result_package` now lock a `cassandre_security.revocation`
  fence row (`SecurityFenceRow`, scope key
  `product=riskos;tenant=<tenant>;netting_set=<netting_set_id>`) via
  `lock_fences_for_mutation` before their mutating write, and
  `register_netting_set` provisions that scope's fence
  (`ensure_fence_exists`) at registration time. **Integration approach
  chosen: a new Alembic migration** (`migrations/versions/
  0003_security_integration.py`, revising `0002`) that creates
  `cassandre_security_fence` (and `cassandre_security_witness_checkpoint`,
  for SEC-15 below) in RiskOS's own `riskos` database, mirroring
  `cassandre_security.revocation.SecurityFenceRow`/`witness.WitnessRow`
  1:1 — each product provisions its own copy of these shared-package
  tables in its own database, consistent with this repo's existing "one
  database per product" rule. This was chosen over, e.g., a shared
  cross-product fence table, because RiskOS's database is already
  product-owned and isolated, and a migration is the same mechanism this
  product already uses for its own schema changes (`0001`, `0002`).
  These two hooks (`lock_fences_for_mutation`/`ensure_fence_exists`, plus
  `submit_witness_checkpoint` below) are added to
  `SqlAlchemyRiskOSRepository` only — deliberately *not* part of the
  `RiskOSRepository` Protocol — so `RiskOSService` looks them up via
  `getattr(self._repo, "...", None)` and treats a missing hook as a no-op.
  This means fencing/witnessing is only exercised when RiskOS runs against
  real Postgres (via `SqlAlchemyRiskOSRepository`); the in-memory
  repository the API and most tests use has no fence table at all and
  skips both, consistent with this codebase's existing rule that
  domain/core/application tests never require a live database. A DENY
  from the fence (stale epoch, unknown scope, or `REVOCATION_EFFECTIVE`
  state) is wrapped as `RiskOSRevocationFenceError` (so `apps/riskos_api`,
  which does not itself depend on `cassandre_security` per this pass's
  scope constraints, can catch a RiskOS-local exception type) and mapped
  to HTTP `409`. **Tests** provision and exercise this against the real
  session-provided Postgres database via the *same* `sql_session` fixture
  `test_db_repository.py` already used (no new fixture invented) —
  extended in `conftest.py` to also `create_all`/`drop_all`
  `cassandre_security.revocation.Base`/`witness.Base`'s own metadata
  alongside RiskOS's own `Base`, matching how
  `packages/cassandre_security/tests/conftest.py`'s own fixtures already
  provision those same tables for that package's tests. (Production
  deployments run the Alembic migration instead; tests never run Alembic,
  matching this product's existing test/migration split.)
- **SEC-10 (canonical evidence v2)** — a **new, additive** capability,
  `RiskOSService.seal_challenger_run_evidence`/
  `verify_challenger_run_evidence`, seals/verifies a `ChallengerRun` in the
  new `cassandre.evidence.v2` (SHA-384, Ed25519) container via
  `cassandre_security.canonical_evidence.seal_evidence`/`verify_evidence`.
  This does **not** touch `export_result_package`'s existing signed
  `cassandre_contracts.Envelope` (V1, SHA-256) path at all — no existing
  signed object's format changes, and nothing is migrated. `ChallengerRun`
  was chosen (over retrofitting the MARGIN_CALL export path) because it is
  a newer, additive, already-independent RSK-X01 aggregate built earlier
  this session — a natural fit for a new, additive evidence format, with
  no risk to the authoritative export path's existing tests
  (`test_result_package_envelope.py`, unchanged). The evidence payload is
  built by a new `challenger_run_to_evidence_payload` in
  `riskos.application.serialization`, deliberately independent of
  `riskos.adapters.codec`'s SQL storage codec for the same aggregate (the
  signed payload's shape should never silently change just because the
  storage codec's shape does).
- **SEC-15 (independent witness, lighter touch)** — `RiskOSService`
  submits a `cassandre_security.witness.submit_checkpoint` after *every*
  audit event `_record_audit_event` appends (when the repository is
  witness-capable), using that event's `sequence`/`event_hash` as the
  witnessed `sequence`/`head_hash` and `product_chain_id="riskos"`.
  **Deviation from the brief's literal wording** ("record a checkpoint
  after each `CalculationRun` is saved"): the witness store requires
  exactly gapless, monotonically increasing sequence numbers per chain
  (it "rejects forked/gapped chain extensions"), but RiskOS's
  `audit_protocol` chain assigns one global sequence per product across
  *every* action (`register_legal_entity`, `run_calculation`,
  `export_result_package`, `run_challenger_valuation`, etc.), not just
  `CalculationRun` saves. Checkpointing only `CALCULATION_RUN_EXECUTED`
  events would submit sparse sequence numbers and raise
  `WitnessGapError` the first time any other audit event landed in
  between (caught by this session's own DB-backed tests before this
  README was written — see "Bugs/observations" below for why this is
  arguably a real rough edge in `cassandre_security.witness` itself, not
  just a RiskOS integration mistake). Checkpointing the whole shared chain
  is a superset of "checkpoint every `CalculationRun` save" and does not
  weaken anything the brief asked for.

### Deviations from the brief

- The revocation-fence "presented epoch" a caller asserts is exposed as an
  explicit `fence_epoch: int = 0` parameter on `run_calculation`/
  `export_result_package`, rather than derived from a real session/client
  state (RiskOS has no such session concept yet — see "Explicitly
  deferred"). Defaulting to `0` (the epoch an unrevoked scope starts at)
  keeps every existing caller's behavior unchanged.
- The revocation-fence lock is acquired on the same `Session` the SQL
  repository's own save call commits on (so the lock genuinely holds
  through that commit), rather than a broader same-transaction refactor of
  `RiskOSService`/`SqlAlchemyRiskOSRepository`'s unit-of-work boundaries
  (each `save_*` method already commits its own transaction, a
  pre-existing simplification this pass did not change).
- SEC-15's witness wiring checkpoints the whole shared per-product audit
  chain rather than only `CalculationRun` saves specifically (see above).
- No new HTTP endpoint exposes `seal_challenger_run_evidence`/
  `verify_challenger_run_evidence` — this pass adds the capability and
  tests it at the service layer (round-trip + tamper rejection), per the
  brief's explicit minimum bar, and stops there to keep the change tightly
  scoped.

### Bugs/observations in `cassandre_security` (reported, not fixed — it is
a read-only shared dependency for this pass)

- `cassandre_security.witness.submit_checkpoint`'s gapless-sequence
  contract is easy for an integrating product to violate by accident: any
  product whose natural "interesting event" stream is a *subset* of a
  shared, globally-sequenced audit/event chain (as RiskOS's
  `CALCULATION_RUN_EXECUTED` events are, interleaved with other action
  types on the same per-product `audit_protocol` chain) will hit
  `WitnessGapError` the first time it tries to checkpoint only that
  subset. The module's docstring/tests describe the fork/gap rejection
  clearly, but nothing flags this "you must checkpoint literally every
  event on the chain, not a filtered subset" requirement, and it is not
  obvious until you (as this session's build did) actually put it in
  front of a real chain that occasionally has other event types in
  between. Consider documenting this constraint explicitly in
  `witness.py`'s module docstring, or offering an explicit
  "sparse/keyframe" checkpoint mode for products that only want to attest
  a subset of a larger chain.

## Explicitly deferred (not attempted in this session)

Left as open backlog with its plan Section 18.2 gate criteria intact, per
`docs/INTERPRETATION.md` Section 7.4/8:

- **RSK-005** — market-data ingestion (FX/curve/vol sourcing beyond the
  RSK-X01 `MarketSnapshot`'s pinned discount curves/FX spots; freshness
  policy beyond the fixed `FRESHNESS_WINDOW_HOURS` constant already used by
  the gates and, now, by the challenger workflow).
- **RSK-010, RSK-012, RSK-014** — not attempted; no code in this slice
  addresses them.
- **SIM-004** — the full ~300-trade synthetic corpus. This slice's tests use
  small, hand-built fixtures (`products/riskos/tests/riskos_fixtures.py`),
  not the full simulation factory.
- **Additional method packs** — only `SIM-CSA-SYMMETRIC-USD-001` is
  implemented. Non-zero threshold/IA, non-USD call currency, and any
  tolerance-based (`WITHIN_TOLERANCE`) reconciliation convention are all
  unimplemented; `DEC-OPS-02`'s exact-match-only rule is specific to this
  one pack.
- **OIDC / real session identity** — the API still has no authentication
  layer or request-identity resolution; `RiskOSService`'s `actor_id`/
  `issuer` remain fixed simulation constants. This session's security
  integration (see "Security integration" above) adds a real
  `cassandre_security`-backed deny-by-default *authorization* engine
  (SEC-04) with a `RiskOSActorContext` callers can pass explicitly, but
  that context is still constructed directly by a caller/test, not parsed
  from a verified request identity — wiring an actual OIDC/session layer
  that populates it from real HTTP requests remains deferred.
- **Full Section 16.3 acceptance suite** — this session's tests are
  hand-picked, plan-derived unit/integration tests proving the implemented
  slice's stated behavior, not the full adversarial/S1-acceptance corpus
  described in plan Section 16.
- **Live Postgres exercise** — `sql_repository.py` and the Alembic migrations
  (including this session's `0002_challenger_run.py` adding `market_snapshot`/
  `challenger_run`) are written but were never run against a real database in
  this session (Docker Desktop was not functional on the build machine); only
  the in-memory repository has been exercised, by both the tests and the API.
- **Historical "known then / corrected now" replay** — plan Section 8.3's
  bitemporal record model (`known_from`/`known_to`) is not implemented on any
  aggregate in this slice; `apps/riskos_web`'s replay page is a clearly
  labeled placeholder rather than a fabricated view (see "RiskOS web UI"
  above).
- **`GET /v1/support-matrix`** — plan Section 11.8 lists this as a stretch
  API; this slice's UI instead renders a support matrix derived by reading
  `riskos.core.gates.run_support_matrix_gate` and
  `riskos.core.challenger_workflow` directly, labeled as not a live endpoint.
- **Real holiday calendars / business-day adjustment** — the RSK-X01
  challenger's `MarketSnapshot.calendars` are simulation-only labels; no
  actual calendar logic is implemented (plan 11.6's single-curve scope).

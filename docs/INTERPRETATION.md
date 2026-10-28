# Pre-coding response (Plan Section 25.1)

This is the mandatory response required before any implementation, per Section 25.1
of `AETHER-Controlled-Pilot-Implementation-Plan.md`. Nothing below is a claim that
these items are finished — it is the interpretation, contradiction list, and plan
that gate what gets built next.

**Naming note:** the platform brand is **Cassandré** (ASCII form `cassandre` in
code/identifiers). The source plan document still refers to the optional
federation layer and the fictional institution as "AETHER" — everywhere this
document says "AETHER" it means the same optional federation layer, now
branded Cassandre; plan Section/ADR/work-item IDs (`AET-*`) are kept as-is
purely as traceability keys back to the unedited source document.

## 1. Interpretation of the pilot boundary

- This is **S1 controlled simulation** only (plan Section 1.4). Nothing here is a
  production trading, margin, or settlement system, and nothing here is evidence
  toward S2/S3.
- Three standalone products (ClauseOS, RiskOS, CollateralOS) plus an optional
  Cassandre federation layer. Each product must independently start, authenticate,
  persist, audit, expose an API, and complete its primary workflow with the other
  three unavailable. This governs every dependency decision below: product
  `core`/`domain` packages never import a sibling product or Cassandre.
- All data is synthetic (`CASSANDRE_SIM_BANK_NA` / `CASSANDRE_SIM_BANK_IFSC`,
  `SIM-*` identifier namespace). No real institution, agreement, LEI, account, or
  price ever enters the system.
- No settlement transmission exists or is planned; CollateralOS export is
  internal-only and never claims network-format conformance (SWIFT/ISO 20022/etc).

## 2. Contradictions / gaps found in the source document

1. Section 9.3 says "generate 300 trades" but Section 1.3's boundary table says
   "250-400 initially." Resolution: 300 is the initial corpus target; 250-400 is
   the acceptable range, 500 is the hard pilot maximum. Not blocking.
2. Section 9.5 says "600 positions" but Section 1.3 says "400-750 initially."
   Same resolution pattern: 600 is the initial target.
3. Section 5 recommends Python "3.13-supported environment"; the only Python
   available on this machine is 3.14.3. Resolution: pin the project to run on
   3.13 semantics where possible and note the discrepancy in the release
   manifest; do not silently claim 3.13. **Flagged as a provisional decision**,
   see Decision Register item `DEC-ENV-01`.
4. Section 6.1 assumes "one controlled Linux workstation." This session runs on
   Windows with Docker Desktop available. Containers run Linux images under
   Docker regardless of host OS, so ADR-002/ADR-012 targets (Postgres, Compose)
   are unaffected, but any host-level hardening in Section 6.4 (rootless
   containers, AppArmor/seccomp) is Linux-host-specific and cannot be fully
   asserted on a Windows host. **Flagged**, see `DEC-ENV-01`.
5. Keycloak (ADR-009) is heavyweight to configure correctly (realms, clients,
   role mappings) for a single-session first slice. I am scaffolding it in
   Compose with a realm-import file so it is real and running, but the initial
   slice's API auth middleware validates locally-issued dev JWTs shaped like
   Keycloak-issued OIDC tokens until the realm import is verified end-to-end.
   This is a real gap against ADR-009, not a silent substitution — tracked as
   `FND-005` remaining work, not claimed done.

## 3. Proposed ADR list

See `docs/adr/`. ADR-001 through ADR-015 restate the plan's Section 4 table as
committed decisions; each file also records the concrete S1 implementation
choice (exact library/version/config) and any known deviation.

## 4. Repository / module dependency graph

```
packages/money_types        <- no internal deps
packages/audit_protocol      <- depends on money_types (canonicalization needs Decimal rules)
packages/cassandre_contracts <- depends on money_types, audit_protocol (envelope schema)
packages/simulation_factory  <- depends on money_types, cassandre_contracts (fixture generation only; not imported by products' domain/core)

products/clauseos/{domain,core}      <- no deps on packages except money_types, audit_protocol (pure)
products/clauseos/{application,adapters} <- domain/core + cassandre_contracts + FastAPI/SQLAlchemy
products/riskos/*        <- same pattern, independent of clauseos
products/collateralos/*  <- same pattern, independent of clauseos/riskos

apps/*_api  <- thin composition root: wires adapters, no business logic
apps/*_web  <- calls only its own product's public API

apps/cassandre_control_api <- depends on packages/cassandre_contracts only;
                           imports no product-internal module (enforced by CI import-graph test, FND-001 test)
```

No product `core`/`domain` package may import `fastapi`, `sqlalchemy`, a clock,
a model SDK, or a sibling product. This is enforced by `scripts/check_import_graph.py`
run in CI (work item `FND-001`).

## 5. Database ownership map

| Database | Owner service | Tables (initial slice) |
| --- | --- | --- |
| `clauseos` | clauseos_api | agreement_bundle, document_node, evidence_span, assertion, review_case, policy_release, audit_event, outbox |
| `riskos` | riskos_api | legal_entity_version, netting_set_version, agreement_terms_package, portfolio_snapshot, trade_mtm, collateral_state_package, calculation_run, margin_waterfall, reconciliation_case, audit_event, outbox |
| `collateralos` | collateralos_api | obligation, policy_bundle, inventory_balance, inventory_snapshot, plan, allocation_line, reservation, settlement_instruction, audit_event, outbox |
| `cassandre_control` | cassandre_control_api | correlated_case, envelope_receipt, federated_audit_export |
| `keycloak` | Keycloak container | Keycloak-managed schema; own backup/restore procedure |

Each has a distinct Postgres role with grants scoped to its own database only;
`scripts/verify_db_isolation.py` (work item `FND-004`) attempts sibling-credential
access and asserts it is rejected.

## 6. Public schema inventory (initial slice)

- `schemas/public/envelope.schema.json` — the Section 8.1 envelope
- `schemas/public/money.schema.json` — Money/Quantity/Price/FXConversion
- `schemas/public/clauseos.policy_release.schema.json`
- `schemas/public/riskos.margin_waterfall.schema.json`
- `schemas/public/collateralos.instruction_record.schema.json`

Each is versioned (`schema_version` semver) and has a compatibility test in
`packages/cassandre_contracts/tests`.

## 7. Milestone plan tied to work IDs

This session targets, in order:

1. `FND-001..FND-004, FND-006, FND-007, FND-008, FND-009` — import graph test,
   envelope/content hashing, money primitives, DB isolation, maker-checker
   protocol, audit chain, idempotency/outbox, provenance enforcement.
2. `SIM-001` — synthetic institution/namespace only (enough for the slice;
   full corpus SIM-002..009 is out of scope for this session and left as
   backlog with its own gate, since it alone is a multi-week deliverable per
   Section 18.2 Phase 2).
3. `CLO-001, CLO-002, CLO-005, CLO-006, CLO-008, CLO-009, CLO-010, CLO-012, CLO-013` —
   ingest guard, bundle/authority graph, rule proposer, replay proposer,
   structural verifier, materiality model, review workflow, release/export,
   minimal API — enough for the Section 25.5 slice (one bundle -> evidence ->
   maker/checker -> signed release). `CLO-X01` (local model) explicitly
   deferred per plan.
4. `RSK-001, RSK-002, RSK-003, RSK-004, RSK-006, RSK-007, RSK-008, RSK-009,
   RSK-011, RSK-013` — masters, manual term ingest, portfolio/MTM/collateral
   packages, one method pack, exposure kernel, waterfall, reconciliation,
   status, minimal API.
5. `COL-001..COL-006, COL-008..COL-014` — obligation ingest, inventory ledger,
   policy compiler, eligibility matrix, LP preview, independent verifier,
   solver evidence, reservation transaction, maker-checker binding,
   preflight/export, minimal API. `COL-007` (MIP) included using HiGHS via
   `highspy` if it installs cleanly on this machine; otherwise LP-only for
   this slice with the gap tracked, not hidden.
6. `AET-001, AET-002, AET-003, AET-006` — client adapters, envelope
   validation/routing, correlated case view, independence tests.

Everything else in the WBS (full corpus SIM-003..009, CLO-007/011/014 breadth,
RSK-005/010/012/014, COL-015, full Phase 6/7 security+resilience+30-day run) is
explicitly **not** attempted in this session and is left as open backlog with
its Section 18.2 gate criteria intact.

## 8. Test strategy and acceptance-evidence map

For each work item above: a failing test is written first from the plan's
stated behavior (e.g., "missing incorporated document causes BUNDLE_INCOMPLETE"),
then the smallest domain code to pass it, then the adapter. Evidence for "done"
on this session's scope is: passing pytest suite committed alongside the code,
plus a runnable end-to-end script (`scripts/run_initial_slice.py`) that exercises
the three products against a real (containerized) Postgres and prints the
resulting envelopes and audit-chain verification result. This is explicitly
**S0-grade demonstration evidence for a hand-picked slice**, not the S1
acceptance corpus described in Section 2 and Section 16, which requires the
full simulation factory and adversarial suites this session does not build.

## 9. Risks requiring a human finance/security/model-risk decision

Recorded in `docs/DECISION_REGISTER.md`. Highlights:

- Exact CSA sign/threshold/MTA/rounding convention for the one method pack
  built in this slice (`DEC-RSK-01`).
- Collateral eligibility/concentration/wrong-way rule set for the one policy
  bundle built in this slice (`DEC-COL-01`).
- Target pilot hardware / long-term hosting (`DEC-ENV-01`).
- Whether local-model inference is ever included, or replay/rules-only
  permanently for S1 (`DEC-CLO-01`).

Because this is a wholly synthetic, non-operational slice with no real
counterparty, the specific numeric conventions chosen for `DEC-RSK-01` and
`DEC-COL-01` are **provisional placeholders** clearly labeled as such in code
and docs, not silent defaults presented as approved — they exist only so the
slice has something concrete to compute against, and every place they appear
is traceable to the decision register entry.

## 10. Smallest end-to-end slice (adopting plan Section 25.5 verbatim)

- ClauseOS: one born-digital synthetic CSA bundle -> evidence -> maker/checker -> signed policy release.
- RiskOS: manually imported equivalent terms + five external MTMs + collateral state -> margin waterfall -> reconciliation break/evidence.
- CollateralOS: manually imported obligation + twenty inventory positions -> verified LP/MIP plan -> atomic reservation -> maker/checker -> preflight -> export.
- Cassandre: validate and correlate the three signed envelopes without calling internal code.

Only after this slice passes does corpus/UI breadth expand — per the plan, not
as a shortcut invented here.

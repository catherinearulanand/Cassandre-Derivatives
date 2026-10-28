# ClauseOS — Section 25.5 initial build slice

This is the smallest end-to-end ClauseOS slice defined by plan Section 25.5
and `docs/INTERPRETATION.md` item 10:

> one born-digital synthetic CSA bundle -> evidence -> maker/checker -> signed policy release.

It is **not** the full ClauseOS product described in plan Section 10. It is not
production-ready, and no claim of test count, timing, or completeness beyond
what is stated below should be inferred from this document.

## What was built, mapped to work items

- **CLO-001 (ingest guard) / CLO-002 (bundle & authority graph, partial)**:
  `clauseos.domain.bundle.AgreementBundle`, `DocumentNode`, `BundleManifest`.
  A bundle only becomes analyzable after its manifest is explicitly approved
  (`approve_manifest`), and `check_completeness()` enforces plan Section
  10.1's incorporated-by-reference rule: a manifest that names a document
  not present in the bundle moves the bundle to the non-publishable
  `BUNDLE_INCOMPLETE` state (`clauseos/domain/bundle.py`). Full
  `AuthorityEdge`/legal-precedence graph (Section 10.1) is **not** built —
  only `amends/supplements/supersedes` as a flat `DocumentReference`, not a
  traversable graph.
- **CLO-005 (deterministic rule proposer)**: `clauseos/core/rule_proposer.py`
  — regex/keyword extraction for Threshold, MTA, and Notification Deadline
  over normalized plain-text/Markdown. No model/LLM call anywhere in this
  package (ADR-006, Decision Register `DEC-CLO-01`). The replay proposer
  ADR-006 also allows is **not** built this session.
- **CLO-006 (structural verifier)**: `clauseos/core/verification.py` —
  confirms an `EvidenceSpan`'s offsets and snippet match the current
  document text exactly, and that the span's `source_content_hash` still
  matches the document (so a changed source document is caught, not
  silently trusted).
- **CLO-008 (maker/checker review workflow)**: `clauseos/domain/review.py`
  — `ReviewCase.record_maker_decision` / `record_checker_decision` raise
  `SameActorError` if the maker and checker identities match, and raise
  `StaleReviewError` (compare-and-swap) if the assertion's evidence hash no
  longer matches what the review case was opened against.
- **CLO-009 (materiality model, partial)**: only three material-term
  concepts are modeled — `THRESHOLD`, `MTA`, `NOTIFICATION_DEADLINE`
  (`clauseos/domain/status.py::MaterialTermConcept`) — not the full Section
  10.5 list (Independent Amount, eligible collateral, haircuts,
  concentration/wrong-way limits, ratings triggers, amendment precedence,
  segregation/reuse/substitution rights are all out of scope this session).
- **CLO-010 (release/export)**: `clauseos/domain/policy.py::build_policy_release`
  refuses to build a release unless every required concept has a
  `CHECKER_APPROVED` review case bound to the assertion's *current*
  evidence hash (`IncompletePolicyError` otherwise — fail closed, not a
  partial release). `clauseos/application/envelope_service.py` wraps the
  release as a signed `cassandre_contracts.Envelope`
  (`producer_product=ProducerProduct.CLAUSEOS`,
  `simulation_provenance.is_simulated=True`,
  `scenario_id="SIM-SCEN-CLAUSEOS-0001"`).
- **CLO-012 / CLO-013 (persistence + minimal API)**: SQLAlchemy declarative
  tables in `clauseos/adapters/db/models.py` (`agreement_bundle`,
  `document_node`, `evidence_span`, `assertion`, `review_case`,
  `policy_release`, `audit_event`, `outbox`), an Alembic migration
  (`products/clauseos/migrations/versions/0001_initial.py`), and a FastAPI
  app (`apps/clauseos_api/src/clauseos_api/app.py`) exposing the endpoints
  listed below via dependency injection over a `BundleRepository` Protocol
  (`clauseos/application/ports.py`) — an in-memory implementation
  (`clauseos/adapters/memory.py`) is used by every test in this session; the
  SQLAlchemy-backed implementation (`clauseos/adapters/db/repository.py`)
  implements the identical interface but is only exercised against Postgres,
  which was unavailable (see "What could not be verified" below).

Every domain mutation records a hash-chained audit event
(`audit_protocol.chain.append_event`) via `BundleRepository.append_audit_event`,
called from `clauseos.application.bundle_service.BundleService`.

## Later session: three deferred gaps closed

A later session closed three of the gaps recorded below as "not attempted
this session." Each is additive to the slice above; none of the 22
originally-passing tests were changed to make these pass (see "Test command
and result" for the current, larger count).

### Gap 1: PDF/OCR ingestion pipeline (plan Section 10.3)

`clauseos/core/pdf_ingestion.py` adds a second ingestion path alongside the
existing plain-text/Markdown one. `BundleService.ingest_document` now
sniffs the actual bytes (`%PDF-` magic header, not filename/extension) and
routes accordingly; plain-text behavior is completely unchanged.

- **Born-digital extraction**: `pypdf` validates structure (malformed/
  corrupted file, encryption, page count, zero-page files) before `pdfplumber`
  does layout-aware text extraction per page (the plan is explicit that
  "`pypdf` alone is not a sufficient layout-evidence engine" — `pdfplumber`
  reconstructs reading order from word/line bounding boxes rather than raw
  content-stream operator order). The joined per-page text is run through
  the same `normalize_text` as the plain-text path, so `EvidenceSpan`
  offsets behave identically regardless of source format. Page-to-offset
  mapping (`PageOffset`, page number + bounding box) is captured for
  provenance but is not added to `EvidenceSpan` itself, since that model's
  fields (plan Section 10.1) are character-offset-only.
- **Explicit limits**: size (25 MiB) and page count (200) limits, and
  encryption rejection, are hard checks, not best-effort.
- **OCR quarantine**: an image-only page (no extractable text layer) is
  never promoted to authoritative text. `shutil.which("tesseract")` is
  checked before any `pytesseract` call; on this development machine
  tesseract is genuinely not installed, so the pipeline returns the
  non-publishable `BundleStatus.OCR_UNCERTAIN` (reused, not a new enum
  member) with an explicit reason. If tesseract *were* present, any OCR
  text obtained still only ever lands in a separate `ocr_quarantine_text`
  field — it is never treated as authoritative `normalized_text` either
  way, per the plan's "optional OCR output is quarantined and visibly
  marked."
- A rejected/quarantined document raises `DocumentIngestionRejectedError`
  (carrying the specific `BundleStatus`) rather than silently admitting it
  or crashing; the API surfaces this as 422 with an `ingestion_status`
  field.
- Tests (`products/clauseos/tests/test_pdf_ingestion.py`) synthesize their
  own tiny PDFs byte-by-byte (`tests/helpers.py`'s `build_born_digital_pdf_
  bytes` / `build_image_only_pdf_bytes` / `build_corrupted_pdf_bytes` —
  no external fixture file, no PDF-authoring dependency beyond what's
  already added for production use) and cover: successful born-digital
  ingestion end-to-end through `BundleService.analyze_bundle` (the rule
  proposer finds the same three material terms in a PDF-sourced document as
  in the plain-text fixtures), rejection of a corrupted PDF as `FAILED`,
  and the real (not simulated) OCR-unavailable path for an image-only PDF
  on this machine, plus one test that monkeypatches tesseract-present to
  prove OCR output still never becomes authoritative even then.
- **Not built**: the rootless-container execution environment for PDF/OCR/
  model processing (Section 10.3's "no network, read-only root filesystem,
  ephemeral scratch, non-root UID, dropped capabilities..." worker) —
  ingestion runs in-process, same as the rest of this product's S1 slice.
  Digest-pinning of parser/model artifacts is likewise not implemented.

### Gap 2: local-model adapter (plan Section 10.3/10.4, ADR-006, `CLO-X01`)

`clauseos/core/model_adapter.py` adds a **third, strictly additive**
proposer calling a local Ollama server (`http://localhost:11434`,
`llama3.2:1b`) over HTTP (`httpx`). ADR-006's rule is unchanged and
enforced in code, not just by convention: `BundleService.analyze_bundle`
takes a new `include_model_proposer: bool = False` parameter (every
existing call site and test omits it, so the critical path is byte-for-byte
unaffected); when `True`, the model's candidates go through the identical
structural-verification and maker/checker review path as rule-based
candidates. There is no separate publication path for model output.

- **Schema-constrained prompting**: the model is asked (via Ollama's
  `format: "json"` option) for a fixed three-field JSON shape
  (`threshold`/`mta`/`notification_deadline`, each `null` or a typed
  object). Every field is strictly validated (type, numeric-string/enum/
  range checks, no unknown top-level keys) before being trusted at all.
- **Verbatim grounding, not trusted offsets**: a 1B-parameter local model
  cannot be trusted to emit correct character offsets directly, so instead
  it must quote its evidence; the adapter finds that quote in the actual
  document text via exact substring search to compute real offsets. A
  quote that isn't found verbatim is dropped — never guessed, never
  fuzzy-matched.
- **Abstention, not error objects**: on any timeout, unreachable server,
  oversized response (`> 64000` bytes, checked before JSON parsing),
  malformed JSON, schema-validation failure, or an ungrounded quote, the
  adapter returns fewer (possibly zero) candidates for that document/
  concept — the same "propose nothing" shape `core.rule_proposer` already
  uses when its regexes don't match, not a distinct `ABSTAIN` status object.
- **Prompt-injection hygiene**: document text is wrapped between
  `<<<DOCUMENT_START>>>`/`<<<DOCUMENT_END>>>` markers with an explicit
  system instruction that content between them is data, never a command,
  regardless of what it appears to ask.
- **Enforced limits**: request timeout (`OLLAMA_REQUEST_TIMEOUT_SECONDS`,
  default 10s), max response bytes, max prompt characters fed to the model.
- Tests (`products/clauseos/tests/test_model_adapter.py`): fast unit tests
  mock the Ollama HTTP call via a fake client injected through
  `propose_candidates_from_model(client=...)` and cover every abstention
  path plus the well-formed/grounded and partial-response cases. **One
  test calls the real, live local Ollama server with no mocking**
  (`test_live_ollama_end_to_end_at_least_once`), gated by
  `pytest.mark.skipif(not is_ollama_reachable(), ...)` which mirrors the
  `CASSANDRE_TEST_DATABASE_URL`-skip pattern already used for Postgres in
  this same `conftest.py`. On the machine this was built and verified on,
  Ollama was running with `llama3.2:1b` already pulled, so **this test
  actually ran (not skipped)** and passed; on a machine without Ollama
  running it skips cleanly rather than failing.
- Update to `docs/adr/ADR-006-clauseos-modes.md` and
  `docs/DECISION_REGISTER.md`'s `DEC-CLO-01` row: both now record that the
  model adapter exists, is optional/never-required, and describe its
  abstention behavior — the original S1 paragraph is left intact with an
  appended update rather than rewritten.

### Gap 3: ClauseOS web UI (plan Section 10.8)

`apps/clauseos_web/static/` is a plain HTML/CSS/vanilla-JS single-page app
(no build step, no `node_modules`) mounted onto the existing
`clauseos_api` FastAPI app at `/ui` via `fastapi.staticfiles.StaticFiles`
(`apps/clauseos_api/src/clauseos_api/app.py`), so `uvicorn
clauseos_api.app:app` serves both the API and the UI from one process, and
the JS calls the API via relative `fetch("/v1/...")` — no CORS/base-URL
configuration needed. Covers every page from the plan's list: bundle
inventory and completeness, document authority graph, side-by-side evidence
review, material-term maker queue, checker queue (showing the maker's
decision and any alternative candidate from a different proposer), the
unresolved/conflict queue, published releases and revocations (including
publish and revoke actions), and evidence export with audit-chain
verification. Per the plan's explicit UI rule, nothing in this app renders
a bare scalar confidence number as proof of anything — every status is a
discrete named badge (`EVIDENCE VERIFIED`/`EVIDENCE MISMATCH`,
`BUNDLE_INCOMPLETE`, `RULE-ONLY`/`MODEL-ONLY`/`PROPOSERS AGREE`/`PROPOSERS
DISAGREE`, `REQUIRED: MATERIAL TERM`), enforced by a static-source test
(`apps/clauseos_web/tests/test_static_assets.py`) as well as by design.

- **Agreement diff and operational-impact report**: an intentional
  placeholder. Plan Section 10.7 lists `GET /v1/bundles/{bundle_id}/diff?
  against=` as a minimum API; `clauseos_api` does not implement it and the
  domain layer has no bundle-diff/operational-impact model, so this page
  says so plainly rather than fabricating output.
- **New, small API additions this gap required** (`apps/clauseos_api/src/
  clauseos_api/app.py`, all additive — no existing endpoint's behavior
  changed): `GET /v1/bundles` (list), `GET /v1/bundles/{id}/documents/
  {name}` (full normalized text, for side-by-side evidence rendering),
  `GET /v1/bundles/{id}/audit-events`, `GET /v1/policy-releases` (list,
  optional `bundle_id` filter) and `POST /v1/policy-releases/{id}:revoke`
  (both backed by a new `BundleRepository.list_policy_releases` method,
  implemented in both the in-memory and SQLAlchemy adapters), and
  `content_base64` accepted alongside the existing `content` field on
  `POST .../documents` so the UI can upload a PDF file.
- **Audit-chain verification is structural, not cryptographic**: the
  Export & Audit page checks that each event's `previous_event_hash`
  matches the prior event's `event_hash` client-side (JavaScript) — this
  detects deletion/reordering but does not re-derive each event's hash
  from its canonical content the way `audit_protocol.chain` does
  server-side. The page's own hint text says so explicitly rather than
  overclaiming "verified."
- **Manually verified against the real, running API** (not just unit
  tests): started `uvicorn clauseos_api.app:app` on this machine and, via
  an actual browser session, clicked through the entire real workflow —
  create bundle → paste-ingest a document → approve manifest → analyze
  (rule proposer) → approve all three assertions as maker → approve all
  three as a distinct checker → publish a policy release → revoke it —
  with every page re-rendering real server responses at each step (see the
  Evidence Review, Maker/Checker Queue, and Export & Audit pages, whose
  screenshots/page-text were inspected during that session). No part of
  this flow was mocked or hand-edited into the DOM.
- Automated tests: `apps/clauseos_api/tests/test_web_ui.py` (TestClient
  hitting the mounted `/ui` route, confirming real files are served and
  the JS's fetch targets are the real API paths) and
  `apps/clauseos_web/tests/test_static_assets.py` (file-presence and
  internal-consistency checks with no server needed).

### Not attempted this session (explicitly out of scope)

- **Full 8-12 bundle agreement corpus (plan Section 9.2)**: this session
  built exactly one synthetic CSA bundle
  (`products/clauseos/tests/fixtures/csa_synthetic_001.md` +
  `confirmation_annex_a_synthetic.md`) plus the one required adversarial
  variant (same CSA text, annex deliberately not ingested, to prove
  `BUNDLE_INCOMPLETE`). The other ten corpus scenarios (zero threshold,
  asymmetric thresholds, ratings-contingent threshold, currency-specific
  MTA, MTA/rounding ordering variants, amendment overriding eligible
  collateral, side-letter notification change, conflicting clauses,
  wrong-way/concentration rules) are not built.
- **`AuthorityEdge`, `ConditionExpression`, `ExternalFactRequirement`,
  `ObligationDefinition`** (plan Section 10.1) are not implemented — this
  slice's three material terms don't require conditional/three-valued
  policy logic (Section 10.6) or an obligation registry.
- **OIDC/Keycloak wiring (ADR-009)**: not built. `actor_id` is passed
  explicitly in each API request body as an authenticated-identity
  stand-in. This is a real, intentional gap, not a silent substitution —
  the maker/checker business rule itself (`SameActorError`) is enforced in
  domain code regardless, and does not depend on authentication being
  wired up.
- **Full idempotency-key middleware (plan Section 8.4, first bullet)**: not
  built at the API layer for this slice. The `outbox` table exists with
  `write_outbox_event`/`mark_outbox_processed` methods on the DB repository,
  but domain flows do not yet enqueue every mutation as an outbox event —
  that wiring is deferred.
- **Section 16.2's full adversarial ClauseOS suite**: only the one required
  adversarial case (missing incorporated document -> `BUNDLE_INCOMPLETE`)
  is built. Conflicting-clause detection, ratings-contingent-with-missing-
  state handling, and the other Section 16.2 cases are not.

## Test command and result

```
uv run pytest products/clauseos apps/clauseos_api apps/clauseos_web -q
```

Result on this machine, after the three gaps above were closed: **59
passed, 2 skipped**. The 2 skips are the same as before this update —
`products/clauseos/tests/test_db_repository.py`'s two DB-backed round-trip
tests, skipped by `conftest.py::clauseos_db_engine` because
`CASSANDRE_TEST_DATABASE_URL` is unset (Docker Desktop's WSL2 backend is not
functional on this development machine, per `docs/INTERPRETATION.md`
`DEC-ENV-01`). Every other test — domain, application, API, PDF ingestion,
and model-adapter layers — has no unmet dependency on this machine and
always runs, **including the one live Ollama integration test**
(`test_live_ollama_end_to_end_at_least_once`), which actually executed
(not skipped) because Ollama was running with `llama3.2:1b` pulled at the
time this was verified. On a machine without Ollama reachable, that one
test — and only that one — would skip cleanly instead.

The original slice's 22 tests are unchanged and still pass; the other 37
are new: 10 in `test_pdf_ingestion.py`, 11 in `test_model_adapter.py`, 7 in
`apps/clauseos_api/tests/test_new_endpoints.py`, 4 in
`apps/clauseos_api/tests/test_web_ui.py`, and 5 in
`apps/clauseos_web/tests/test_static_assets.py`.

The DB adapter code was still exercised locally against an ephemeral
in-memory SQLite engine (not part of the committed test suite, since SQLite
is not the target database and this repo's Postgres-only ownership model
should not silently gain a SQLite fallback) to catch import/logic errors
that would otherwise go completely unverified. That check found and fixed a
real bug: datetime columns were not declared `DateTime(timezone=True)`,
which silently corrupted the audit hash chain on read-back
(`clauseos/adapters/db/models.py`, `clauseos/adapters/db/mapping.py`'s
`_ensure_utc` helper). This is disclosed here because it was never verified
against the actual target database (Postgres) in this session.

## Known scope simplifications (disclosed, not hidden)

- Nested value objects (a manifest's parties/relationships, a candidate's
  proposed value, a policy release's published terms) are stored as JSON
  columns on their parent row in the DB adapter rather than further
  normalized into additional child tables.
- The bundle state machine collapses `VALIDATED -> APPROVED -> PUBLISHED`
  into one `publish_policy_release` call; there is no separate API step or
  distinct actor identity for "validate" versus "approve" in this slice.
- `SEMANTIC_REVIEW` (the model-proposal step in the full `AssertionStatus`
  state machine) is never entered — every material-term assertion in this
  slice goes `STRUCTURALLY_VERIFIED -> HUMAN_REVIEW_REQUIRED` directly,
  consistent with ADR-006 (no model proposer built this session).
- The full 18-member `BundleStatus`/`AssertionStatus` enums from plan
  Section 10.2 are all defined (so a `match`/exhaustiveness check has
  something to check against, per ADR-014), but not every member is
  reachable through an implemented transition in this slice (e.g.
  `OCR_UNCERTAIN`, `EXTERNAL_STATE_REQUIRED` have no producing code path
  here — they exist for the exhaustive-enum discipline, not because this
  slice generates them).

## A cross-product test-collection issue found (not fixed, out of lane)

While adding this product's tests, `pytest` failed at the *whole-repository*
level (`uv run pytest` with no path arguments) with an `ImportPathMismatchError`
between `products/clauseos/tests/conftest.py` and sibling products'
`tests/__init__.py`-marked directories. This was because every product's
scaffolded `tests/` directory shares the literal package name `tests`, and
`pytest` (with `--import-mode=prepend`, the project default) cannot import
two different `tests` packages under the same module name in one run. This
product's own contribution to that collision was removed (see
`products/clauseos/tests/` — no `__init__.py`, so `helpers.py`/`conftest.py`
import as plain top-level modules rather than `tests.*`), and
`apps/clauseos_api/tests/__init__.py` (also this product's file) was removed
for the same reason. After that fix, `uv run pytest products/clauseos
apps/clauseos_api -q` (the command this session was asked to leave green)
passes cleanly. A residual collision between `products/riskos/tests` and
`apps/cassandre_control_api/tests` (owned by other in-progress agents, not
touched here) still reproduces on a full bare `uv run pytest` — that is
outside this product's directory scope and is left for whoever integrates
all three products together, most plausibly by giving each product's test
package a unique dotted name or by adding `--import-mode=importlib` at the
root `pyproject.toml`.

## Security integration (`cassandre_security`, later session)

A later session wired the shared `packages/cassandre_security` package
(the foundational P0 slice: SEC-01/02/04/05/08/10/14/15 — see that
package's own `README.md`/`COMPONENTS.md`/`THREAT_REGISTER.md`) into
ClauseOS's actual runtime code, not just as an unused import. Everything
below is additive: every pre-existing test and call site is unchanged
unless it deliberately opts into the new behavior.

- **SEC-04 (authorization)**: `clauseos.application.security.
  ClauseOsAuthorizationService` wraps `cassandre_security.authorization.
  DenyByDefaultPolicyEngine` with a `PolicyRule` allowlisting exactly the
  six sensitive (action, purpose) pairs ClauseOS's service layer performs
  — ingest document, analyze/propose assertion, maker review decision,
  checker review decision, publish policy release, export evidence
  bundle. `BundleService` calls `.authorize(...)` at the start of each of
  those methods; a DENY raises `clauseos.domain.errors.
  AuthorizationDeniedError`, which `apps/clauseos_api` maps to HTTP 403
  (before the 422/409/400 mappings, checked first) — the action never
  executes on a DENY. ClauseOS has no OIDC/Keycloak identity/session
  integration yet (the product's own already-disclosed ADR-009 gap), so
  the default `RevocationChecker`/`GrantResolver` are documented,
  intentionally permissive placeholders for that missing infrastructure
  (see `clauseos/application/security.py`'s module docstring) — the
  engine's deny-by-default action allowlisting, scope-completeness,
  policy-digest, and session-expiry checks are still fully enforced
  regardless. `/v1/evidence-bundles:export`'s request body gained an
  optional `actor_id` field (default `"SIM-USER-UNSPECIFIED-EXPORT"`) so
  the pre-existing test calling it without one still passes; a real
  deployment should make this mandatory once identity is wired up.
- **SEC-05 (maker/checker)**: ClauseOS's own domain layer
  (`clauseos.domain.review.ReviewCase.record_checker_decision`) already
  enforces distinct maker/checker identities via a plain `actor_id`
  string comparison (`SameActorError`) — that check is untouched.
  `BundleService.record_checker_decision` additionally calls
  `cassandre_security.maker_checker.enforce_distinct_natural_persons`
  as **defense-in-depth**, not a duplicate implementation: with the
  package's default identity-resolution directory it is functionally
  equivalent to the existing check today, but a real deployment can plug
  in an alias-aware `NaturalPersonDirectory` (`BundleService`'s
  `natural_person_directory` constructor parameter) so that two
  *different* `actor_id` strings resolving to the same human are also
  rejected — something the domain layer's plain string compare cannot
  catch. See `test_alias_aware_self_approval_rejected_even_with_distinct_actor_ids`
  and its companion `test_domain_layer_alone_would_have_allowed_the_alias_case`
  in `tests/test_security_integration.py`, which prove this is a real
  behavior difference, not a no-op.
- **SEC-08 (envelope encryption)**: `clauseos.application.
  document_sealing.DocumentSealingService` (owned by `BundleService`, also
  reachable directly via `service.seal_restricted_document_bytes`/
  `open_restricted_document_bytes`) seals/opens restricted document bytes
  with `cassandre_security.envelope.seal`/`open_`, bound to an
  `EnvelopeContext` (`product_id="clauseos"`, `object_id=document_id`,
  `data_class=RESTRICTED`), using a `LocalSoftwareKeyCustodyAdapter` —
  labeled, per that class's own docstring, as an **S1 reference
  implementation only** (KEK held in process memory, not HSM-backed).
  This is a new, opt-in capability; `DocumentNode`'s existing plain-text
  `raw_bytes_sha256`/`normalized_text` storage, used by every existing
  test, is completely untouched.
- **SEC-14 (revocation fencing)**: `clauseos.adapters.db.fence_gateway.
  SqlAlchemyRevocationFenceGateway` wires `cassandre_security.revocation`'s
  `ensure_fence_exists`/`lock_fences_for_mutation`/`advance_revocation`
  into ClauseOS's own Postgres database, using the scope-key convention
  `product=clauseos;tenant=<scenario_id>;object=<bundle_id|review_id>`.
  `BundleService.publish_policy_release` and `record_checker_decision`
  both accept an optional `presented_revocation_epoch` and, when a
  `fence_gateway` is configured (`None` by default, so every pre-existing
  test/call site is unaffected), lock/verify the fence before the
  mutation proceeds; a stale/unknown/revoked fence raises
  `RevocationFenceBlockedError` (mapped to HTTP 409). A new Alembic
  migration (`migrations/versions/0002_security_fence.py`) creates the
  `cassandre_security_fence` table in ClauseOS's own database, matching
  `cassandre_security.revocation.SecurityFenceRow`'s schema. **Disclosed
  limitation**: `cassandre_security.revocation`'s own docstring specifies
  holding the fence row lock through the caller's *own* atomic
  domain/audit/outbox commit; ClauseOS's existing `SqlAlchemyBundleRepository`
  does not expose a cross-call unit of work (each `save_*` method opens
  and commits its own transaction), so this gateway checks/advances the
  fence in its own short transaction immediately before the caller's
  separate domain-mutation transaction(s), rather than one shared
  transaction. This still correctly detects and blocks a stale/unknown/
  revoked fence state before the mutation proceeds, proven against real
  Postgres in `tests/test_security_integration.py`, but leaves a narrow
  theoretical race window between the two commits that a broader
  unit-of-work refactor of the repository layer would close.
- **SEC-10 (canonical evidence v2, lighter touch)**: `clauseos.application.
  evidence_v2.seal_policy_release_publication_evidence`/
  `verify_policy_release_publication_evidence` wrap
  `cassandre_security.canonical_evidence.seal_evidence`/`verify_evidence`
  for one new, additive evidence artifact type — policy-release
  publication evidence (`cassandre.evidence.v2`, SHA-384,
  multi-signature-ready). ClauseOS's existing audit-event hash chain
  (`audit_protocol.chain`) and its existing signed `Envelope` format
  (`clauseos.application.envelope_service`, built on
  `cassandre_contracts.build_envelope`/`sign_envelope`) are both
  completely untouched — this is a second, independent evidence artifact
  for the same publication event, not a migration of the first.
  `BundleService.publish_policy_release`'s return signature is unchanged
  (still `(release, envelope)`); callers wanting the v2 evidence container
  call the new module function separately.

**New tests**: `products/clauseos/tests/test_security_integration.py`
proves, against the real running code paths (not just the shared
package's own unit tests): an unauthorized action is genuinely denied and
blocks the mutation, both directly against `BundleService` and through the
FastAPI app (HTTP 403); the maker/checker defense-in-depth call rejects a
self-approval a plain domain-layer string check would miss; a sealed
document round-trips through `envelope.seal`/`open_` and a tampered
ciphertext (or wrong `document_id` context) fails closed; the revocation
fence genuinely blocks a mutation presenting a stale epoch and an
unprovisioned scope, both proven against real Postgres via the existing
`clauseos_db_engine` fixture; and the canonical-evidence-v2 container
round-trips and fails closed on tamper.

**Test counts** (`uv run pytest products/clauseos apps/clauseos_api
apps/clauseos_web -q`, run from `D:\Cassandre-Platform`):

- Without `CASSANDRE_TEST_DATABASE_URL`: **68 passed, 4 skipped** (4 skips:
  the 2 pre-existing DB-backed round-trip tests in `test_db_repository.py`,
  plus the 2 new revocation-fence tests in `test_security_integration.py`
  that also require real Postgres).
- With `CASSANDRE_TEST_DATABASE_URL="postgresql+psycopg2://clauseos_app:
  clauseos_dev_only_change_me@localhost:55432/clauseos"`: **72 passed, 0
  skipped** — all four of the above run for real against live Postgres,
  including the two-connection fence-blocking scenarios.

No regressions: every test that passed before this integration still
passes unchanged.

**Dependency change**: `products/clauseos/pyproject.toml` gained
`cassandre_security` as a dependency (workspace member; `uv sync` from the
repo root picked it up). No other product's files, the shared
`packages/cassandre_security/` package itself, or the root
`pyproject.toml`/`conftest.py` were touched.

**Bugs found in `cassandre_security` (not fixed here — that package is a
read-only shared dependency other agents are concurrently integrating into
other products)**: none found. Every module used (`authorization.py`,
`maker_checker.py`, `envelope.py`, `revocation.py`, `canonical_evidence.py`)
behaved exactly as documented in its own docstrings and the package's
`COMPONENTS.md`/`THREAT_REGISTER.md` during this integration.

**Deviations from the integration brief**:

- The envelope-encryption capability (SEC-08) is exposed at the service
  layer (`DocumentSealingService` + `BundleService.seal_restricted_document_bytes`/
  `open_restricted_document_bytes`), not as a new `BundleRepository`
  method or a new HTTP endpoint. The brief allowed either ("a new
  repository method or adapter" was given as an example, not a
  requirement); no DB schema change or API surface was added for sealed
  bytes this pass.
- The revocation-fence wiring does not hold one lock across the fence
  check and the caller's own domain/audit/outbox commit, for the reason
  disclosed above (no cross-call unit of work in the existing DB
  repository). The fencing still genuinely blocks stale/unknown/revoked
  mutations; it is a narrower guarantee than "one shared transaction."
- `/v1/evidence-bundles:export` gained an optional (not mandatory)
  `actor_id` field to stay backward-compatible with the one existing test
  that calls it without one — see the SEC-04 bullet above.

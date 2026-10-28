# Cassandre security component — build tracker

## Scope revision, 2026-09-15

User asked to complete the entire security component, excluding only segments that require their own authentication, human intervention, or validation. This **reverses the earlier "classical-only" decision** — post-quantum crypto is software, not something requiring the user's direct action, so it is now in scope.

**Newly in scope this pass** (SEC-09's software half, SEC-11/12/13, SEC-16/17, SEC-18, SEC-19, SEC-20, SEC-21):

| Module | Maps to | Status | Tests |
| --- | --- | --- | --- |
| `pqc.py` — ML-KEM-768 + ML-DSA-65 via `quantcrypt` (reviewed library, not hand-rolled), crypto capability inventory | SEC-11/13 | done | 15 passed (sizes match FIPS 203/204 tables, implicit-rejection proof, domain separation, tamper rejection) |
| `canonical_evidence.py` upgraded — ML-DSA-65 as an opt-in second required signature (`REQUIRED_SIGNATURE_ALGORITHMS_V2_HYBRID`), fully backward compatible with the classical-only default every product already integrated | SEC-13 | done | 5 new tests (16 total in the file); whole-repo suite reconfirmed unaffected |
| `key_ceremony.py` — ceremony record-keeping/state machine + threshold secret splitting via `shamir-mnemonic` (Trezor's real-world SLIP-39, not custom polynomial math — spec explicitly forbids hand-rolled splitting) | SEC-09 software half | done | 13 passed (2-of-3, any-two-of-three, below-threshold fail-closed, corrupt share rejected, distinct-participant enforcement) |
| `release_admission.py` — manifest verification, two-approver enforcement (reuses `maker_checker`, not a second implementation), activation re-verification (binary-swap, rollback-below-floor, stale-epoch rejection) | SEC-18 | done | 11 passed |
| `backup_restore.py` — coherent recovery set manifest + quarantine-verify-before-reopen; `snapshot_database`/`restore_snapshot_into_quarantine` shell out to real `pg_dump`/`psql` inside the actual Postgres container via `docker exec` | SEC-19 | logic done (11 passed); real-Postgres snapshot/restore round trip pending Docker |
| `incident_response.py` + `RUNBOOKS.md` — degraded-state machine (no state but NORMAL permits new publish/export — structurally enforced, not just documented), 10-phase incident sequence with no phase-skipping, 5 separate runbooks (ransomware, inventory/approval breach, audit fork, online-key compromise, root compromise) | SEC-20 | done | 12 passed |
| `acceptance.py` — runs the real test suite via `pytest --junitxml`, maps results to SEC control IDs via a curated table, emits the spec's own `cassandre.security-evidence.v1` schema; `check_hard_blocker_coverage` checks spec section 15's hard-blocker list against actually-passing tests | SEC-21 | logic done (7 passed); full real run pending Docker (needs the DB-gated tests included) |
| TLS hybrid transport | SEC-12 | honest partial finding, not fully built | see below |

### SEC-12 finding (TLS hybrid transport)

Investigated directly rather than left unexamined. Confirmed:
- OpenSSL 3.5.6/3.5.8 is genuinely installed on this machine (`openssl version`), and OpenSSL 3.5+ ships native ML-KEM support in its default provider (`openssl list -providers` shows the default provider active).
- `openssl s_server -groups X25519MLKEM768` accepts the group name without an "unknown group" error — the binary recognizes the hybrid group.
- Python's stdlib `ssl` module has **no API** to select or configure TLS named groups (`ssl.SSLContext` only exposes `set_ecdh_curve` for classical curves) — confirmed by inspection, not assumed.

**Conclusion**: the cryptographic capability exists at the OpenSSL/system layer, but there is no path to actually enforce the hybrid group on this repo's own FastAPI/uvicorn services through Python alone. Real enforcement needs a TLS-terminating reverse proxy (nginx/Caddy/similar) built against OpenSSL 3.5+ sitting in front of uvicorn — new infrastructure, not more application code. This is honestly reported as not built, rather than faked with an inconclusive local demo.

### Final verification, 2026-09-15

- Docker crashed again mid-session with the identical stale-socket bug as before (see message history) — fixed the same way (elevated stop + clear `run/` directory), confirmed Postgres/Keycloak auto-restarted via their `restart: unless-stopped` policy.
- `backup_restore.py`'s real `pg_dump`/`psql`-via-`docker exec` path found and fixed one real bug during testing: `docker cp -` (stdin) treats its destination as a directory (it reads a tar stream), not a file path — always failed with "destination must be a directory". Fixed to copy the host dump file directly. Verified end-to-end against **two independent products' real databases** (ClauseOS, RiskOS): snapshot → restore into a fresh quarantine database → row counts match exactly, proven via a parametrized test (`test_backup_restore_integration.py`), not a one-off manual check.
- That same integration test initially had a hidden ordering bug: it depended on ambient data left by an earlier manual seeding script, which a *later, unrelated* run of RiskOS's own test suite legitimately wiped via its own `drop_all` teardown — a real "worked once, broke on re-run" bug, found by deliberately re-running the exact sequence. Fixed by making the test self-seed its own data via each product's own repository layer before snapshotting, independent of any other test's state. Re-verified order-independent (ran the breaking sequence again, passed).
- **Real acceptance evidence report generated** (SEC-21, not simulated): `acceptance_report_2026-09-15.json`, produced by actually running 183 tests across `packages/cassandre_security` + all three products' security-integration test files against real Postgres. **183 passed, 0 failed, 0 falsely marked NOT_RUN.** `check_hard_blocker_coverage` against spec section 15's hard-blocker list: 9 of 13 genuinely covered by a passing test; the other 4 (production-data-access-by-agents, required-hybrid-downgrade, raw-content-in-logs, settlement-transmission-route) honestly reported as NOT covered rather than hidden or fabricated.
- Package suite final count: **147 passed, 0 skipped** with real Postgres; **145 passed, 2 skipped** without. Whole-repo: **489 passed, 26 skipped, 0 failed**, both with and without the DB env var (tested both ways after discovering that pointing the shared `CASSANDRE_TEST_DATABASE_URL` at one generic database for ALL products at once — an invocation mistake on my part, not a product bug — causes a spurious CollateralOS failure; each product needs its own scoped URL, confirmed by re-running that one test against its correct database).

**Still explicitly excluded** (genuinely need the user's own action, real hardware, or a separate human/organizational decision — not something buildable as code in this session):
- The actual physical key ceremony with real named human custodians in a room — `key_ceremony.py` builds the software mechanism and proves it with synthetic custodian identities; a real deployment still needs real people to run it.
- SEC-22: independent human/S2 security review and sign-off.
- SEC-23/24: confidential computing and FHE/MPC — these need real attested hardware or a cloud attestation service and a separate research/procurement decision, not more Python.
- Real production keys, real customer data, real penetration testing.

Original P0 slice below is unchanged and already verified; new sections follow it.

---


Source spec: `Cassandre-Comprehensive-Security-Architecture.md` (design-only document; not implemented, penetration-tested, independently audited, or approved for customer data — see that document's own status banner).

Scope decision recorded 2026-09-13: **classical cryptography only this pass** (AES-256-GCM + Ed25519; no ML-KEM/ML-DSA — those libraries are not installed and are a separate, explicitly gated future migration per section 8.1/8.4 of the spec), and **the foundational P0 slice** of the backlog: SEC-01, SEC-02, SEC-04, SEC-05, SEC-08, SEC-10, SEC-14, SEC-15. SEC-09 (human key ceremonies), SEC-11–13/23–24 (PQC), SEC-16/17 (deferred to per-product wiring below where relevant), SEC-18 (release admission), SEC-19/20 (backup/incident runbooks) are **not** in this pass — they need real hardware, named human custodians, or infrastructure this session does not have.

This file is updated as each piece lands. "Status" reflects what's actually built and tested in this repository right now, not the aspirational design.

## Package: `packages/cassandre_security/`

| # | Component | Module | Maps to | Status | Tests |
| - | --- | --- | --- | --- | --- |
| 1 | Threat register / scope inventory | `THREAT_REGISTER.md` | SEC-01 | done | n/a (doc) |
| 2 | Data classification + synthetic-only import gate | `classification.py` | SEC-02 | done | 8 passed |
| 3 | Deny-by-default authorization engine | `authorization.py` | SEC-04 | done | 9 passed |
| 4 | Maker-checker / distinct-actor enforcement | `maker_checker.py` | SEC-05 | done | 7 passed |
| 5 | Envelope encryption (AES-256-GCM, DEK/KEK) | `envelope.py` | SEC-08 | done | 11 passed (tamper/context/nonce/retry) |
| 6 | Key lifecycle state machine | `key_lifecycle.py` | SEC-08/09 (state machine only, no human ceremony) | done | 8 passed |
| 7 | Canonical evidence v2 (SHA-384, multi-signature-ready container) | `canonical_evidence.py` | SEC-10 | done | 10 passed (mutation/strip/key-substitution/replay) |
| 8 | Revocation/security-fence epoch mechanism | `revocation.py` | SEC-14 | done | 9 passed, incl. a genuine 2-connection race against real Postgres proving both commit orderings (JPMC-FINAL-01) |
| 9 | Independent witness/checkpoint store | `witness.py` | SEC-15 (witness half) | done | 9 passed (fork/gap/replay/freshness) |
| 10 | Authoritative-table privilege lockdown (DB migration helper) | `db_lockdown.py` | SEC-15 (DB-enforcement half) | done | 3 passed, incl. a real Postgres role/grant proof via `information_schema.role_table_grants` |

**Package-level test count: 74 passed** (`uv run pytest packages/cassandre_security -q` with `CASSANDRE_TEST_DATABASE_URL`/`CASSANDRE_TEST_ADMIN_DATABASE_URL` set against the live docker-compose Postgres; 72 passed / 2 cleanly skipped without those env vars).

## Product integration ("plug it in")

| Product | What gets wired | Status |
| --- | --- | --- |
| ClauseOS | authorization engine (403 on deny) on ingest/analyze/maker/checker/publish/export actions; maker-checker defense-in-depth; envelope encryption for restricted document bytes; revocation fence on publish/checker-decision (real Postgres, verified); evidence v2 for policy-release publication | done — 72 passed (0 skipped) with real Postgres, 68 passed/4 skipped without; independently re-verified |
| RiskOS | authorization engine (403 on deny) on 6 gated actions incl. CHALLENGER, kept fully independent of MARGIN_CALL (re-verified); revocation fence on run_calculation/export_result_package (real Postgres, verified, 409 on stale epoch); evidence v2 for ChallengerRun; witness checkpointing on the shared audit chain | done — 82 passed (0 skipped) with real Postgres, 74 passed/8 skipped without; independently re-verified incl. challenger-independence test |
| CollateralOS | authorization engine (403 on deny) on all 8 store actions, in both in-memory and SQL stores; maker-checker defense-in-depth; revocation fence on reserve/export (SQL store only, documented rationale) with a genuine two-connection race against the reservation lock; evidence v2 on export | done — 65 passed (0 skipped) with real Postgres x2 runs, 54 passed/11 skipped without; independently re-verified |

## Post-integration fixes to the shared package

Both product-integration agents surfaced real gaps in `cassandre_security` itself, independently confirmed and fixed after all three product integrations landed (so the fix wouldn't race with in-flight work on the shared API):

1. **`canonical_evidence.verify_evidence` didn't cross-check payload bytes.** Signature verification proved the signed body was untampered, but nothing recomputed the body's `content_digest_hex` against a caller's actual payload — a container signed over one payload would pass "verification" even when the caller held a different payload, unless they separately re-hashed and compared it themselves. Fixed: `verify_evidence` now accepts an optional `payload` parameter that, when supplied, recomputes and asserts the digest matches; two new tests cover the substituted-payload rejection and the payload-omitted (signature-only) mode. Found by the CollateralOS integration agent.
2. **`authorization.AuthorizationRequest.content_digest` was accepted but never checked against anything.** This is by design (the engine has no object-store dependency — spec section 7's decision engine is a pure typed function), but it wasn't documented, so a reader could reasonably assume the engine validates it. Fixed: documented in the dataclass field's own comment that this binding is the caller's responsibility (e.g. `envelope.py`'s AAD already does this for encrypted objects). Found by the CollateralOS integration agent.
3. **Noted, not changed:** RiskOS's integration agent found that `witness.submit_checkpoint`'s gapless-sequence requirement is a real hazard for a product whose audit chain assigns one global sequence across every action type — checkpointing only a subset of events (e.g. only `CalculationRun` saves) hits spurious `WitnessGapError`s. RiskOS worked around this by checkpointing every event on the shared chain. This is arguably correct behavior for the module (it's supposed to reject gaps) but the module's docstring should have warned that a caller checkpointing a *subset* of a shared sequence will need its own dedicated counter. Left as-is; a future pass should either document this more prominently in `witness.py` or add a convenience wrapper for "checkpoint every Nth event of a larger chain."

Full package suite after these fixes: **76 passed** (packages/cassandre_security alone, real Postgres). Whole-repo suite: **419 passed, 25 skipped, 0 failed** — no regressions in any of the three products.

## Explicitly out of scope this pass (recorded, not silently dropped)

- ML-KEM/ML-DSA post-quantum crypto (SEC-11/12/13) — needs `liboqs-python` or `quantcrypt` installed and evaluated; user decision: classical-only for now.
- Real HSM/customer key ceremony (SEC-09) — needs named human custodians; this pass ships a clearly-labeled software `KeyCustodyAdapter` reference implementation only.
- Release manifest / customer admission verifier (SEC-18).
- Backup/restore coherent-recovery-set tooling (SEC-19) and incident runbooks (SEC-20).
- TLS hybrid transport profile (SEC-11/12) and confidential computing (SEC-23).
- Independent human/S2 review, penetration testing, cryptographic module certification — the spec is explicit that no AI review substitutes for these.

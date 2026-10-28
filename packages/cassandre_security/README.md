# cassandre_security

Shared security-enforcement package implementing the **foundational P0 slice** of `Cassandre-Comprehensive-Security-Architecture.md`: SEC-01, SEC-02, SEC-04, SEC-05, SEC-08, SEC-10, SEC-14, SEC-15. Classical cryptography only (AES-256-GCM, Ed25519) — no ML-KEM/ML-DSA this pass; see `COMPONENTS.md` for the full scope decision and what is explicitly deferred.

**This is unreviewed AI-authored code implementing an AI-authored design specification.** No penetration testing, cryptographic engineering review, or independent human security review has occurred. Do not use with real (non-synthetic) data. See `THREAT_REGISTER.md` for the residual-risk register this pass does not close.

## Modules

- `classification.py` — `DataClassification` enum, synthetic-provenance ingestion gate, fail-closed declassification.
- `authorization.py` — deny-by-default `AuthorizationRequest`/`AuthorizationDecision`/`DenyByDefaultPolicyEngine` matching the spec's `cassandre.authorization.v1` schema.
- `maker_checker.py` — distinct-natural-person enforcement and independent dual-approval for critical actions.
- `envelope.py` — AES-256-GCM envelope encryption with AAD context binding; `LocalSoftwareKeyCustodyAdapter` is an S1-only reference implementation, explicitly not HSM-backed.
- `key_lifecycle.py` — key state machine (pending/active/decrypt-only/verify-only/suspended/compromised/destroyed).
- `canonical_evidence.py` — a new SHA-384, multi-signature-ready evidence container (`cassandre.evidence.v2`), distinct from and not replacing `audit_protocol`'s existing SHA-256 V1 format.
- `revocation.py` — the security-fence/epoch mechanism resolving the spec's Appendix A blocker JPMC-FINAL-01; proven against real Postgres with a genuine two-connection race.
- `witness.py` — independent checkpoint store rejecting forked/gapped chain extensions.
- `db_lockdown.py` — generates and verifies (against real `information_schema.role_table_grants`) the REVOKE/GRANT SQL that removes direct DML from application roles.

## Testing

```bash
uv run pytest packages/cassandre_security -q
```

72 tests run with no external dependency. Two additional tests require real Postgres and are skipped otherwise:

```bash
CASSANDRE_TEST_DATABASE_URL="postgresql+psycopg2://<role>:<pass>@localhost:55432/<db>" \
CASSANDRE_TEST_ADMIN_DATABASE_URL="postgresql+psycopg2://postgres:<superuser-pass>@localhost:55432/postgres" \
uv run pytest packages/cassandre_security -q
```

## Integrating into a product

See `COMPONENTS.md` for the per-product wiring plan. In short: call `authorization.DenyByDefaultPolicyEngine.decide(...)` before any sensitive action, wrap sensitive stored bytes with `envelope.seal`/`open_`, call `revocation.lock_fences_for_mutation` inside the same transaction as your domain/audit/outbox write for any critical mutation, and use `maker_checker.enforce_distinct_natural_persons` wherever your product already has its own maker/checker flow.

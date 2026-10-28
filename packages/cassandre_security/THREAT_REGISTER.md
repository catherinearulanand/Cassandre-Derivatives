# Cassandre threat register and scope inventory (SEC-01)

Source: `Cassandre-Comprehensive-Security-Architecture.md` sections 2-4. This is the signed-scope artifact the spec's Section 2.1 decision table calls for ("Product owner confirms boundaries before repository changes") — recorded here as the technical inventory; the actual sign-off is a human decision this document cannot manufacture.

## Product mapping (spec section 2, resolved for this repository)

Cassandre is the security architecture protecting three standalone products in this repository: **ClauseOS**, **RiskOS**, **CollateralOS**, plus the optional **Cassandre control** federation layer (`apps/cassandre_control_api`, formerly referred to as AETHER in the source implementation plan). This mapping is what this pass's code implements against; it is not a substitute for the product owner's own confirmation the spec calls for.

## Assets (spec 4.1), as they exist in this repository today

| Asset class | Concrete instances in this repo |
| --- | --- |
| Restricted originals / extracts | ClauseOS `AgreementBundle`/`DocumentNode` (PDF/text ingestion), evidence spans |
| Approved terms and amendments | ClauseOS `ContractAssertion`, published policy releases |
| MTMs and collateral balances | RiskOS `PortfolioSnapshot`/`TradeMTM`, `CollateralStatePackage` |
| Inventory and SSIs | CollateralOS `InventorySnapshot`/`InventoryBalanceRow` |
| Identity and role grants | Product-local maker/checker actor IDs; Keycloak realm `cassandre-sim` |
| Keys and recovery material | `cassandre_security.envelope`/`key_lifecycle` KEK/DEK records (S1 software adapter only) |
| Approvals | ClauseOS `ReviewCase`, CollateralOS `PlanApproval`, RiskOS reconciliation cases |
| Audit records | `audit_protocol.AuditEvent` hash chains (per-product), `cassandre_security.witness` checkpoints |
| Exports | RiskOS/CollateralOS/ClauseOS signed `Envelope` objects (`cassandre_contracts`) |

## Adversaries (spec 4.2) in scope for this pass

Malicious documents (ClauseOS ingestion); compromised parser/model artifacts (ClauseOS's PDF/OCR/Ollama adapter — already sandboxed by graceful-degradation, not by OS-level isolation, in this session's build); privileged database administrator (addressed partially by `db_lockdown.py`, residual documented below); authenticated users exceeding scope (addressed by `authorization.py`); stale/replayed authorization state (addressed by `revocation.py`). Out of scope this pass: unauthenticated internet attackers (no network-facing deployment hardening done here), ransomware operators, quantum-capable impersonation (no PQC this pass).

## High-priority attack chains this pass actually cuts

| Attack chain (spec 4.3) | This pass's mitigation | What remains open |
| --- | --- | --- |
| DB administrator → rewritten approvals/audit | `db_lockdown.py` revokes direct DML from app roles; `audit_protocol` hash chain + `witness.py` external checkpoint | DB superuser/owner bypass is a declared residual, not solved — spec is explicit this is unavoidable without infrastructure this session doesn't have |
| Restore old state → resurrect role/key → approved export | `revocation.py`'s epoch fencing rejects a mutation presenting a stale epoch | No coherent-recovery-set tooling yet (SEC-19, out of scope) — restore itself isn't built |
| Crypto downgrade → classical-only traffic | N/A this pass (classical-only is the chosen baseline, not a downgrade) | PQC migration (SEC-11/12/13) is future work |
| Stolen user session → object enumeration → export | `authorization.py`'s deny-by-default engine + short decision expiry | No real session/device binding (needs actual Keycloak wiring, out of scope this pass) |

## Residual risks explicitly NOT addressed by this pass (recorded, not hidden)

- Database superuser/owner can still bypass `db_lockdown.py`'s grants (spec section 12: this is declared, not solved).
- `LocalSoftwareKeyCustodyAdapter` holds the KEK in process memory — not HSM-backed (spec section 16's "Synthetic workstation" profile only).
- No real identity-provider integration (Keycloak realm exists per this session's Docker work, but `authorization.py`'s `RevocationChecker`/`GrantResolver` protocols are not yet wired to it — that is per-product integration work).
- No PQC, no TLS hybrid transport, no attested compute.
- No human key ceremony, no backup/restore tooling, no incident runbooks.
- This is unreviewed AI-authored code implementing an AI-authored design specification. Per the source document's own repeated statement: independent human security review, cryptographic engineering review, and penetration testing have not occurred and are required before any of this touches real data.

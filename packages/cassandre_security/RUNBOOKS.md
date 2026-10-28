# Incident runbooks (SEC-20)

Five separate procedures, per spec section 12: "Ransomware, inventory/approval breach, audit fork, online-key compromise, and root compromise are separate procedures." Each follows the common phase sequence enforced in code by `incident_response.py`'s `IncidentPhase` state machine (`DECLARED -> CONTAINED -> CAPABILITIES_SUSPENDED -> EVIDENCE_PRESERVED -> ASSESSED -> REBUILT -> RECONCILED -> RESTORED_IN_QUARANTINE -> INDEPENDENTLY_APPROVED_TO_REOPEN -> CLOSED`), with type-specific detail below at each phase.

Every runbook assumes the roles in `incident_response.IncidentRoles` are already named **before** an incident (spec: "No S2 with unowned alerts, recovery, or key lifecycle"). These are template procedures for a real deployment's real staff to follow — this document does not, and cannot, name real people.

## 1. Ransomware

- **Contain**: isolate affected hosts at the network layer; do not power off encrypted-disk hosts if forensic imaging is planned. Revoke all sessions/tokens for accounts with access to the affected host (`revocation.advance_revocation` with `REVOCATION_EFFECTIVE` for every scope touching that host).
- **Suspend**: move every key whose host may have been compromised to `SUSPENDED` (`key_lifecycle.transition`), not `COMPROMISED` yet — compromise is confirmed at the Assess phase, not assumed at Contain.
- **Preserve evidence**: snapshot affected storage before any remediation tooling runs; do not let anti-ransomware tooling auto-quarantine files that are themselves evidence.
- **Assess**: determine which KEK/DEK material was ever present in plaintext on the affected host's memory or swap. Any key that was: mark `COMPROMISED`.
- **Rebuild**: redeploy from the last admitted release (`release_admission`), never from the compromised host's own disk image.
- **Reconcile**: replay the affected product's audit chain against the independent witness (`witness.check_freshness`) to detect any gap.
- **Restore in quarantine**: see `backup_restore.py`'s coherent-recovery-set restore — never restore into a host that has not been rebuilt clean.
- **Reopen**: requires the incident's `independent_resumption_checker`, never the incident commander alone.

## 2. Inventory or approval breach

(A CollateralOS reservation forged, or a ClauseOS/RiskOS approval bypassed.)

- **Contain**: revoke the specific actor/role scope implicated (`revocation.advance_revocation` at the narrowest scope that covers the breach — not a blanket product-wide lockdown unless the actor's blast radius is unknown).
- **Suspend**: set `DegradedState.INCIDENT_LOCKDOWN` for the affected product, blocking new reservations/approvals/exports.
- **Preserve evidence**: export the affected audit-chain segment and witness checkpoints before any remediation write touches the same rows.
- **Assess**: replay `revocation.lock_fences_for_mutation`'s fence-row history for the affected scope to determine the exact epoch at which the forged/bypassed action was accepted.
- **Rebuild**: not applicable unless the breach involved code, not just a stolen credential.
- **Reconcile**: CollateralOS — re-run `attempt_reserve`'s pure domain function against the ledger to confirm no double-reservation exists. RiskOS/ClauseOS — re-verify affected approvals' binding hashes.
- **Restore in quarantine / Reopen**: as above.

## 3. Audit fork

(Two different `head_hash` values observed for the same `(product_chain_id, sequence)`.)

- **Contain**: `witness.submit_checkpoint` already refuses to accept the second, conflicting extension (`WitnessForkError`) — the fork is detected, not silently taken. Freeze all further writes to that product's audit chain immediately.
- **Suspend**: `DegradedState.TRUST_REVOKED` for the affected product — no new publication/export is trustworthy until the fork's cause is understood.
- **Preserve evidence**: export both conflicting chain segments and every witness checkpoint around the fork point.
- **Assess**: determine which segment (if either) matches the independently witnessed sequence — the witness's own accepted checkpoint is the tiebreaker, not either in-database chain.
- **Rebuild**: rebuild the product's audit-chain table from the witnessed-correct segment; the other segment becomes incident evidence, not live data.
- **Reconcile**: every domain object whose audit trail touches the discarded segment needs individual review — a fork can hide an unwitnessed mutation.
- **Restore in quarantine / Reopen**: as above, with extra scrutiny — this is the spec's own named residual risk ("the witness cannot prove events never emitted").

## 4. Online-key compromise

(A KEK, signing key, or witness key suspected exposed — not the offline root.)

- **Contain**: revoke every session that could have requested unwrap operations from the compromised key's custody boundary.
- **Suspend**: `key_lifecycle.transition(key, to=COMPROMISED)` immediately — no `DECRYPT_ONLY`/`VERIFY_ONLY` grace period for a key under active suspicion.
- **Preserve evidence**: capture custody-boundary access logs for the key's entire active lifetime, not just the suspected compromise window.
- **Assess**: every object encrypted under a DEK this key ever wrapped is now suspect; every signature this key ever produced needs re-verification against the independently witnessed record it was supposed to protect.
- **Rebuild**: generate a replacement key under the normal (non-ceremony, since this isn't a root) key-generation path; a real deployment does this under its own key-management runbook, not this document.
- **Reconcile**: re-encrypt remaining controlled copies under the new key (spec: "DEK compromise requires re-encryption of remaining controlled copies... with no promise to recover secrecy of stolen plaintext/ciphertext-plus-key").
- **Restore in quarantine / Reopen**: as above.

## 5. Root compromise

(The offline trust anchor itself — the most severe case, spec section 9's explicit "distinct from ordinary online-key rotation.")

- **Contain**: this requires the full `key_ceremony` replacement-pin procedure, not the online-key runbook above — "the compromised root cannot be the sole authority authorizing its successor" (spec section 9). Every standalone consumer needs a separately authenticated, independently verified replacement-pin ceremony.
- **Suspend**: `DegradedState.TRUST_REVOKED` platform-wide, not just one product — a compromised root affects every product's trust chain.
- **Preserve evidence**: retain the compromised root's historical public evidence; do not destroy it — it's needed to mark the uncertain-interval signatures per spec.
- **Assess**: every signature verifiable only under the compromised root, across every product, is now in the "uncertain interval" the spec names — this needs a full inventory, not a sample.
- **Rebuild**: run `key_ceremony.start_ceremony`/`complete_ceremony` for the new root with a fresh quorum of custodians; the new root's `old_public_fingerprint` field must reference the compromised root for the historical record, per spec.
- **Reconcile**: every product re-pins to the new root and re-validates its own trust bundle sequence.
- **Restore in quarantine / Reopen**: this is the one case the spec explicitly says needs the most caution — "Never restore into a suspected operating system merely to meet an RTO." A root-compromise reopen is not a same-day operation.

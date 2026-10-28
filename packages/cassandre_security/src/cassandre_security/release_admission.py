"""Release manifests and customer admission (SEC-18).

Spec section 13: "A release contains source/build identity, test
manifest, SBOM, configuration schema, crypto capability matrix, migration
plan, signatures, and known limitations. Customer admission binds the
exact artifact, configuration, permission and egress diff, model/parser/
solver set, schema migration, cryptographic policy, minimum-version
epoch, test evidence, and rollback plan. Two independently entitled
customer deployment approvers authorize that digest; vendor/build
signatures prove provenance only. Activation re-verifies the admitted
digest locally and fails closed on mismatch, missing evidence, revoked
signer, stale epoch, or rollback."

This module implements the manifest schema, digest verification, and the
two-approver admission gate (reusing `maker_checker.require_independent_dual_approval`,
not a second implementation of that rule). It does not implement an actual
CI/build pipeline, SBOM generator, or vulnerability scanner -- those are
separate tooling this pass does not build; `ReleaseManifest` is the
contract such tooling would populate.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from audit_protocol import content_hash

from cassandre_security.maker_checker import CriticalActionApproval, require_independent_dual_approval


class ReleaseAdmissionError(ValueError):
    pass


class DigestMismatchError(ReleaseAdmissionError):
    pass


class StaleMinimumVersionError(ReleaseAdmissionError):
    pass


class RollbackBelowFloorError(ReleaseAdmissionError):
    pass


@dataclass(frozen=True)
class ReleaseManifest:
    release_id: str
    source_revision: str
    artifact_digest: str  # what actually gets deployed -- the value everything else binds to
    sbom_digest: str | None
    configuration_schema_digest: str
    crypto_capability_digest: str
    migration_plan_digest: str
    test_evidence_digest: str
    known_limitations: tuple[str, ...]
    minimum_version_epoch: int  # monotonic floor; a release cannot self-lower this
    built_at: dt.datetime
    build_signer_key_id: str

    def canonical_fields(self) -> dict:
        return {
            "release_id": self.release_id,
            "source_revision": self.source_revision,
            "artifact_digest": self.artifact_digest,
            "sbom_digest": self.sbom_digest,
            "configuration_schema_digest": self.configuration_schema_digest,
            "crypto_capability_digest": self.crypto_capability_digest,
            "migration_plan_digest": self.migration_plan_digest,
            "test_evidence_digest": self.test_evidence_digest,
            "known_limitations": list(self.known_limitations),
            "minimum_version_epoch": self.minimum_version_epoch,
            "built_at": self.built_at.isoformat(),
            "build_signer_key_id": self.build_signer_key_id,
        }

    def manifest_digest(self) -> str:
        return content_hash(self.canonical_fields())


@dataclass(frozen=True)
class AdmissionDecision:
    manifest_digest: str
    approvers: tuple[str, ...]
    admitted_at: dt.datetime


def admit_release(
    manifest: ReleaseManifest,
    *,
    requester_actor_subject: str,
    approvals: tuple[CriticalActionApproval, ...],
    now: dt.datetime,
) -> AdmissionDecision:
    """Spec: 'Two independently entitled customer deployment approvers
    authorize that digest.' Reuses the exact same independent-dual-approval
    rule as every other critical action in this package -- not a separate
    admission-specific implementation of maker/checker."""
    require_independent_dual_approval(requester_actor_subject=requester_actor_subject, approvals=approvals)
    return AdmissionDecision(
        manifest_digest=manifest.manifest_digest(),
        approvers=tuple(a.approver_actor_subject for a in approvals),
        admitted_at=now,
    )


@dataclass(frozen=True)
class RunningDeploymentState:
    """What's actually running right now, to check the admitted manifest
    against at every start (spec: 'A separately administered customer
    supervisor verifies these values at every start... drift terminates or
    quarantines the workload.')."""

    artifact_digest: str
    configuration_schema_digest: str
    minimum_version_epoch: int


def verify_activation(
    manifest: ReleaseManifest,
    *,
    admission: AdmissionDecision,
    running: RunningDeploymentState,
    current_minimum_version_epoch: int,
) -> None:
    """Fails closed on any deviation -- raises, never returns a "maybe ok"
    value. Called at every start, not just at deploy time (spec's
    activation re-verification requirement)."""

    if admission.manifest_digest != manifest.manifest_digest():
        raise DigestMismatchError("admission was granted for a different manifest digest than the one presented")

    if running.artifact_digest != manifest.artifact_digest:
        raise DigestMismatchError(
            f"running artifact_digest {running.artifact_digest!r} does not match admitted manifest {manifest.artifact_digest!r}"
        )
    if running.configuration_schema_digest != manifest.configuration_schema_digest:
        raise DigestMismatchError("running configuration_schema_digest does not match the admitted manifest")

    if manifest.minimum_version_epoch < current_minimum_version_epoch:
        raise RollbackBelowFloorError(
            f"manifest's minimum_version_epoch {manifest.minimum_version_epoch} is below the current "
            f"security floor {current_minimum_version_epoch} -- rollback below the minimum-version policy is refused"
        )
    if running.minimum_version_epoch < current_minimum_version_epoch:
        raise StaleMinimumVersionError(
            f"running deployment's minimum_version_epoch {running.minimum_version_epoch} is stale "
            f"(current floor is {current_minimum_version_epoch})"
        )

from __future__ import annotations

import datetime as dt

import pytest

from cassandre_security.maker_checker import CriticalActionApproval, IndependentApprovalError
from cassandre_security.release_admission import (
    DigestMismatchError,
    ReleaseManifest,
    RollbackBelowFloorError,
    RunningDeploymentState,
    StaleMinimumVersionError,
    admit_release,
    verify_activation,
)

NOW = dt.datetime(2026, 1, 5, 12, 0, 0, tzinfo=dt.timezone.utc)


def _manifest(**overrides) -> ReleaseManifest:
    defaults = dict(
        release_id="SIM-RELEASE-0001",
        source_revision="abc123",
        artifact_digest="sha256:artifact-v1",
        sbom_digest="sha256:sbom-v1",
        configuration_schema_digest="sha256:config-v1",
        crypto_capability_digest="sha256:crypto-v1",
        migration_plan_digest="sha256:migration-v1",
        test_evidence_digest="sha256:tests-v1",
        known_limitations=("no PQC TLS transport yet",),
        minimum_version_epoch=3,
        built_at=NOW,
        build_signer_key_id="SIM-BUILD-SIGNER-1",
    )
    defaults.update(overrides)
    return ReleaseManifest(**defaults)


def _approvals() -> tuple[CriticalActionApproval, ...]:
    return (
        CriticalActionApproval(approver_actor_subject="SIM-APPROVER-A", reason="reviewed SBOM"),
        CriticalActionApproval(approver_actor_subject="SIM-APPROVER-B", reason="reviewed test evidence"),
    )


def test_admit_release_requires_two_independent_approvers():
    admission = admit_release(
        _manifest(), requester_actor_subject="SIM-REQUESTER", approvals=_approvals(), now=NOW
    )
    assert admission.manifest_digest == _manifest().manifest_digest()
    assert set(admission.approvers) == {"SIM-APPROVER-A", "SIM-APPROVER-B"}


def test_admit_release_rejects_single_approver():
    with pytest.raises(IndependentApprovalError):
        admit_release(
            _manifest(),
            requester_actor_subject="SIM-REQUESTER",
            approvals=_approvals()[:1],
            now=NOW,
        )


def test_admit_release_rejects_requester_as_approver():
    approvals = (
        CriticalActionApproval(approver_actor_subject="SIM-REQUESTER", reason="self"),
        CriticalActionApproval(approver_actor_subject="SIM-APPROVER-B", reason="reviewed"),
    )
    with pytest.raises(IndependentApprovalError):
        admit_release(_manifest(), requester_actor_subject="SIM-REQUESTER", approvals=approvals, now=NOW)


def test_verify_activation_succeeds_when_everything_matches():
    manifest = _manifest()
    admission = admit_release(manifest, requester_actor_subject="SIM-REQUESTER", approvals=_approvals(), now=NOW)
    running = RunningDeploymentState(
        artifact_digest=manifest.artifact_digest,
        configuration_schema_digest=manifest.configuration_schema_digest,
        minimum_version_epoch=manifest.minimum_version_epoch,
    )
    verify_activation(manifest, admission=admission, running=running, current_minimum_version_epoch=3)


def test_verify_activation_rejects_admission_for_a_different_manifest():
    manifest_a = _manifest()
    manifest_b = _manifest(artifact_digest="sha256:artifact-v2-DIFFERENT")
    admission_for_a = admit_release(manifest_a, requester_actor_subject="SIM-REQUESTER", approvals=_approvals(), now=NOW)
    running = RunningDeploymentState(
        artifact_digest=manifest_b.artifact_digest,
        configuration_schema_digest=manifest_b.configuration_schema_digest,
        minimum_version_epoch=manifest_b.minimum_version_epoch,
    )
    with pytest.raises(DigestMismatchError):
        verify_activation(manifest_b, admission=admission_for_a, running=running, current_minimum_version_epoch=3)


def test_verify_activation_rejects_binary_swap_after_admission():
    """A malicious/stale update swapping the binary after admission but
    presenting the OLD admitted manifest must be rejected -- the running
    artifact_digest won't match what was actually admitted."""
    manifest = _manifest()
    admission = admit_release(manifest, requester_actor_subject="SIM-REQUESTER", approvals=_approvals(), now=NOW)
    swapped_running = RunningDeploymentState(
        artifact_digest="sha256:SWAPPED-MALICIOUS-BINARY",
        configuration_schema_digest=manifest.configuration_schema_digest,
        minimum_version_epoch=manifest.minimum_version_epoch,
    )
    with pytest.raises(DigestMismatchError):
        verify_activation(manifest, admission=admission, running=swapped_running, current_minimum_version_epoch=3)


def test_verify_activation_rejects_rollback_below_security_floor():
    old_manifest = _manifest(minimum_version_epoch=1, artifact_digest="sha256:old-vulnerable-release")
    admission = admit_release(old_manifest, requester_actor_subject="SIM-REQUESTER", approvals=_approvals(), now=NOW)
    running = RunningDeploymentState(
        artifact_digest=old_manifest.artifact_digest,
        configuration_schema_digest=old_manifest.configuration_schema_digest,
        minimum_version_epoch=1,
    )
    with pytest.raises(RollbackBelowFloorError):
        verify_activation(old_manifest, admission=admission, running=running, current_minimum_version_epoch=5)


def test_verify_activation_rejects_stale_running_epoch_even_if_manifest_is_current():
    manifest = _manifest(minimum_version_epoch=5)
    admission = admit_release(manifest, requester_actor_subject="SIM-REQUESTER", approvals=_approvals(), now=NOW)
    stale_running = RunningDeploymentState(
        artifact_digest=manifest.artifact_digest,
        configuration_schema_digest=manifest.configuration_schema_digest,
        minimum_version_epoch=2,  # somehow the running process itself is on a stale epoch
    )
    with pytest.raises(StaleMinimumVersionError):
        verify_activation(manifest, admission=admission, running=stale_running, current_minimum_version_epoch=5)


def test_manifest_digest_is_deterministic():
    assert _manifest().manifest_digest() == _manifest().manifest_digest()


def test_manifest_digest_changes_with_any_field():
    assert _manifest().manifest_digest() != _manifest(source_revision="different-revision").manifest_digest()

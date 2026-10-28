from __future__ import annotations

import datetime as dt

import pytest

from cassandre_security.backup_restore import (
    BackupRestoreError,
    DatabaseSnapshot,
    QuarantineStatus,
    build_coherent_recovery_set,
    verify_quarantine_before_reopen,
)

NOW = dt.datetime(2026, 1, 5, 12, 0, 0, tzinfo=dt.timezone.utc)


def _snapshot(**overrides) -> DatabaseSnapshot:
    defaults = dict(
        product="clauseos",
        database_name="clauseos",
        dump_path="/tmp/fake.sql",
        dump_sha256="a" * 64,
        taken_at=NOW,
    )
    defaults.update(overrides)
    return DatabaseSnapshot(**defaults)


def test_build_coherent_recovery_set_succeeds_with_valid_inputs():
    recovery_set = build_coherent_recovery_set(
        recovery_set_id="SIM-RECOVERY-0001",
        write_pause_started_at=NOW,
        write_pause_ended_at=NOW + dt.timedelta(minutes=5),
        database_snapshots=(_snapshot(),),
        identity_state_digest="sha256:identity-v1",
        public_trust_versions={"clauseos": "v1"},
        configuration_digest="sha256:config-v1",
        migration_heads={"clauseos": "0002_security_fence"},
        tombstone_count=0,
        checkpoint_sequences={"clauseos": 5},
    )
    assert recovery_set.manifest_digest() == recovery_set.manifest_digest()


def test_build_coherent_recovery_set_rejects_empty_snapshots():
    with pytest.raises(BackupRestoreError):
        build_coherent_recovery_set(
            recovery_set_id="SIM-RECOVERY-0001",
            write_pause_started_at=NOW,
            write_pause_ended_at=NOW + dt.timedelta(minutes=5),
            database_snapshots=(),
            identity_state_digest="sha256:identity-v1",
            public_trust_versions={},
            configuration_digest="sha256:config-v1",
            migration_heads={},
            tombstone_count=0,
            checkpoint_sequences={},
        )


def test_build_coherent_recovery_set_rejects_inverted_write_pause_window():
    with pytest.raises(BackupRestoreError):
        build_coherent_recovery_set(
            recovery_set_id="SIM-RECOVERY-0001",
            write_pause_started_at=NOW,
            write_pause_ended_at=NOW - dt.timedelta(minutes=5),
            database_snapshots=(_snapshot(),),
            identity_state_digest="sha256:identity-v1",
            public_trust_versions={},
            configuration_digest="sha256:config-v1",
            migration_heads={},
            tombstone_count=0,
            checkpoint_sequences={},
        )


def test_build_coherent_recovery_set_rejects_snapshot_taken_outside_pause_window():
    outside_window_snapshot = _snapshot(taken_at=NOW + dt.timedelta(hours=1))
    with pytest.raises(BackupRestoreError):
        build_coherent_recovery_set(
            recovery_set_id="SIM-RECOVERY-0001",
            write_pause_started_at=NOW,
            write_pause_ended_at=NOW + dt.timedelta(minutes=5),
            database_snapshots=(outside_window_snapshot,),
            identity_state_digest="sha256:identity-v1",
            public_trust_versions={},
            configuration_digest="sha256:config-v1",
            migration_heads={},
            tombstone_count=0,
            checkpoint_sequences={},
        )


def test_manifest_digest_is_deterministic_regardless_of_dict_insertion_order():
    kwargs = dict(
        recovery_set_id="SIM-RECOVERY-0001",
        write_pause_started_at=NOW,
        write_pause_ended_at=NOW + dt.timedelta(minutes=5),
        database_snapshots=(_snapshot(),),
        identity_state_digest="sha256:identity-v1",
        configuration_digest="sha256:config-v1",
        tombstone_count=0,
    )
    set_a = build_coherent_recovery_set(
        **kwargs, public_trust_versions={"clauseos": "v1", "riskos": "v1"},
        migration_heads={"clauseos": "h1", "riskos": "h2"}, checkpoint_sequences={"clauseos": 1, "riskos": 2},
    )
    set_b = build_coherent_recovery_set(
        **kwargs, public_trust_versions={"riskos": "v1", "clauseos": "v1"},
        migration_heads={"riskos": "h2", "clauseos": "h1"}, checkpoint_sequences={"riskos": 2, "clauseos": 1},
    )
    assert set_a.manifest_digest() == set_b.manifest_digest()


def _complete_recovery_set() -> "object":
    return build_coherent_recovery_set(
        recovery_set_id="SIM-RECOVERY-0001",
        write_pause_started_at=NOW,
        write_pause_ended_at=NOW + dt.timedelta(minutes=5),
        database_snapshots=(_snapshot(),),
        identity_state_digest="sha256:identity-v1",
        public_trust_versions={"clauseos": "v1"},
        configuration_digest="sha256:config-v1",
        migration_heads={"clauseos": "0002_security_fence"},
        tombstone_count=0,
        checkpoint_sequences={"clauseos": 5},
    )


def test_quarantine_verification_ready_to_reopen_only_when_everything_checks_out():
    result = verify_quarantine_before_reopen(
        recovery_set=_complete_recovery_set(),
        current_revocations_known=True,
        current_tombstones_applied=True,
        current_minimum_policy_epoch_known=True,
        witness_checkpoints_verified=True,
        reconciled_domain_state=True,
    )
    assert result.status is QuarantineStatus.VERIFIED_READY_TO_REOPEN
    assert result.reasons == ()


@pytest.mark.parametrize(
    "field_name",
    [
        "current_revocations_known",
        "current_tombstones_applied",
        "current_minimum_policy_epoch_known",
        "witness_checkpoints_verified",
        "reconciled_domain_state",
    ],
)
def test_quarantine_verification_blocks_reopen_if_any_single_check_fails(field_name):
    kwargs = dict(
        current_revocations_known=True,
        current_tombstones_applied=True,
        current_minimum_policy_epoch_known=True,
        witness_checkpoints_verified=True,
        reconciled_domain_state=True,
    )
    kwargs[field_name] = False
    result = verify_quarantine_before_reopen(recovery_set=_complete_recovery_set(), **kwargs)
    assert result.status is QuarantineStatus.QUARANTINED_FAILED_VERIFICATION
    assert len(result.reasons) == 1

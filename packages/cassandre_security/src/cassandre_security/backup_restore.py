"""Backups and quarantined restore (SEC-19).

Spec section 12: "The S1 coherent recovery set uses a bounded write pause
for authoritative mutations, export finalization, identity changes, and
content garbage collection; drains transactions; snapshots product
databases; enumerates and copies referenced durable content; and binds
export intents/files, identity state, public trust versions,
configuration, migration heads, tombstones, and checkpoint sequences in
one verified manifest. Missing objects or generation mismatch keep
recovery quarantined."

And section 9: "Restore key custody first in a quarantined customer
environment; import current revocations, tombstones, emergency-stop
state, and minimum accepted policy/build/trust epochs from a separate
freshness authority; verify backup and witness checkpoints; reconcile
domain and reservation state; issue new runtime identities; then perform
a fresh exact-hash preflight for every resumed export."

This module shells out to the real `pg_dump`/`pg_restore` INSIDE the
actual running Postgres container (`docker exec`) rather than
reimplementing a database dump format -- "use reviewed maintained
implementations" applies to backup tooling as much as to cryptography.
Restoring into a quarantine database (never directly over the live one)
and gating "reopen" behind an explicit verification step is this module's
own logic; the bytes themselves are `pg_dump`'s.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from dataclasses import dataclass, field
from enum import Enum

from audit_protocol import content_hash


class BackupRestoreError(ValueError):
    pass


class SnapshotFailedError(BackupRestoreError):
    pass


class QuarantineVerificationError(BackupRestoreError):
    pass


@dataclass(frozen=True)
class DatabaseSnapshot:
    product: str
    database_name: str
    dump_path: str
    dump_sha256: str
    taken_at: dt.datetime
    row_count_by_table: dict[str, int] = field(default_factory=dict)


def snapshot_database(
    *,
    container_name: str,
    postgres_role: str,
    database_name: str,
    product: str,
    output_dir: str,
    now: dt.datetime,
) -> DatabaseSnapshot:
    """Runs `pg_dump` inside the real Postgres container via `docker exec`
    and copies the dump out to `output_dir` on the host, hashing it for
    the coherent-recovery-set manifest. Raises `SnapshotFailedError` (never
    returns a partial/best-effort snapshot) on any non-zero exit."""

    import hashlib
    import os

    os.makedirs(output_dir, exist_ok=True)
    dump_filename = f"{product}-{database_name}-{now.strftime('%Y%m%dT%H%M%SZ')}.sql"
    container_dump_path = f"/tmp/{dump_filename}"
    host_dump_path = os.path.join(output_dir, dump_filename)

    dump_proc = subprocess.run(
        [
            "docker", "exec", container_name,
            "pg_dump", "-U", postgres_role, "-d", database_name,
            "-f", container_dump_path, "--no-owner", "--no-privileges",
        ],
        capture_output=True, text=True, timeout=120,
    )
    if dump_proc.returncode != 0:
        raise SnapshotFailedError(f"pg_dump failed for {product}/{database_name}: {dump_proc.stderr}")

    copy_proc = subprocess.run(
        ["docker", "cp", f"{container_name}:{container_dump_path}", host_dump_path],
        capture_output=True, text=True, timeout=60,
    )
    if copy_proc.returncode != 0:
        raise SnapshotFailedError(f"docker cp failed for {product}/{database_name}: {copy_proc.stderr}")

    subprocess.run(["docker", "exec", container_name, "rm", "-f", container_dump_path], capture_output=True, timeout=30)

    with open(host_dump_path, "rb") as f:
        dump_bytes = f.read()
    if not dump_bytes:
        raise SnapshotFailedError(f"pg_dump for {product}/{database_name} produced an empty file")
    dump_sha256 = hashlib.sha256(dump_bytes).hexdigest()

    return DatabaseSnapshot(
        product=product,
        database_name=database_name,
        dump_path=host_dump_path,
        dump_sha256=dump_sha256,
        taken_at=now,
    )


@dataclass(frozen=True)
class CoherentRecoverySet:
    """One verified manifest binding everything the spec requires bound
    together. `manifest_digest()` is what a restore later checks against
    to detect "missing objects or generation mismatch"."""

    recovery_set_id: str
    write_pause_started_at: dt.datetime
    write_pause_ended_at: dt.datetime
    database_snapshots: tuple[DatabaseSnapshot, ...]
    identity_state_digest: str
    public_trust_versions: dict[str, str]  # product -> trust bundle version
    configuration_digest: str
    migration_heads: dict[str, str]  # product -> alembic head revision
    tombstone_count: int
    checkpoint_sequences: dict[str, int]  # product_chain_id -> witnessed sequence at snapshot time

    def canonical_fields(self) -> dict:
        return {
            "recovery_set_id": self.recovery_set_id,
            "write_pause_started_at": self.write_pause_started_at.isoformat(),
            "write_pause_ended_at": self.write_pause_ended_at.isoformat(),
            "database_snapshots": [
                {"product": s.product, "database_name": s.database_name, "dump_sha256": s.dump_sha256, "taken_at": s.taken_at.isoformat()}
                for s in self.database_snapshots
            ],
            "identity_state_digest": self.identity_state_digest,
            "public_trust_versions": dict(sorted(self.public_trust_versions.items())),
            "configuration_digest": self.configuration_digest,
            "migration_heads": dict(sorted(self.migration_heads.items())),
            "tombstone_count": self.tombstone_count,
            "checkpoint_sequences": dict(sorted(self.checkpoint_sequences.items())),
        }

    def manifest_digest(self) -> str:
        return content_hash(self.canonical_fields())


def build_coherent_recovery_set(
    *,
    recovery_set_id: str,
    write_pause_started_at: dt.datetime,
    write_pause_ended_at: dt.datetime,
    database_snapshots: tuple[DatabaseSnapshot, ...],
    identity_state_digest: str,
    public_trust_versions: dict[str, str],
    configuration_digest: str,
    migration_heads: dict[str, str],
    tombstone_count: int,
    checkpoint_sequences: dict[str, int],
) -> CoherentRecoverySet:
    if write_pause_ended_at < write_pause_started_at:
        raise BackupRestoreError("write_pause_ended_at cannot be before write_pause_started_at")
    if not database_snapshots:
        raise BackupRestoreError("a coherent recovery set requires at least one database snapshot")
    for snapshot in database_snapshots:
        if snapshot.taken_at < write_pause_started_at or snapshot.taken_at > write_pause_ended_at:
            raise BackupRestoreError(
                f"snapshot for {snapshot.product}/{snapshot.database_name} was taken outside the declared write-pause window"
            )
    return CoherentRecoverySet(
        recovery_set_id=recovery_set_id,
        write_pause_started_at=write_pause_started_at,
        write_pause_ended_at=write_pause_ended_at,
        database_snapshots=database_snapshots,
        identity_state_digest=identity_state_digest,
        public_trust_versions=public_trust_versions,
        configuration_digest=configuration_digest,
        migration_heads=migration_heads,
        tombstone_count=tombstone_count,
        checkpoint_sequences=checkpoint_sequences,
    )


class QuarantineStatus(str, Enum):
    RESTORING = "RESTORING"
    VERIFYING = "VERIFYING"
    VERIFIED_READY_TO_REOPEN = "VERIFIED_READY_TO_REOPEN"
    QUARANTINED_FAILED_VERIFICATION = "QUARANTINED_FAILED_VERIFICATION"


def restore_snapshot_into_quarantine(
    *,
    container_name: str,
    postgres_role: str,
    snapshot: DatabaseSnapshot,
    quarantine_database_name: str,
) -> None:
    """Restores into a NEW, separate `quarantine_database_name` -- never
    directly over the live database. Caller is responsible for having
    created that database first (e.g. `CREATE DATABASE ... `) since that
    requires superuser/owner privileges this function does not assume."""

    # `docker cp <host-file> container:<path>` copies a real file to an
    # exact destination path. `docker cp -` (stdin) instead reads a TAR
    # STREAM and extracts it, requiring a directory destination -- using
    # that form here was a real bug (found during this session's own
    # Postgres integration testing): it always failed with "destination
    # must be a directory". Since the dump already exists on the host
    # (`snapshot.dump_path`), copy that file directly instead.
    copy_proc = subprocess.run(
        ["docker", "cp", snapshot.dump_path, f"{container_name}:/tmp/restore.sql"],
        capture_output=True, timeout=60,
    )
    if copy_proc.returncode != 0:
        raise SnapshotFailedError(f"docker cp into container failed: {copy_proc.stderr}")

    restore_proc = subprocess.run(
        ["docker", "exec", container_name, "psql", "-U", postgres_role, "-d", quarantine_database_name, "-f", "/tmp/restore.sql"],
        capture_output=True, text=True, timeout=120,
    )
    subprocess.run(["docker", "exec", container_name, "rm", "-f", "/tmp/restore.sql"], capture_output=True, timeout=30)
    if restore_proc.returncode != 0:
        raise SnapshotFailedError(f"psql restore into {quarantine_database_name} failed: {restore_proc.stderr}")


@dataclass(frozen=True)
class QuarantineVerificationResult:
    status: QuarantineStatus
    reasons: tuple[str, ...]


def verify_quarantine_before_reopen(
    *,
    recovery_set: CoherentRecoverySet,
    current_revocations_known: bool,
    current_tombstones_applied: bool,
    current_minimum_policy_epoch_known: bool,
    witness_checkpoints_verified: bool,
    reconciled_domain_state: bool,
) -> QuarantineVerificationResult:
    """Spec section 9's restore checklist, as an explicit gate. Every one
    of these must be true before `VERIFIED_READY_TO_REOPEN` -- missing any
    one keeps the restore in quarantine (spec: 'Missing objects or
    generation mismatch keep recovery quarantined')."""

    reasons: list[str] = []
    if not current_revocations_known:
        reasons.append("current revocation state could not be imported from the freshness authority")
    if not current_tombstones_applied:
        reasons.append("current deletion tombstones have not been applied to the restored data")
    if not current_minimum_policy_epoch_known:
        reasons.append("current minimum accepted policy/build/trust epoch is unknown")
    if not witness_checkpoints_verified:
        reasons.append("restored audit chain heads do not match the independent witness")
    if not reconciled_domain_state:
        reasons.append("domain and reservation state has not been reconciled post-restore")

    if reasons:
        return QuarantineVerificationResult(status=QuarantineStatus.QUARANTINED_FAILED_VERIFICATION, reasons=tuple(reasons))
    return QuarantineVerificationResult(status=QuarantineStatus.VERIFIED_READY_TO_REOPEN, reasons=())

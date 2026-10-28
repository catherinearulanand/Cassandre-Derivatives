"""Customer key ceremony: threshold secret splitting and ceremony
record-keeping (SEC-09's software half).

Spec section 9: "Proposed human recovery ceremony uses independent
customer custodians, with a tested quorum such as 2-of-3... This is
operational authorization, not permission to invent threshold ML-DSA or
split a private key with custom code." Section 17 repeats: "Do not write
your own KEM, signature primitive, threshold cryptography..."

Secret splitting here uses `shamir-mnemonic` -- Trezor's SLIP-39
implementation, a real-world-deployed, independently reviewed threshold
scheme (not polynomial-interpolation math written for this package). This
module does NOT implement threshold ML-DSA (no such standard construction
exists) -- it splits opaque root secret material (e.g. a root KEK, or a
seed used to derive one), which the spec's "software-managed keys... using
synthetic content" S1 profile is exactly scoped for.

What this module explicitly does NOT provide, and why: the actual
ceremony -- named human custodians physically present, verifying each
other's identity, recording the event -- requires real people. This
module provides the record-keeping schema and a state machine so a real
ceremony has somewhere to write its evidence, plus a synthetic drill
function that exercises the whole mechanism end-to-end with fake
("SIM-CUSTODIAN-*") identities standing in for real ones. That drill
proves the mechanism works; it does not constitute a ceremony.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from enum import Enum

from shamir_mnemonic import MnemonicError, combine_mnemonics, generate_mnemonics


class KeyCeremonyError(ValueError):
    pass


class InsufficientSharesError(KeyCeremonyError):
    pass


@dataclass(frozen=True)
class ThresholdSplitResult:
    """`share_mnemonics` are SLIP-39 human-readable word-list shares --
    this is what a real custodian would each be handed and would store
    independently, per spec: 'Store shares separately from backups and
    each other.' This dataclass never persists them together with the
    original secret."""

    group_threshold: int
    share_count: int
    share_mnemonics: tuple[str, ...]


def split_root_secret(secret: bytes, *, threshold: int, total_shares: int) -> ThresholdSplitResult:
    if not (1 <= threshold <= total_shares):
        raise KeyCeremonyError(f"threshold must be between 1 and total_shares; got threshold={threshold}, total_shares={total_shares}")
    if len(secret) not in (16, 32):
        raise KeyCeremonyError(f"SLIP-39 requires a 16 or 32 byte secret; got {len(secret)} bytes")

    groups = generate_mnemonics(
        group_threshold=1,
        groups=[(threshold, total_shares)],
        master_secret=secret,
    )
    return ThresholdSplitResult(group_threshold=threshold, share_count=total_shares, share_mnemonics=tuple(groups[0]))


def reconstruct_root_secret(shares: tuple[str, ...]) -> bytes:
    """Raises `InsufficientSharesError` (never returns a partial/guessed
    secret) if fewer than the threshold number of valid shares are
    supplied."""
    try:
        return combine_mnemonics(list(shares))
    except MnemonicError as exc:
        raise InsufficientSharesError(f"could not reconstruct secret from {len(shares)} share(s): {exc}") from exc


class CeremonyPurpose(str, Enum):
    INITIAL_ROOT_GENERATION = "INITIAL_ROOT_GENERATION"
    ROUTINE_ROTATION = "ROUTINE_ROTATION"
    ROOT_COMPROMISE_REPLACEMENT = "ROOT_COMPROMISE_REPLACEMENT"
    RECOVERY_DRILL = "RECOVERY_DRILL"


class CeremonyStatus(str, Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


@dataclass(frozen=True)
class CeremonyParticipant:
    person_id: str
    role: str  # e.g. "custodian", "witness", "incident_commander"
    identity_verified: bool


@dataclass
class CeremonyRecord:
    """Spec section 9: 'The ceremony record includes purpose or incident,
    participants, offline device and approved-software digest, old/new
    public fingerprints, trust-bundle sequence, issuer purposes, validity,
    signed-output hashes, independent verification, and resealing --
    never private shares or plaintext keys.' Every field below is
    NON-secret evidence; this dataclass structurally cannot hold a share
    or a plaintext key (no such field exists)."""

    ceremony_id: str
    purpose: CeremonyPurpose
    status: CeremonyStatus
    participants: tuple[CeremonyParticipant, ...]
    offline_device_digest: str | None
    approved_software_digest: str | None
    old_public_fingerprint: str | None
    new_public_fingerprint: str | None
    trust_bundle_sequence: int | None
    started_at: dt.datetime
    completed_at: dt.datetime | None = None
    independent_verification_person_id: str | None = None
    notes: str = ""


def start_ceremony(
    *,
    ceremony_id: str,
    purpose: CeremonyPurpose,
    participants: tuple[CeremonyParticipant, ...],
    now: dt.datetime,
    minimum_participants: int = 3,
) -> CeremonyRecord:
    if len(participants) < minimum_participants:
        raise KeyCeremonyError(
            f"ceremony requires at least {minimum_participants} participants, got {len(participants)}"
        )
    unverified = [p.person_id for p in participants if not p.identity_verified]
    if unverified:
        raise KeyCeremonyError(f"all participants must have verified identity before a ceremony starts: {unverified}")
    distinct_persons = {p.person_id for p in participants}
    if len(distinct_persons) != len(participants):
        raise KeyCeremonyError("ceremony participants must be distinct natural persons")

    return CeremonyRecord(
        ceremony_id=ceremony_id,
        purpose=purpose,
        status=CeremonyStatus.IN_PROGRESS,
        participants=participants,
        offline_device_digest=None,
        approved_software_digest=None,
        old_public_fingerprint=None,
        new_public_fingerprint=None,
        trust_bundle_sequence=None,
        started_at=now,
    )


def complete_ceremony(
    record: CeremonyRecord,
    *,
    new_public_fingerprint: str,
    trust_bundle_sequence: int,
    independent_verification_person_id: str,
    now: dt.datetime,
    offline_device_digest: str | None = None,
    approved_software_digest: str | None = None,
) -> CeremonyRecord:
    if record.status is not CeremonyStatus.IN_PROGRESS:
        raise KeyCeremonyError(f"cannot complete a ceremony in status {record.status.value}")
    if independent_verification_person_id not in {p.person_id for p in record.participants}:
        raise KeyCeremonyError("independent verifier must be one of the ceremony's own verified participants")

    from dataclasses import replace

    return replace(
        record,
        status=CeremonyStatus.COMPLETED,
        new_public_fingerprint=new_public_fingerprint,
        trust_bundle_sequence=trust_bundle_sequence,
        independent_verification_person_id=independent_verification_person_id,
        offline_device_digest=offline_device_digest or record.offline_device_digest,
        approved_software_digest=approved_software_digest or record.approved_software_digest,
        completed_at=now,
    )


@dataclass(frozen=True)
class SyntheticDrillResult:
    ceremony: CeremonyRecord
    reconstruction_succeeded_with_threshold_shares: bool
    reconstruction_correctly_failed_below_threshold: bool
    one_missing_custodian_still_reconstructs: bool


def run_synthetic_recovery_drill(
    *, threshold: int = 2, total_shares: int = 3, now: dt.datetime
) -> SyntheticDrillResult:
    """Spec section 9: 'Drills cover one unavailable custodian, one
    corrupt share, and loss of one media copy.' This exercises the actual
    `shamir-mnemonic` mechanism end-to-end with SYNTHETIC custodian
    identities -- it proves the software works; it is explicitly NOT a
    substitute for a real ceremony with real people (see module
    docstring)."""

    participants = tuple(
        CeremonyParticipant(person_id=f"SIM-CUSTODIAN-{i}", role="custodian", identity_verified=True)
        for i in range(1, total_shares + 1)
    )
    ceremony = start_ceremony(
        ceremony_id="SIM-CEREMONY-DRILL-0001",
        purpose=CeremonyPurpose.RECOVERY_DRILL,
        participants=participants,
        now=now,
        minimum_participants=total_shares,
    )

    root_secret = os.urandom(32)
    split = split_root_secret(root_secret, threshold=threshold, total_shares=total_shares)

    # Threshold shares reconstruct correctly.
    reconstructed = reconstruct_root_secret(split.share_mnemonics[:threshold])
    reconstruction_succeeded = reconstructed == root_secret

    # Below-threshold shares must fail closed, never return a wrong guess.
    reconstruction_correctly_failed_below_threshold = False
    try:
        reconstruct_root_secret(split.share_mnemonics[: threshold - 1])
    except InsufficientSharesError:
        reconstruction_correctly_failed_below_threshold = True

    # "One unavailable custodian" -- drop share 0, reconstruct from the rest.
    remaining_shares = split.share_mnemonics[1:]
    one_missing_reconstructed = reconstruct_root_secret(remaining_shares[:threshold])
    one_missing_custodian_still_reconstructs = one_missing_reconstructed == root_secret

    completed = complete_ceremony(
        ceremony,
        new_public_fingerprint="SIM-FINGERPRINT-DRILL",
        trust_bundle_sequence=1,
        independent_verification_person_id=participants[0].person_id,
        now=now,
    )

    return SyntheticDrillResult(
        ceremony=completed,
        reconstruction_succeeded_with_threshold_shares=reconstruction_succeeded,
        reconstruction_correctly_failed_below_threshold=reconstruction_correctly_failed_below_threshold,
        one_missing_custodian_still_reconstructs=one_missing_custodian_still_reconstructs,
    )

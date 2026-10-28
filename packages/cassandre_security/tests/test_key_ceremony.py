from __future__ import annotations

import datetime as dt
import os

import pytest

from cassandre_security.key_ceremony import (
    CeremonyParticipant,
    CeremonyPurpose,
    CeremonyStatus,
    InsufficientSharesError,
    KeyCeremonyError,
    complete_ceremony,
    reconstruct_root_secret,
    run_synthetic_recovery_drill,
    split_root_secret,
    start_ceremony,
)

NOW = dt.datetime(2026, 1, 5, 12, 0, 0, tzinfo=dt.timezone.utc)


def test_split_and_reconstruct_with_exact_threshold():
    secret = os.urandom(32)
    split = split_root_secret(secret, threshold=2, total_shares=3)
    assert len(split.share_mnemonics) == 3
    recovered = reconstruct_root_secret(split.share_mnemonics[:2])
    assert recovered == secret


def test_below_threshold_shares_fail_closed():
    secret = os.urandom(32)
    split = split_root_secret(secret, threshold=2, total_shares=3)
    with pytest.raises(InsufficientSharesError):
        reconstruct_root_secret(split.share_mnemonics[:1])


def test_any_two_of_three_shares_work_not_just_the_first_two():
    secret = os.urandom(32)
    split = split_root_secret(secret, threshold=2, total_shares=3)
    assert reconstruct_root_secret((split.share_mnemonics[0], split.share_mnemonics[2])) == secret
    assert reconstruct_root_secret((split.share_mnemonics[1], split.share_mnemonics[2])) == secret


def test_invalid_threshold_rejected():
    with pytest.raises(KeyCeremonyError):
        split_root_secret(os.urandom(32), threshold=5, total_shares=3)


def test_wrong_secret_length_rejected():
    with pytest.raises(KeyCeremonyError):
        split_root_secret(os.urandom(20), threshold=2, total_shares=3)


def _participants(n: int) -> tuple[CeremonyParticipant, ...]:
    return tuple(
        CeremonyParticipant(person_id=f"SIM-CUSTODIAN-{i}", role="custodian", identity_verified=True)
        for i in range(1, n + 1)
    )


def test_start_ceremony_requires_minimum_participants():
    with pytest.raises(KeyCeremonyError):
        start_ceremony(
            ceremony_id="SIM-CEREMONY-1",
            purpose=CeremonyPurpose.INITIAL_ROOT_GENERATION,
            participants=_participants(2),
            now=NOW,
            minimum_participants=3,
        )


def test_start_ceremony_requires_verified_identity():
    unverified = (CeremonyParticipant(person_id="SIM-CUSTODIAN-1", role="custodian", identity_verified=False),) + _participants(2)[1:]
    with pytest.raises(KeyCeremonyError):
        start_ceremony(
            ceremony_id="SIM-CEREMONY-1",
            purpose=CeremonyPurpose.INITIAL_ROOT_GENERATION,
            participants=unverified,
            now=NOW,
            minimum_participants=3,
        )


def test_start_ceremony_requires_distinct_persons():
    duplicated = (
        CeremonyParticipant(person_id="SIM-CUSTODIAN-1", role="custodian", identity_verified=True),
        CeremonyParticipant(person_id="SIM-CUSTODIAN-1", role="witness", identity_verified=True),
        CeremonyParticipant(person_id="SIM-CUSTODIAN-2", role="custodian", identity_verified=True),
    )
    with pytest.raises(KeyCeremonyError):
        start_ceremony(
            ceremony_id="SIM-CEREMONY-1",
            purpose=CeremonyPurpose.INITIAL_ROOT_GENERATION,
            participants=duplicated,
            now=NOW,
            minimum_participants=3,
        )


def test_complete_ceremony_requires_verifier_to_be_a_participant():
    ceremony = start_ceremony(
        ceremony_id="SIM-CEREMONY-1",
        purpose=CeremonyPurpose.INITIAL_ROOT_GENERATION,
        participants=_participants(3),
        now=NOW,
        minimum_participants=3,
    )
    with pytest.raises(KeyCeremonyError):
        complete_ceremony(
            ceremony,
            new_public_fingerprint="fp",
            trust_bundle_sequence=1,
            independent_verification_person_id="SIM-NOT-A-PARTICIPANT",
            now=NOW,
        )


def test_complete_ceremony_success():
    ceremony = start_ceremony(
        ceremony_id="SIM-CEREMONY-1",
        purpose=CeremonyPurpose.INITIAL_ROOT_GENERATION,
        participants=_participants(3),
        now=NOW,
        minimum_participants=3,
    )
    completed = complete_ceremony(
        ceremony,
        new_public_fingerprint="fp-1",
        trust_bundle_sequence=1,
        independent_verification_person_id="SIM-CUSTODIAN-2",
        now=NOW,
    )
    assert completed.status is CeremonyStatus.COMPLETED
    assert completed.new_public_fingerprint == "fp-1"


def test_cannot_complete_an_already_completed_ceremony():
    ceremony = start_ceremony(
        ceremony_id="SIM-CEREMONY-1",
        purpose=CeremonyPurpose.INITIAL_ROOT_GENERATION,
        participants=_participants(3),
        now=NOW,
        minimum_participants=3,
    )
    completed = complete_ceremony(
        ceremony, new_public_fingerprint="fp-1", trust_bundle_sequence=1,
        independent_verification_person_id="SIM-CUSTODIAN-2", now=NOW,
    )
    with pytest.raises(KeyCeremonyError):
        complete_ceremony(
            completed, new_public_fingerprint="fp-2", trust_bundle_sequence=2,
            independent_verification_person_id="SIM-CUSTODIAN-2", now=NOW,
        )


def test_synthetic_recovery_drill_covers_all_required_scenarios():
    """Spec section 9: 'Drills cover one unavailable custodian, one
    corrupt share, and loss of one media copy.'"""
    result = run_synthetic_recovery_drill(threshold=2, total_shares=3, now=NOW)
    assert result.reconstruction_succeeded_with_threshold_shares is True
    assert result.reconstruction_correctly_failed_below_threshold is True
    assert result.one_missing_custodian_still_reconstructs is True
    assert result.ceremony.status is CeremonyStatus.COMPLETED
    assert result.ceremony.purpose is CeremonyPurpose.RECOVERY_DRILL


def test_corrupt_share_is_rejected_not_silently_accepted():
    secret = os.urandom(32)
    split = split_root_secret(secret, threshold=2, total_shares=3)
    corrupted_share = split.share_mnemonics[0].replace(split.share_mnemonics[0].split()[0], "academic")
    with pytest.raises((InsufficientSharesError,)):
        reconstruct_root_secret((corrupted_share, split.share_mnemonics[1]))

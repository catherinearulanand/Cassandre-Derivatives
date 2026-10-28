from __future__ import annotations

import datetime as dt

import pytest

from cassandre_security.key_lifecycle import (
    KeyLifecycleError,
    KeyPurpose,
    KeyRecord,
    KeyState,
    assert_may_decrypt_or_verify_historical,
    assert_may_encrypt_or_sign_new,
    transition,
)

NOW = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def _pending_key() -> KeyRecord:
    return KeyRecord(
        key_id="SIM-KEY-1",
        purpose=KeyPurpose.DATA_WRAPPING,
        owner_person_id="SIM-PERSON-1",
        scope="product=clauseos",
        custody_location="local-software-adapter",
        state=KeyState.PENDING,
        created_at=NOW,
    )


def test_pending_to_active_sets_activated_at():
    key = _pending_key()
    activated = transition(key, to=KeyState.ACTIVE, now=NOW)
    assert activated.state is KeyState.ACTIVE
    assert activated.activated_at == NOW


def test_active_key_may_encrypt_and_sign():
    key = transition(_pending_key(), to=KeyState.ACTIVE, now=NOW)
    assert_may_encrypt_or_sign_new(key)


def test_pending_key_may_not_encrypt():
    with pytest.raises(KeyLifecycleError):
        assert_may_encrypt_or_sign_new(_pending_key())


def test_decrypt_only_key_may_not_encrypt_new_but_may_decrypt_historical():
    active = transition(_pending_key(), to=KeyState.ACTIVE, now=NOW)
    retiring = transition(active, to=KeyState.DECRYPT_ONLY, now=NOW)
    with pytest.raises(KeyLifecycleError):
        assert_may_encrypt_or_sign_new(retiring)
    assert_may_decrypt_or_verify_historical(retiring)


def test_compromised_key_may_not_decrypt_or_encrypt():
    active = transition(_pending_key(), to=KeyState.ACTIVE, now=NOW)
    compromised = transition(active, to=KeyState.COMPROMISED, now=NOW, reason="suspected exposure")
    assert compromised.revocation_reason == "suspected exposure"
    with pytest.raises(KeyLifecycleError):
        assert_may_encrypt_or_sign_new(compromised)
    with pytest.raises(KeyLifecycleError):
        assert_may_decrypt_or_verify_historical(compromised)


def test_destroyed_key_cannot_transition_anywhere():
    active = transition(_pending_key(), to=KeyState.ACTIVE, now=NOW)
    destroyed = transition(active, to=KeyState.COMPROMISED, now=NOW)
    destroyed = transition(destroyed, to=KeyState.DESTROYED, now=NOW)
    with pytest.raises(KeyLifecycleError):
        transition(destroyed, to=KeyState.ACTIVE, now=NOW)


def test_illegal_transition_pending_directly_to_compromised_is_still_allowed_only_via_active():
    # PENDING's only non-terminal transition is to ACTIVE -- a key that was
    # never activated cannot be marked COMPROMISED (there is nothing live
    # to compromise yet); it can only be discarded (DESTROYED).
    with pytest.raises(KeyLifecycleError):
        transition(_pending_key(), to=KeyState.COMPROMISED, now=NOW)


def test_suspended_key_can_be_reactivated():
    active = transition(_pending_key(), to=KeyState.ACTIVE, now=NOW)
    suspended = transition(active, to=KeyState.SUSPENDED, now=NOW)
    reactivated = transition(suspended, to=KeyState.ACTIVE, now=NOW)
    assert reactivated.state is KeyState.ACTIVE

"""Key lifecycle state machine (SEC-08/09's non-ceremony half).

Spec section 9: "Key lifecycle: planned -> generated under ceremony ->
tested -> activated -> retiring/decrypt-only or verify-only -> revoked or
destroyed. Encryption/signing with retired keys is denied while controlled
historical decryption/verification follows a separately authorized
policy."

This module implements ONLY the state machine and its transition rules.
The actual customer-custodian ceremony (2-of-3 quorum, offline root,
physical share custody) is explicitly out of scope this pass -- see
`COMPONENTS.md`. `KeyRecord` is a plain data/validation object a real
ceremony tool would populate.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum


class KeyState(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DECRYPT_ONLY = "DECRYPT_ONLY"  # retiring: no new encryption, existing ciphertext still readable
    VERIFY_ONLY = "VERIFY_ONLY"  # retiring: no new signatures, existing signatures still verifiable
    SUSPENDED = "SUSPENDED"
    COMPROMISED = "COMPROMISED"
    DESTROYED = "DESTROYED"


class KeyPurpose(str, Enum):
    DATA_WRAPPING = "DATA_WRAPPING"
    APPLICATION_SIGNING = "APPLICATION_SIGNING"
    AUDIT_CHECKPOINT_SIGNING = "AUDIT_CHECKPOINT_SIGNING"
    TLS_IDENTITY = "TLS_IDENTITY"
    RELEASE_SIGNING = "RELEASE_SIGNING"
    BACKUP_ENCRYPTION = "BACKUP_ENCRYPTION"


class KeyLifecycleError(ValueError):
    pass


# Allowed transitions. Every key ultimately reaches a terminal state
# (DESTROYED) or stays COMPROMISED pending incident-driven destruction --
# there is no transition back to ACTIVE from a retiring/compromised state
# (spec: "An expired signing key must not sign new artifacts even though
# its historic valid outputs remain inspectable").
_ALLOWED_TRANSITIONS: dict[KeyState, frozenset[KeyState]] = {
    KeyState.PENDING: frozenset({KeyState.ACTIVE, KeyState.DESTROYED}),
    KeyState.ACTIVE: frozenset({KeyState.DECRYPT_ONLY, KeyState.VERIFY_ONLY, KeyState.SUSPENDED, KeyState.COMPROMISED}),
    KeyState.DECRYPT_ONLY: frozenset({KeyState.COMPROMISED, KeyState.DESTROYED}),
    KeyState.VERIFY_ONLY: frozenset({KeyState.COMPROMISED, KeyState.DESTROYED}),
    KeyState.SUSPENDED: frozenset({KeyState.ACTIVE, KeyState.COMPROMISED, KeyState.DESTROYED}),
    KeyState.COMPROMISED: frozenset({KeyState.DESTROYED}),
    KeyState.DESTROYED: frozenset(),
}

# Which states may perform which fresh (non-historical) operation.
_CAN_ENCRYPT_OR_SIGN_NEW: frozenset[KeyState] = frozenset({KeyState.ACTIVE})
_CAN_DECRYPT_OR_VERIFY_HISTORICAL: frozenset[KeyState] = frozenset(
    {KeyState.ACTIVE, KeyState.DECRYPT_ONLY, KeyState.VERIFY_ONLY}
)


@dataclass(frozen=True)
class KeyRecord:
    key_id: str
    purpose: KeyPurpose
    owner_person_id: str
    scope: str  # e.g. "product=clauseos;tenant=SIM-TENANT-A"
    custody_location: str  # e.g. "local-software-adapter" or a real KMS/HSM identifier
    state: KeyState
    created_at: dt.datetime
    activated_at: dt.datetime | None = None
    retired_at: dt.datetime | None = None
    revocation_reason: str | None = None
    recovery_test_passed_at: dt.datetime | None = None


def transition(record: KeyRecord, *, to: KeyState, now: dt.datetime, reason: str | None = None) -> KeyRecord:
    if to not in _ALLOWED_TRANSITIONS[record.state]:
        raise KeyLifecycleError(f"illegal key transition {record.state.value} -> {to.value} for key {record.key_id!r}")

    kwargs: dict = {"state": to}
    if to is KeyState.ACTIVE and record.state is KeyState.PENDING:
        kwargs["activated_at"] = now
    if to in (KeyState.DECRYPT_ONLY, KeyState.VERIFY_ONLY, KeyState.SUSPENDED):
        kwargs["retired_at"] = now
    if to in (KeyState.COMPROMISED, KeyState.DESTROYED):
        kwargs["revocation_reason"] = reason or record.revocation_reason

    from dataclasses import replace

    return replace(record, **kwargs)


def assert_may_encrypt_or_sign_new(record: KeyRecord) -> None:
    if record.state not in _CAN_ENCRYPT_OR_SIGN_NEW:
        raise KeyLifecycleError(
            f"key {record.key_id!r} is in state {record.state.value}; new encryption/signing is denied"
        )


def assert_may_decrypt_or_verify_historical(record: KeyRecord) -> None:
    if record.state not in _CAN_DECRYPT_OR_VERIFY_HISTORICAL:
        raise KeyLifecycleError(
            f"key {record.key_id!r} is in state {record.state.value}; historical decrypt/verify is denied"
        )

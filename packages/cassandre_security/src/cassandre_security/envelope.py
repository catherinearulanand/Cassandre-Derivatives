"""Envelope encryption (SEC-08): spec section 8.1/8.2.

Classical profile only this pass (see `COMPONENTS.md`): AES-256-GCM bulk
encryption, a fresh random 256-bit DEK per immutable object version, DEK
wrapped by a customer key-custody boundary. No ML-KEM: the DEK wrap step
uses AES-256-GCM key-wrapping too (a KEK directly encrypts the DEK), which
is a reviewed, standard construction -- not a hand-rolled primitive.

AAD binds schema version, organization, product, legal entity, object
ID/version, data class, and cipher profile, exactly as spec section 8.2
requires: "Never authenticate only the ciphertext while allowing its
ownership labels to change."

Write path: one `seal()` call per new DEK/object version. There is no
"re-encrypt in place" operation exposed here -- callers needing to change
content create a new object version (`reseal_new_version` is just `seal`
with a new `object_version`), per spec: "never re-encrypt changed plaintext
with an old key/nonce."
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from audit_protocol import canonicalize
from cassandre_security.classification import DataClassification

SCHEMA_VERSION = "cassandre.envelope.v1"
CIPHER_PROFILE = "AES-256-GCM"
DEK_BYTES = 32  # 256-bit
NONCE_BYTES = 12  # 96-bit GCM nonce, per spec 8.2


class EnvelopeError(ValueError):
    """Base class for all envelope-format/verification failures. Every
    failure mode below fails closed: callers never receive partial or
    best-guess plaintext."""


class EnvelopeTamperError(EnvelopeError):
    pass


class EnvelopeContextMismatchError(EnvelopeError):
    pass


class EnvelopeFormatError(EnvelopeError):
    pass


@dataclass(frozen=True)
class EnvelopeContext:
    """The fields authenticated as AAD (spec 8.2). Constructing this is the
    caller's job -- every field here must be server-resolved, never taken
    from an unauthenticated client label."""

    organization_id: str
    product_id: str
    legal_entity_id: str | None
    object_id: str
    object_version: str
    data_class: DataClassification

    def canonical_aad(self) -> bytes:
        return canonicalize(
            {
                "schema_version": SCHEMA_VERSION,
                "organization_id": self.organization_id,
                "product_id": self.product_id,
                "legal_entity_id": self.legal_entity_id,
                "object_id": self.object_id,
                "object_version": self.object_version,
                "data_class": self.data_class.value,
                "cipher_profile": CIPHER_PROFILE,
            }
        )


@dataclass(frozen=True)
class SecureEnvelope:
    """The wire/storage format. Every field is required; a parser
    reconstructing this from storage must reject unknown extra fields,
    wrong lengths, and unsupported algorithms (spec 8.2) -- enforced by
    `open_` below re-deriving AAD from an independently supplied
    `EnvelopeContext` rather than trusting any embedded context fields."""

    schema_version: str
    cipher_profile: str
    nonce_b64: str
    ciphertext_b64: str
    wrapped_dek_b64: str
    wrap_nonce_b64: str
    wrap_key_version: str


class KeyCustodyAdapter(Protocol):
    """Scoped cryptographic operations, never generic key export (spec
    section 8/9: "The key service exposes scoped cryptographic operations,
    not generic key export."). A real deployment implements this against a
    customer KMS/HSM; workloads never receive the KEK itself."""

    def current_wrap_key_version(self) -> str: ...

    def wrap_dek(self, dek: bytes, *, key_version: str) -> tuple[bytes, bytes]:
        """Returns (wrapped_dek, wrap_nonce)."""
        ...

    def unwrap_dek(self, wrapped_dek: bytes, *, wrap_nonce: bytes, key_version: str) -> bytes: ...


@dataclass
class LocalSoftwareKeyCustodyAdapter:
    """S1 REFERENCE IMPLEMENTATION ONLY. This holds the KEK in process
    memory -- explicitly NOT HSM-backed, NOT the customer-key-custody
    boundary the spec requires for anything beyond a synthetic workstation
    demonstration (spec section 16's "Synthetic workstation" profile row).
    A real deployment must implement `KeyCustodyAdapter` against a
    customer-controlled KMS/HSM before this touches non-synthetic data.
    """

    _keys_by_version: dict[str, bytes]
    _current_version: str

    @classmethod
    def generate(cls, *, key_version: str = "v1") -> "LocalSoftwareKeyCustodyAdapter":
        return cls(_keys_by_version={key_version: AESGCM.generate_key(bit_length=256)}, _current_version=key_version)

    def current_wrap_key_version(self) -> str:
        return self._current_version

    def wrap_dek(self, dek: bytes, *, key_version: str) -> tuple[bytes, bytes]:
        kek = self._keys_by_version[key_version]
        nonce = os.urandom(NONCE_BYTES)
        wrapped = AESGCM(kek).encrypt(nonce, dek, None)
        return wrapped, nonce

    def unwrap_dek(self, wrapped_dek: bytes, *, wrap_nonce: bytes, key_version: str) -> bytes:
        try:
            kek = self._keys_by_version[key_version]
        except KeyError as exc:
            raise EnvelopeFormatError(f"unknown wrap key version {key_version!r}") from exc
        try:
            return AESGCM(kek).decrypt(wrap_nonce, wrapped_dek, None)
        except InvalidTag as exc:
            raise EnvelopeTamperError("DEK wrap authentication failed") from exc


def seal(plaintext: bytes, *, context: EnvelopeContext, custody: KeyCustodyAdapter) -> SecureEnvelope:
    """Write path (spec 8.2): generate a fresh DEK, encrypt once, wrap the
    DEK, return an immutable envelope. Call this exactly once per new
    object version."""

    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(NONCE_BYTES)
    aad = context.canonical_aad()
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext, aad)

    key_version = custody.current_wrap_key_version()
    wrapped_dek, wrap_nonce = custody.wrap_dek(dek, key_version=key_version)

    return SecureEnvelope(
        schema_version=SCHEMA_VERSION,
        cipher_profile=CIPHER_PROFILE,
        nonce_b64=base64.b64encode(nonce).decode("ascii"),
        ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
        wrapped_dek_b64=base64.b64encode(wrapped_dek).decode("ascii"),
        wrap_nonce_b64=base64.b64encode(wrap_nonce).decode("ascii"),
        wrap_key_version=key_version,
    )


def open_(envelope: SecureEnvelope, *, context: EnvelopeContext, custody: KeyCustodyAdapter) -> bytes:
    """Read path (spec 8.2): authenticate actor/authorize purpose happens
    BEFORE this is called (see `authorization.py`) -- this function only
    performs the cryptographic verification and decrypt. `context` must be
    independently resolved by the caller (from the authorization decision's
    own object metadata), never read out of the envelope itself, or a
    swapped envelope's stale labels could be silently accepted.
    """

    if envelope.schema_version != SCHEMA_VERSION:
        raise EnvelopeFormatError(f"unsupported envelope schema_version {envelope.schema_version!r}")
    if envelope.cipher_profile != CIPHER_PROFILE:
        raise EnvelopeFormatError(f"unsupported cipher_profile {envelope.cipher_profile!r}")

    try:
        nonce = base64.b64decode(envelope.nonce_b64, validate=True)
        ciphertext = base64.b64decode(envelope.ciphertext_b64, validate=True)
        wrapped_dek = base64.b64decode(envelope.wrapped_dek_b64, validate=True)
        wrap_nonce = base64.b64decode(envelope.wrap_nonce_b64, validate=True)
    except Exception as exc:  # noqa: BLE001 - any malformed base64 is a format error
        raise EnvelopeFormatError("malformed base64 field") from exc

    if len(nonce) != NONCE_BYTES:
        raise EnvelopeFormatError(f"nonce must be exactly {NONCE_BYTES} bytes, got {len(nonce)}")
    if len(wrap_nonce) != NONCE_BYTES:
        raise EnvelopeFormatError(f"wrap_nonce must be exactly {NONCE_BYTES} bytes, got {len(wrap_nonce)}")

    dek = custody.unwrap_dek(wrapped_dek, wrap_nonce=wrap_nonce, key_version=envelope.wrap_key_version)
    if len(dek) != DEK_BYTES:
        raise EnvelopeFormatError(f"unwrapped DEK must be exactly {DEK_BYTES} bytes, got {len(dek)}")

    aad = context.canonical_aad()
    try:
        return AESGCM(dek).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        # Covers both a tampered ciphertext/tag AND a context/AAD mismatch
        # (e.g. caller supplied the wrong object_version or tenant) --
        # GCM's authentication does not distinguish these, and this
        # function must not either: both fail closed identically.
        raise EnvelopeTamperError("envelope authentication failed: tampered ciphertext or mismatched context") from exc

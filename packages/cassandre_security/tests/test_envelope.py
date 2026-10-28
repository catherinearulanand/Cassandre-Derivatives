from __future__ import annotations

import base64
import dataclasses

import pytest

from cassandre_security.classification import DataClassification
from cassandre_security.envelope import (
    EnvelopeContext,
    EnvelopeFormatError,
    EnvelopeTamperError,
    LocalSoftwareKeyCustodyAdapter,
    open_,
    seal,
)


def _context(**overrides) -> EnvelopeContext:
    defaults = dict(
        organization_id="SIM-ORG-1",
        product_id="clauseos",
        legal_entity_id="SIM-ENTITY-1",
        object_id="SIM-BUNDLE-1",
        object_version="v1",
        data_class=DataClassification.RESTRICTED,
    )
    defaults.update(overrides)
    return EnvelopeContext(**defaults)


def test_seal_and_open_round_trip():
    custody = LocalSoftwareKeyCustodyAdapter.generate()
    plaintext = b"the threshold is USD 1,000,000.00"
    envelope = seal(plaintext, context=_context(), custody=custody)
    recovered = open_(envelope, context=_context(), custody=custody)
    assert recovered == plaintext


def test_two_seals_of_the_same_plaintext_use_different_nonces_and_ciphertexts():
    custody = LocalSoftwareKeyCustodyAdapter.generate()
    plaintext = b"same content"
    e1 = seal(plaintext, context=_context(object_version="v1"), custody=custody)
    e2 = seal(plaintext, context=_context(object_version="v2"), custody=custody)
    assert e1.nonce_b64 != e2.nonce_b64
    assert e1.ciphertext_b64 != e2.ciphertext_b64
    assert e1.wrapped_dek_b64 != e2.wrapped_dek_b64  # fresh DEK per object version


def test_tampered_ciphertext_is_rejected():
    custody = LocalSoftwareKeyCustodyAdapter.generate()
    envelope = seal(b"secret contents", context=_context(), custody=custody)
    tampered_bytes = bytearray(base64.b64decode(envelope.ciphertext_b64))
    tampered_bytes[0] ^= 0xFF
    tampered = dataclasses.replace(envelope, ciphertext_b64=base64.b64encode(bytes(tampered_bytes)).decode("ascii"))
    with pytest.raises(EnvelopeTamperError):
        open_(tampered, context=_context(), custody=custody)


def test_wrong_context_object_version_is_rejected_as_tamper():
    custody = LocalSoftwareKeyCustodyAdapter.generate()
    envelope = seal(b"secret contents", context=_context(object_version="v1"), custody=custody)
    with pytest.raises(EnvelopeTamperError):
        open_(envelope, context=_context(object_version="v2"), custody=custody)


def test_wrong_context_tenant_and_object_swap_is_rejected():
    """A ciphertext copied into another tenant/product context must fail
    both authorization (elsewhere) AND this context-binding check (spec
    8.2's AAD requirement) -- this test proves the crypto layer alone
    already refuses it."""
    custody = LocalSoftwareKeyCustodyAdapter.generate()
    envelope = seal(b"secret contents", context=_context(organization_id="SIM-ORG-1"), custody=custody)
    with pytest.raises(EnvelopeTamperError):
        open_(envelope, context=_context(organization_id="SIM-ORG-OTHER-TENANT"), custody=custody)


def test_unsupported_cipher_profile_rejected():
    custody = LocalSoftwareKeyCustodyAdapter.generate()
    envelope = seal(b"data", context=_context(), custody=custody)
    corrupted = dataclasses.replace(envelope, cipher_profile="AES-128-CBC")
    with pytest.raises(EnvelopeFormatError):
        open_(corrupted, context=_context(), custody=custody)


def test_unsupported_schema_version_rejected():
    custody = LocalSoftwareKeyCustodyAdapter.generate()
    envelope = seal(b"data", context=_context(), custody=custody)
    corrupted = dataclasses.replace(envelope, schema_version="cassandre.envelope.v0")
    with pytest.raises(EnvelopeFormatError):
        open_(corrupted, context=_context(), custody=custody)


def test_malformed_base64_rejected():
    custody = LocalSoftwareKeyCustodyAdapter.generate()
    envelope = seal(b"data", context=_context(), custody=custody)
    corrupted = dataclasses.replace(envelope, ciphertext_b64="not-valid-base64!!!")
    with pytest.raises(EnvelopeFormatError):
        open_(corrupted, context=_context(), custody=custody)


def test_unknown_wrap_key_version_rejected():
    custody = LocalSoftwareKeyCustodyAdapter.generate(key_version="v1")
    envelope = seal(b"data", context=_context(), custody=custody)
    corrupted = dataclasses.replace(envelope, wrap_key_version="v999-does-not-exist")
    with pytest.raises(EnvelopeFormatError):
        open_(corrupted, context=_context(), custody=custody)


def test_tampered_wrapped_dek_rejected():
    custody = LocalSoftwareKeyCustodyAdapter.generate()
    envelope = seal(b"data", context=_context(), custody=custody)
    tampered_bytes = bytearray(base64.b64decode(envelope.wrapped_dek_b64))
    tampered_bytes[0] ^= 0xFF
    corrupted = dataclasses.replace(envelope, wrapped_dek_b64=base64.b64encode(bytes(tampered_bytes)).decode("ascii"))
    with pytest.raises(EnvelopeTamperError):
        open_(corrupted, context=_context(), custody=custody)


def test_wrong_custody_adapter_cannot_unwrap_another_adapters_dek():
    # Both adapters happen to use the same key_version label ("v1" by
    # default) but hold different actual KEK bytes -- proving that a
    # matching version label alone is not sufficient to unwrap: the wrong
    # KEK fails AEAD authentication on the wrap layer, the same as any
    # other tamper.
    custody_a = LocalSoftwareKeyCustodyAdapter.generate()
    custody_b = LocalSoftwareKeyCustodyAdapter.generate()
    envelope = seal(b"data", context=_context(), custody=custody_a)
    with pytest.raises(EnvelopeTamperError):
        open_(envelope, context=_context(), custody=custody_b)

from __future__ import annotations

import base64

import pytest

from cassandre_security.pqc import (
    MLDSA_65_PUBLIC_KEY_BYTES,
    MLDSA_65_SIGNATURE_BYTES,
    MLKEM_768_CIPHERTEXT_BYTES,
    MLKEM_768_PRIVATE_KEY_BYTES,
    MLKEM_768_PUBLIC_KEY_BYTES,
    MLKEM_768_SHARED_SECRET_BYTES,
    PqcError,
    crypto_capability_report,
    generate_mldsa_keypair,
    generate_mlkem_keypair,
    kem_decapsulate,
    kem_encapsulate,
    mldsa_sign,
    mldsa_verify,
)


def test_mlkem_keypair_sizes_match_fips_203_table():
    keypair = generate_mlkem_keypair("SIM-KEM-1")
    assert len(keypair.public_key) == MLKEM_768_PUBLIC_KEY_BYTES == 1184
    assert len(keypair.private_key) == MLKEM_768_PRIVATE_KEY_BYTES == 2400


def test_mldsa_keypair_public_key_size_matches_fips_204_table():
    keypair = generate_mldsa_keypair("SIM-DSA-1")
    assert len(keypair.public_key) == MLDSA_65_PUBLIC_KEY_BYTES == 1952


def test_kem_encapsulate_decapsulate_round_trip():
    keypair = generate_mlkem_keypair("SIM-KEM-1")
    encapsulation = kem_encapsulate(keypair.public_key)
    assert len(base64.b64decode(encapsulation.ciphertext_b64)) == MLKEM_768_CIPHERTEXT_BYTES
    assert len(encapsulation.shared_secret) == MLKEM_768_SHARED_SECRET_BYTES

    recovered_secret = kem_decapsulate(keypair.private_key, encapsulation.ciphertext_b64)
    assert recovered_secret == encapsulation.shared_secret


def test_kem_decapsulate_with_wrong_private_key_yields_different_secret_not_an_exception():
    """FIPS 203's implicit-rejection property (spec 8.1: preserve it, don't
    expose a separate 'was this valid' oracle): decapsulating with the
    WRONG key never raises -- it deterministically returns a secret that
    will not match, which a caller's downstream AEAD/HMAC check then fails
    closed on."""
    keypair_a = generate_mlkem_keypair("SIM-KEM-A")
    keypair_b = generate_mlkem_keypair("SIM-KEM-B")
    encapsulation = kem_encapsulate(keypair_a.public_key)
    wrong_secret = kem_decapsulate(keypair_b.private_key, encapsulation.ciphertext_b64)
    assert wrong_secret != encapsulation.shared_secret


def test_kem_decapsulate_rejects_malformed_ciphertext_length():
    keypair = generate_mlkem_keypair("SIM-KEM-1")
    with pytest.raises(PqcError):
        kem_decapsulate(keypair.private_key, base64.b64encode(b"too short").decode("ascii"))


def test_kem_decapsulate_rejects_malformed_base64():
    keypair = generate_mlkem_keypair("SIM-KEM-1")
    with pytest.raises(PqcError):
        kem_decapsulate(keypair.private_key, "not valid base64!!!")


def test_mldsa_sign_and_verify_round_trip():
    keypair = generate_mldsa_keypair("SIM-DSA-1")
    message = b"the threshold is USD 1,000,000.00"
    signature = mldsa_sign(keypair, message)
    assert len(base64.b64decode(signature)) == MLDSA_65_SIGNATURE_BYTES
    assert mldsa_verify(keypair.public_key_b64, message, signature) is True


def test_mldsa_verify_fails_for_wrong_public_key():
    keypair_a = generate_mldsa_keypair("SIM-DSA-A")
    keypair_b = generate_mldsa_keypair("SIM-DSA-B")
    signature = mldsa_sign(keypair_a, b"message")
    assert mldsa_verify(keypair_b.public_key_b64, b"message", signature) is False


def test_mldsa_verify_fails_for_tampered_message():
    keypair = generate_mldsa_keypair("SIM-DSA-1")
    signature = mldsa_sign(keypair, b"original message")
    assert mldsa_verify(keypair.public_key_b64, b"tampered message", signature) is False


def test_mldsa_verify_fails_for_tampered_signature():
    keypair = generate_mldsa_keypair("SIM-DSA-1")
    signature_bytes = bytearray(base64.b64decode(mldsa_sign(keypair, b"message")))
    signature_bytes[0] ^= 0xFF
    tampered = base64.b64encode(bytes(signature_bytes)).decode("ascii")
    assert mldsa_verify(keypair.public_key_b64, b"message", tampered) is False


def test_mldsa_domain_separation_prevents_cross_protocol_replay():
    """A raw ML-DSA signature over a bare digest from a DIFFERENT protocol
    (no domain tag) must not verify here -- proves the domain tag is
    actually applied, not just documented."""
    from quantcrypt.dss import MLDSA_65

    keypair = generate_mldsa_keypair("SIM-DSA-1")
    dss = MLDSA_65()
    raw_signature = dss.sign(keypair.private_key, b"message")  # no domain tag
    raw_signature_b64 = base64.b64encode(raw_signature).decode("ascii")
    assert mldsa_verify(keypair.public_key_b64, b"message", raw_signature_b64) is False


def test_malformed_base64_inputs_to_verify_fail_closed_not_raise():
    keypair = generate_mldsa_keypair("SIM-DSA-1")
    assert mldsa_verify(keypair.public_key_b64, b"message", "not base64!!!") is False
    assert mldsa_verify("not base64!!!", b"message", mldsa_sign(keypair, b"message")) is False


def test_crypto_capability_report_reports_expected_algorithms():
    report = crypto_capability_report()
    assert "ML-KEM-768" in report.mlkem_variant
    assert "ML-DSA-65" in report.mldsa_variant
    assert report.openssl_version  # non-empty

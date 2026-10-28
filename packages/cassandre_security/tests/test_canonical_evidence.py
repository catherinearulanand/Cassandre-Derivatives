from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
from audit_protocol.keys import generate_keypair

from cassandre_security.canonical_evidence import (
    REQUIRED_SIGNATURE_ALGORITHMS_V2_HYBRID,
    EvidenceVerificationError,
    build_protected_body,
    seal_evidence,
    verify_evidence,
)
from cassandre_security.pqc import generate_mldsa_keypair

NOW = dt.datetime(2026, 1, 5, 12, 0, 0, tzinfo=dt.timezone.utc)


def _body(**overrides):
    defaults = dict(
        payload={"terms": {"threshold": "1000000.00"}},
        tenant_id="SIM-TENANT-A",
        signer_key_set_id="SIM-KEYSET-1",
        object_id="SIM-BUNDLE-1",
        object_version="v1",
        purpose="MATERIAL_TERM_PUBLICATION",
        policy_epoch=3,
        valid_from=NOW - dt.timedelta(minutes=1),
        valid_until=NOW + dt.timedelta(hours=1),
    )
    defaults.update(overrides)
    return build_protected_body(**defaults)


def test_seal_and_verify_round_trip():
    keypair = generate_keypair("SIM-KEYSET-1")
    body = _body()
    container = seal_evidence(body, ed25519_keypair=keypair)
    verify_evidence(container, trusted_ed25519_public_key_b64=keypair.public_key_b64, now=NOW)


def test_wrong_public_key_fails_verification():
    keypair = generate_keypair("SIM-KEYSET-1")
    other_keypair = generate_keypair("SIM-KEYSET-2")
    container = seal_evidence(_body(), ed25519_keypair=keypair)
    with pytest.raises(EvidenceVerificationError):
        verify_evidence(container, trusted_ed25519_public_key_b64=other_keypair.public_key_b64, now=NOW)


def test_mutated_body_field_invalidates_signature():
    keypair = generate_keypair("SIM-KEYSET-1")
    container = seal_evidence(_body(), ed25519_keypair=keypair)
    mutated_body = dataclasses.replace(container.body, object_version="v2-tampered")
    from cassandre_security.canonical_evidence import EvidenceContainer

    tampered_container = EvidenceContainer(body=mutated_body, signatures=container.signatures)
    with pytest.raises(EvidenceVerificationError):
        verify_evidence(tampered_container, trusted_ed25519_public_key_b64=keypair.public_key_b64, now=NOW)


def test_stripped_signature_fails_closed():
    keypair = generate_keypair("SIM-KEYSET-1")
    container = seal_evidence(_body(), ed25519_keypair=keypair)
    from cassandre_security.canonical_evidence import EvidenceContainer

    stripped = EvidenceContainer(body=container.body, signatures={})
    with pytest.raises(EvidenceVerificationError):
        verify_evidence(stripped, trusted_ed25519_public_key_b64=keypair.public_key_b64, now=NOW)


def test_expired_validity_interval_rejected():
    keypair = generate_keypair("SIM-KEYSET-1")
    body = _body(valid_from=NOW - dt.timedelta(hours=2), valid_until=NOW - dt.timedelta(hours=1))
    container = seal_evidence(body, ed25519_keypair=keypair)
    with pytest.raises(EvidenceVerificationError):
        verify_evidence(container, trusted_ed25519_public_key_b64=keypair.public_key_b64, now=NOW)


def test_not_yet_valid_rejected():
    keypair = generate_keypair("SIM-KEYSET-1")
    body = _body(valid_from=NOW + dt.timedelta(hours=1), valid_until=NOW + dt.timedelta(hours=2))
    container = seal_evidence(body, ed25519_keypair=keypair)
    with pytest.raises(EvidenceVerificationError):
        verify_evidence(container, trusted_ed25519_public_key_b64=keypair.public_key_b64, now=NOW)


def test_local_trusted_policy_can_require_an_algorithm_the_payload_lacks():
    """Proves 'verify required algorithms from local trusted policy, not
    untrusted payload negotiation' (spec 8.4): even though this container
    only ever declares 'ed25519', a verifier configured to require a
    not-yet-implemented 'ml-dsa-65' must reject it rather than silently
    accept whatever the payload happened to bring."""
    keypair = generate_keypair("SIM-KEYSET-1")
    container = seal_evidence(_body(), ed25519_keypair=keypair)
    with pytest.raises(EvidenceVerificationError):
        verify_evidence(
            container,
            trusted_ed25519_public_key_b64=keypair.public_key_b64,
            now=NOW,
            required_algorithms=frozenset({"ed25519", "ml-dsa-65"}),
        )


def test_duplicate_algorithm_in_signature_suite_rejected_at_build_time():
    from cassandre_security.canonical_evidence import EvidenceError

    with pytest.raises(EvidenceError):
        _body(signature_suite=("ed25519", "ed25519"))


def test_signature_map_must_exactly_match_declared_suite():
    keypair = generate_keypair("SIM-KEYSET-1")
    container = seal_evidence(_body(), ed25519_keypair=keypair)
    from cassandre_security.canonical_evidence import EvidenceContainer

    extra_signature = dict(container.signatures)
    extra_signature["unexpected-algo"] = "not-a-real-signature"
    tampered = EvidenceContainer(body=container.body, signatures=extra_signature)
    with pytest.raises(EvidenceVerificationError):
        verify_evidence(tampered, trusted_ed25519_public_key_b64=keypair.public_key_b64, now=NOW)


def test_verify_evidence_with_payload_catches_substituted_payload():
    """Proves the fix for the gap CollateralOS's integration found: a
    signature-valid container whose body was signed over a DIFFERENT
    payload than the one the caller now holds must be rejected when
    `payload` is supplied, not silently accepted just because the
    signature itself checks out."""
    keypair = generate_keypair("SIM-KEYSET-1")
    original_payload = {"terms": {"threshold": "1000000.00"}}
    container = seal_evidence(_body(payload=original_payload), ed25519_keypair=keypair)

    verify_evidence(container, trusted_ed25519_public_key_b64=keypair.public_key_b64, now=NOW, payload=original_payload)

    substituted_payload = {"terms": {"threshold": "1.00"}}
    with pytest.raises(EvidenceVerificationError):
        verify_evidence(
            container,
            trusted_ed25519_public_key_b64=keypair.public_key_b64,
            now=NOW,
            payload=substituted_payload,
        )


def test_verify_evidence_without_payload_still_verifies_signature_only():
    """Omitting `payload` is a valid, documented mode (signature-only
    verification) -- it must not spuriously fail."""
    keypair = generate_keypair("SIM-KEYSET-1")
    container = seal_evidence(_body(), ed25519_keypair=keypair)
    verify_evidence(container, trusted_ed25519_public_key_b64=keypair.public_key_b64, now=NOW)


def test_hybrid_dual_signature_seal_and_verify_round_trip():
    """Spec 8.4: 'requires BOTH independent signatures on the same
    canonical signed body.' This is the new, opt-in strict profile --
    fully additive, the classical-only default path is untouched."""
    ed_keypair = generate_keypair("SIM-KEYSET-1")
    ml_keypair = generate_mldsa_keypair("SIM-KEYSET-1-MLDSA")
    body = _body(signature_suite=("ed25519", "ml-dsa-65"))
    container = seal_evidence(body, ed25519_keypair=ed_keypair, ml_dsa_keypair=ml_keypair)

    assert set(container.signatures.keys()) == {"ed25519", "ml-dsa-65"}
    verify_evidence(
        container,
        trusted_ed25519_public_key_b64=ed_keypair.public_key_b64,
        trusted_ml_dsa_public_key_b64=ml_keypair.public_key_b64,
        now=NOW,
        required_algorithms=REQUIRED_SIGNATURE_ALGORITHMS_V2_HYBRID,
    )


def test_hybrid_profile_rejects_stripped_ml_dsa_signature():
    """Spec 8.4: an OR policy (accept either signature alone) 'preserves
    an attacker's ability to use the weaker accepted branch' -- proves
    stripping just the ML-DSA signature and keeping a valid ed25519 one
    still fails when the hybrid profile is required."""
    ed_keypair = generate_keypair("SIM-KEYSET-1")
    ml_keypair = generate_mldsa_keypair("SIM-KEYSET-1-MLDSA")
    body = _body(signature_suite=("ed25519", "ml-dsa-65"))
    container = seal_evidence(body, ed25519_keypair=ed_keypair, ml_dsa_keypair=ml_keypair)

    from cassandre_security.canonical_evidence import EvidenceContainer

    stripped = EvidenceContainer(body=container.body, signatures={"ed25519": container.signatures["ed25519"]})
    with pytest.raises(EvidenceVerificationError):
        verify_evidence(
            stripped,
            trusted_ed25519_public_key_b64=ed_keypair.public_key_b64,
            trusted_ml_dsa_public_key_b64=ml_keypair.public_key_b64,
            now=NOW,
            required_algorithms=REQUIRED_SIGNATURE_ALGORITHMS_V2_HYBRID,
        )


def test_hybrid_profile_rejects_wrong_ml_dsa_key():
    ed_keypair = generate_keypair("SIM-KEYSET-1")
    ml_keypair = generate_mldsa_keypair("SIM-KEYSET-1-MLDSA")
    other_ml_keypair = generate_mldsa_keypair("SIM-KEYSET-2-MLDSA")
    body = _body(signature_suite=("ed25519", "ml-dsa-65"))
    container = seal_evidence(body, ed25519_keypair=ed_keypair, ml_dsa_keypair=ml_keypair)

    with pytest.raises(EvidenceVerificationError):
        verify_evidence(
            container,
            trusted_ed25519_public_key_b64=ed_keypair.public_key_b64,
            trusted_ml_dsa_public_key_b64=other_ml_keypair.public_key_b64,
            now=NOW,
            required_algorithms=REQUIRED_SIGNATURE_ALGORITHMS_V2_HYBRID,
        )


def test_existing_classical_only_default_is_unaffected_by_pqc_addition():
    """Backward-compatibility proof: the exact call pattern every existing
    product integration uses (no ml_dsa_keypair, default required_algorithms)
    still behaves identically to before ML-DSA was added."""
    keypair = generate_keypair("SIM-KEYSET-1")
    container = seal_evidence(_body(), ed25519_keypair=keypair)
    assert set(container.signatures.keys()) == {"ed25519"}
    verify_evidence(container, trusted_ed25519_public_key_b64=keypair.public_key_b64, now=NOW)


def test_seal_evidence_rejects_mismatched_keypair_and_declared_suite():
    from cassandre_security.canonical_evidence import EvidenceError

    ed_keypair = generate_keypair("SIM-KEYSET-1")
    ml_keypair = generate_mldsa_keypair("SIM-KEYSET-1-MLDSA")
    # Declares ml-dsa-65 in the suite but caller forgot to pass the keypair.
    body = _body(signature_suite=("ed25519", "ml-dsa-65"))
    with pytest.raises(EvidenceError):
        seal_evidence(body, ed25519_keypair=ed_keypair)
    # Passes an ml_dsa_keypair but the body doesn't declare it.
    with pytest.raises(EvidenceError):
        seal_evidence(_body(), ed25519_keypair=ed_keypair, ml_dsa_keypair=ml_keypair)


def test_v1_and_v2_profiles_use_different_domain_tags_and_digest_algorithms():
    """Direct proof that this is a genuinely new profile, not the old V1
    format wearing a new schema_version string."""
    from audit_protocol.keys import DOMAIN_TAG as V1_DOMAIN_TAG

    from cassandre_security.canonical_evidence import DIGEST_ALGORITHM_DEFAULT, DOMAIN_TAG_V2

    assert DOMAIN_TAG_V2 != V1_DOMAIN_TAG
    assert DIGEST_ALGORITHM_DEFAULT == "sha384"

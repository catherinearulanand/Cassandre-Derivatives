"""Signed export envelope tests (plan Section 8.1; ADR-012's "validated
internal record only, never transmitted" boundary; work item COL-013).

Mirrors `packages/cassandre_contracts/tests/test_envelope.py`'s pattern:
build+sign an envelope, verify it independently with a `TrustBundle` built
only from the public key (never the store's private key material), and
prove that tampering with the payload after signing is caught by
`verify_envelope` via `CONTENT_HASH_MISMATCH` -- the signature alone does
not save a mutated envelope, because verification recomputes the content
hash from the (possibly tampered) fields and compares it to the stored one
before it even looks at the signature bytes.
"""

from __future__ import annotations

from cassandre_contracts import ProducerProduct, TrustBundle, verify_envelope

from fixtures.inventory_corpus import AS_OF
from fixtures.store_scenario import build_reserved_plan_scenario

MAKER_ID = "SIM-USER-MAKER-0001"
CHECKER_ID = "SIM-USER-CHECKER-0001"
OPS_ID = "SIM-USER-OPS-0001"


def _exported_envelope(seq: str):
    scenario = build_reserved_plan_scenario(seq=seq)
    store = scenario.store
    store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)
    store.checker_approve(scenario.reservation_id, checker_id=CHECKER_ID, actor_id=CHECKER_ID, now=AS_OF)
    envelope = store.export_internal(
        scenario.instruction_id,
        idempotency_key=f"SIM-IDEMP-{seq}",
        request_fingerprint="fingerprint-a",
        actor_id=OPS_ID,
        now=AS_OF,
    )
    return store, envelope


def test_export_envelope_is_a_signed_collateralos_envelope_independently_verifiable():
    store, envelope = _exported_envelope("0601")

    assert envelope.producer_product is ProducerProduct.COLLATERALOS
    assert envelope.signature is not None
    assert envelope.payload["transmitted"] is False  # ADR-012: never transmitted anywhere
    assert envelope.payload["instruction_id"] == "SIM-INSTR-0601"

    # Independent verification: a TrustBundle built only from the public
    # key half of the store's keypair -- no access to private key material.
    trust_bundle = TrustBundle(keys_by_id={store.keypair.key_id: store.keypair.public_key_b64})
    result = verify_envelope(envelope, trust_bundle=trust_bundle, expected_audience="cassandre")

    assert result.ok, result.failures


def test_tampered_export_payload_is_rejected_with_content_hash_mismatch():
    store, envelope = _exported_envelope("0602")
    trust_bundle = TrustBundle(keys_by_id={store.keypair.key_id: store.keypair.public_key_b64})

    # Tamper with the signed payload after the fact -- e.g. attempting to
    # inflate the exported instruction's recorded delivered quantity.
    tampered_lines = list(envelope.payload["lines"])
    tampered_lines[0] = {
        **tampered_lines[0],
        "delivered_quantity": {
            **tampered_lines[0]["delivered_quantity"],
            "amount": "999999999",
        },
    }
    tampered = envelope.model_copy(update={"payload": {**envelope.payload, "lines": tampered_lines}})

    result = verify_envelope(tampered, trust_bundle=trust_bundle, expected_audience="cassandre")

    assert not result.ok
    assert "CONTENT_HASH_MISMATCH" in result.failures


def test_tampered_export_envelope_fails_even_with_a_stale_signature_copy():
    """The signature is over the content_hash, not the live fields -- so a
    tampered envelope that keeps the *original* signature is still caught
    purely by the content-hash recomputation, independent of signature
    validity, confirming the two checks are not redundant with each other."""
    store, envelope = _exported_envelope("0603")
    trust_bundle = TrustBundle(keys_by_id={store.keypair.key_id: store.keypair.public_key_b64})

    tampered = envelope.model_copy(update={"object_version": "tampered-version"})

    result = verify_envelope(tampered, trust_bundle=trust_bundle, expected_audience="cassandre")

    assert not result.ok
    assert "CONTENT_HASH_MISMATCH" in result.failures

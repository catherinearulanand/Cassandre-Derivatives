import pytest

from cassandre_contracts import ProducerProduct, TrustBundle

from cassandre_control_api.domain import (
    RejectedEnvelopeError,
    assemble_federated_audit_export,
    correlation_key_for,
    record_envelope,
)


def test_record_envelope_succeeds_with_valid_signature(signed_envelope_factory):
    env, kp = signed_envelope_factory(ProducerProduct.CLAUSEOS, "SIM-SCEN-0001", "obj-1", "clauseos-key-1")
    bundle = TrustBundle(keys_by_id={kp.key_id: kp.public_key_b64})
    receipt = record_envelope(env, trust_bundle=bundle)
    assert receipt.verification.ok
    assert receipt.envelope.object_id == "obj-1"


def test_record_envelope_rejects_unknown_key(signed_envelope_factory):
    env, kp = signed_envelope_factory(ProducerProduct.CLAUSEOS, "SIM-SCEN-0001", "obj-1", "clauseos-key-1")
    empty_bundle = TrustBundle(keys_by_id={})
    with pytest.raises(RejectedEnvelopeError):
        record_envelope(env, trust_bundle=empty_bundle)


def test_correlation_key_is_scenario_id(signed_envelope_factory):
    env, _ = signed_envelope_factory(ProducerProduct.RISKOS, "SIM-SCEN-0042", "obj-2", "riskos-key-1")
    assert correlation_key_for(env) == "SIM-SCEN-0042"


def test_correlated_case_completeness(signed_envelope_factory):
    from cassandre_control_api.domain import CorrelatedCase

    case = CorrelatedCase(correlation_key="SIM-SCEN-0007")
    assert not case.is_cross_product_complete

    for product, key_id in [
        (ProducerProduct.CLAUSEOS, "clauseos-key-1"),
        (ProducerProduct.RISKOS, "riskos-key-1"),
        (ProducerProduct.COLLATERALOS, "collateralos-key-1"),
    ]:
        env, kp = signed_envelope_factory(product, "SIM-SCEN-0007", f"obj-{product.value}", key_id)
        bundle = TrustBundle(keys_by_id={kp.key_id: kp.public_key_b64})
        receipt = record_envelope(env, trust_bundle=bundle)
        case.add(receipt)

    assert case.is_cross_product_complete


def test_federated_audit_export_traces_back_to_verified_receipts(signed_envelope_factory):
    from cassandre_control_api.domain import CorrelatedCase

    case = CorrelatedCase(correlation_key="SIM-SCEN-0099")
    env, kp = signed_envelope_factory(ProducerProduct.COLLATERALOS, "SIM-SCEN-0099", "obj-x", "col-key-1")
    bundle = TrustBundle(keys_by_id={kp.key_id: kp.public_key_b64})
    receipt = record_envelope(env, trust_bundle=bundle)
    case.add(receipt)

    export = assemble_federated_audit_export(case)
    assert export.correlation_key == "SIM-SCEN-0099"
    assert len(export.receipts) == 1
    assert export.receipts[0].envelope.object_id == "obj-x"

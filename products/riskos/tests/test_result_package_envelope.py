"""Independent verification of RiskOS's signed `ResultPackage` envelope,
mirroring `packages/cassandre_contracts/tests/test_envelope.py`'s pattern.
Required test (item 7): sign, independently verify with
`cassandre_contracts.verify_envelope`, and reject a tampered payload."""

from __future__ import annotations

import datetime as dt

from audit_protocol.keys import generate_keypair
from cassandre_contracts import TrustBundle, verify_envelope

import riskos_fixtures as fx
from riskos.application.envelope import build_signed_result_envelope
from riskos.application.result_package import build_result_package
from riskos.core.workflow import MarginCallRequest, execute_margin_call
from riskos.domain.enums import CalculationPurpose


def _run_golden_scenario():
    scenario = fx.build_golden_scenario()
    request = MarginCallRequest(
        run_id="SIM-RUN-ENVELOPE-0001",
        purpose=CalculationPurpose.MARGIN_CALL,
        calculation_entity=scenario.calculation_entity,
        counterparty_entity=scenario.counterparty_entity,
        netting_set_candidates=(scenario.netting_set,),
        agreement_terms=scenario.agreement_terms,
        portfolio=scenario.portfolio,
        manifest=scenario.manifest,
        collateral=scenario.collateral,
        method_pack=scenario.method_pack,
        counterparty_call=scenario.counterparty_call,
        now=scenario.now,
    )
    return execute_margin_call(request), scenario


def test_signed_result_package_envelope_verifies_successfully():
    result, scenario = _run_golden_scenario()
    result_package = build_result_package(
        result_id="SIM-RESULT-0001",
        run=result.run,
        method_pack=scenario.method_pack,
        reconciliation=result.reconciliation,
        now=scenario.now,
    )
    keypair = generate_keypair("riskos-test-key-1")
    envelope = build_signed_result_envelope(result_package, keypair=keypair, now=scenario.now)

    assert envelope.simulation_provenance.is_simulated is True
    assert envelope.simulation_provenance.scenario_id == "SIM-SCEN-RISKOS-0001"

    trust_bundle = TrustBundle(keys_by_id={keypair.key_id: keypair.public_key_b64})
    verification = verify_envelope(envelope, trust_bundle=trust_bundle, expected_audience="cassandre")
    assert verification.ok, verification.failures


def test_tampered_result_package_payload_is_rejected():
    result, scenario = _run_golden_scenario()
    result_package = build_result_package(
        result_id="SIM-RESULT-0002",
        run=result.run,
        method_pack=scenario.method_pack,
        reconciliation=result.reconciliation,
        now=scenario.now,
    )
    keypair = generate_keypair("riskos-test-key-2")
    envelope = build_signed_result_envelope(result_package, keypair=keypair, now=scenario.now)

    tampered_payload = dict(envelope.payload)
    tampered_payload["waterfall"] = dict(tampered_payload["waterfall"] or {})
    if tampered_payload["waterfall"]:
        tampered_payload["waterfall"]["call_amount"] = {
            "amount": "999999999.00",
            "currency": "USD",
            "rounding_context": "tampered",
        }
    tampered_envelope = envelope.model_copy(update={"payload": tampered_payload})

    trust_bundle = TrustBundle(keys_by_id={keypair.key_id: keypair.public_key_b64})
    verification = verify_envelope(tampered_envelope, trust_bundle=trust_bundle, expected_audience="cassandre")
    assert not verification.ok
    assert "CONTENT_HASH_MISMATCH" in verification.failures


def test_unsigned_envelope_is_rejected():
    result, scenario = _run_golden_scenario()
    result_package = build_result_package(
        result_id="SIM-RESULT-0003",
        run=result.run,
        method_pack=scenario.method_pack,
        reconciliation=result.reconciliation,
        now=scenario.now,
    )
    keypair = generate_keypair("riskos-test-key-3")
    envelope = build_signed_result_envelope(result_package, keypair=keypair, now=scenario.now)
    unsigned = envelope.model_copy(update={"signature": None})

    trust_bundle = TrustBundle(keys_by_id={keypair.key_id: keypair.public_key_b64})
    verification = verify_envelope(unsigned, trust_bundle=trust_bundle, expected_audience="cassandre")
    assert not verification.ok
    assert "MISSING_SIGNATURE" in verification.failures

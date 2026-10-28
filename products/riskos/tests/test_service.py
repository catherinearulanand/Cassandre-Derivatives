"""Application-service tests: wiring the pure workflow to the in-memory
repository, the audit chain, and envelope export. No DB dependency."""

from __future__ import annotations

from audit_protocol import verify_chain
from audit_protocol.keys import generate_keypair
from cassandre_contracts import TrustBundle, verify_envelope

import riskos_fixtures as fx
from riskos.adapters.in_memory import InMemoryRiskOSRepository
from riskos.application.service import RiskOSNotFoundError, RiskOSService
from riskos.domain.enums import (
    Actionability,
    CalculationPurpose,
    CalculationStatus,
    ReconciliationStatus,
)


def _build_service() -> RiskOSService:
    repo = InMemoryRiskOSRepository()
    keypair = generate_keypair("riskos-service-test-key")
    return RiskOSService(repo, keypair=keypair)


def test_full_service_workflow_produces_complete_matched_run_and_verifiable_audit_chain():
    service = _build_service()
    scenario = fx.build_golden_scenario()
    now = scenario.now

    service.register_legal_entity(scenario.calculation_entity, now=now)
    service.register_legal_entity(scenario.counterparty_entity, now=now)
    service.register_netting_set(scenario.netting_set, now=now)

    ok, reasons = service.validate_agreement_terms(scenario.agreement_terms)
    assert ok, reasons
    approved_terms = service.approve_agreement_terms(scenario.agreement_terms, now=now)
    assert approved_terms.approved

    service.ingest_portfolio_snapshot(scenario.portfolio, now=now)
    service.ingest_collateral_state(scenario.collateral, now=now)

    run = service.run_calculation(
        run_id="SIM-RUN-SERVICE-0001",
        purpose=CalculationPurpose.MARGIN_CALL,
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        agreement_id=scenario.agreement_terms.agreement_id,
        portfolio_snapshot_id=scenario.portfolio.snapshot_id,
        collateral_package_id=scenario.collateral.package_id,
        manifest=scenario.manifest,
        counterparty_call=scenario.counterparty_call,
        now=now,
    )

    assert run.calculation_status is CalculationStatus.COMPLETE
    assert run.reconciliation_status is ReconciliationStatus.MATCHED
    assert run.actionability is Actionability.SIMULATION_ELIGIBLE

    fetched_run = service.get_calculation_run(run.run_id)
    assert fetched_run.run_id == run.run_id

    events = service._repo.list_audit_events()  # test-only introspection
    assert len(events) >= 1
    chain_result = verify_chain(list(events))
    assert chain_result.ok, chain_result.reason

    envelope = service.export_result_package(run_id=run.run_id, now=now)
    trust_bundle = TrustBundle(keys_by_id={service._keypair.key_id: service._keypair.public_key_b64})
    verification = verify_envelope(envelope, trust_bundle=trust_bundle, expected_audience="cassandre")
    assert verification.ok, verification.failures


def test_get_calculation_run_raises_for_unknown_run_id():
    service = _build_service()
    try:
        service.get_calculation_run("SIM-RUN-DOES-NOT-EXIST")
        assert False, "expected RiskOSNotFoundError"
    except RiskOSNotFoundError:
        pass


def test_submit_reconciliation_recomputes_against_a_new_counterparty_call():
    service = _build_service()
    scenario = fx.build_golden_scenario()
    now = scenario.now
    service.register_legal_entity(scenario.calculation_entity, now=now)
    service.register_legal_entity(scenario.counterparty_entity, now=now)
    service.register_netting_set(scenario.netting_set, now=now)
    service.approve_agreement_terms(scenario.agreement_terms, now=now)
    service.ingest_portfolio_snapshot(scenario.portfolio, now=now)
    service.ingest_collateral_state(scenario.collateral, now=now)
    run = service.run_calculation(
        run_id="SIM-RUN-SERVICE-0002",
        purpose=CalculationPurpose.MARGIN_CALL,
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        agreement_id=scenario.agreement_terms.agreement_id,
        portfolio_snapshot_id=scenario.portfolio.snapshot_id,
        collateral_package_id=scenario.collateral.package_id,
        manifest=scenario.manifest,
        counterparty_call=scenario.counterparty_call,
        now=now,
    )
    assert run.reconciliation_status is ReconciliationStatus.MATCHED

    disagreeing_call = fx.build_matching_counterparty_call()
    import dataclasses

    disagreeing_call = dataclasses.replace(disagreeing_call, call_amount=fx.pack_money("1.00"))
    case = service.submit_reconciliation(run_id=run.run_id, counterparty_call=disagreeing_call, now=now)
    assert case.reconciliation_status is ReconciliationStatus.BREAK

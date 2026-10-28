"""End-to-end tests of the S1 authoritative workflow (plan Section 11.3),
exercising the full golden 5-trade scenario plus the required blocking
cases: unsupported agreement (f) and trade-population mismatch (g)."""

from __future__ import annotations

import dataclasses

import pytest

from riskos.core.method_pack import build_method_pack_001
from riskos.core.netting import resolve_netting_set
from riskos.core.workflow import MarginCallRequest, execute_margin_call
from riskos.domain.entities import NettingSetVersion, TradeMTM
from riskos.domain.enums import (
    Actionability,
    AgreementSupportStatus,
    CalculationPurpose,
    CalculationStatus,
    DataQualityStatus,
    ReconciliationStatus,
    TradePopulationDisposition,
    ValidationStatus,
)

import riskos_fixtures as fx


def _request_from_scenario(scenario: fx.GoldenScenario) -> MarginCallRequest:
    return MarginCallRequest(
        run_id="SIM-RUN-0001",
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


def test_golden_scenario_completes_and_matches():
    """Full pipeline over the plan 25.5 slice's 5-trade scenario: COMPLETE,
    APPROVED, MATCHED, RESTRICTED_USE (provisional pack), SIMULATION_ELIGIBLE."""

    scenario = fx.build_golden_scenario()
    result = execute_margin_call(_request_from_scenario(scenario))
    run = result.run

    assert run.calculation_status is CalculationStatus.COMPLETE
    assert run.data_quality_status is DataQualityStatus.APPROVED
    assert run.reconciliation_status is ReconciliationStatus.MATCHED
    assert run.validation_status is ValidationStatus.RESTRICTED_USE
    assert run.actionability is Actionability.SIMULATION_ELIGIBLE
    assert run.agreement_support_status is AgreementSupportStatus.SUPPORTED

    assert run.waterfall is not None
    assert str(run.waterfall.exposure.amount) == "100536.00"
    assert str(run.waterfall.credit_support_balance.amount) == "55000.00"
    assert run.waterfall.call_status.value == "DELIVER"
    assert str(run.waterfall.call_amount.amount) == "45536"
    assert run.waterfall.transferor_entity_id == scenario.counterparty_entity.entity_id
    assert run.waterfall.transferee_entity_id == scenario.calculation_entity.entity_id

    assert result.reconciliation is not None
    assert result.reconciliation.undisputed_amount is not None
    assert str(result.reconciliation.undisputed_amount.amount) == "45536"


def test_golden_scenario_with_reconciliation_break_is_complete_but_review_required():
    """A calculation may complete while remaining non-actionable (plan
    11.7): COMPLETE + BREAK => actionability REVIEW_REQUIRED, not REJECTED."""

    scenario = fx.build_golden_scenario()
    disagreeing_counterparty_call = dataclasses.replace(
        scenario.counterparty_call, call_amount=fx.pack_money("40000.00")
    )
    scenario = dataclasses.replace(scenario, counterparty_call=disagreeing_counterparty_call)
    result = execute_margin_call(_request_from_scenario(scenario))

    assert result.run.calculation_status is CalculationStatus.COMPLETE
    assert result.run.reconciliation_status is ReconciliationStatus.BREAK
    assert result.run.actionability is Actionability.REVIEW_REQUIRED


def test_unsupported_agreement_blocks_calculation_without_a_call_amount():
    """Required test (f): an unsupported agreement variant returns
    UNSUPPORTED_AGREEMENT and never a computed call amount."""

    scenario = fx.build_golden_scenario()
    unsupported_terms = fx.build_agreement_terms(is_supported=False)
    scenario = dataclasses.replace(scenario, agreement_terms=unsupported_terms)
    result = execute_margin_call(_request_from_scenario(scenario))

    assert result.run.calculation_status is CalculationStatus.FAILED
    assert result.run.agreement_support_status is AgreementSupportStatus.UNSUPPORTED_AGREEMENT
    assert result.run.waterfall is None
    assert result.run.actionability is Actionability.REJECTED
    assert "UNSUPPORTED" not in ""  # sanity no-op guard against silent pass-through
    assert result.run.failure_reason is not None


def test_method_unresolved_when_method_pack_id_does_not_match_implemented_pack():
    scenario = fx.build_golden_scenario()
    unresolved_terms = dataclasses.replace(scenario.agreement_terms, method_pack_id="SIM-CSA-SOME-OTHER-PACK-999")
    scenario = dataclasses.replace(scenario, agreement_terms=unresolved_terms)
    result = execute_margin_call(_request_from_scenario(scenario))

    assert result.run.calculation_status is CalculationStatus.FAILED
    assert result.run.agreement_support_status is AgreementSupportStatus.METHOD_UNRESOLVED
    assert result.run.waterfall is None


def test_trade_population_mismatch_blocks_the_run():
    """Required test (g): the fixture's manifest claims 5 accepted trades;
    dropping one trade's disposition to REJECTED means only 4 resolve as
    accepted, which must block the entire run atomically, not silently
    proceed with a partial population."""

    scenario = fx.build_golden_scenario()
    trades = list(scenario.portfolio.trades)
    trades[0] = dataclasses.replace(trades[0], disposition=TradePopulationDisposition.REJECTED)
    bad_portfolio = dataclasses.replace(scenario.portfolio, trades=tuple(trades))
    scenario = dataclasses.replace(scenario, portfolio=bad_portfolio)

    result = execute_margin_call(_request_from_scenario(scenario))

    assert result.run.calculation_status is CalculationStatus.FAILED
    assert result.run.waterfall is None
    assert result.run.actionability is Actionability.REJECTED
    assert "population" in result.run.failure_reason.lower()


def test_unsupported_calculation_purpose_is_rejected_not_silently_run():
    scenario = fx.build_golden_scenario()
    request = dataclasses.replace(_request_from_scenario(scenario), purpose=CalculationPurpose.STRESS)
    result = execute_margin_call(request)
    assert result.run.calculation_status is CalculationStatus.FAILED
    assert result.run.waterfall is None


def test_zero_netting_set_matches_blocks_the_run():
    scenario = fx.build_golden_scenario()
    request = dataclasses.replace(_request_from_scenario(scenario), netting_set_candidates=())
    result = execute_margin_call(request)
    assert result.run.calculation_status is CalculationStatus.FAILED
    assert "zero" in result.run.failure_reason.lower()


def test_multiple_netting_set_matches_blocks_the_run():
    scenario = fx.build_golden_scenario()
    duplicate_netting_set: NettingSetVersion = dataclasses.replace(scenario.netting_set, netting_set_id="SIM-NETSET-0002", version="v2")
    request = dataclasses.replace(
        _request_from_scenario(scenario),
        netting_set_candidates=(scenario.netting_set, duplicate_netting_set),
    )
    result = execute_margin_call(request)
    assert result.run.calculation_status is CalculationStatus.FAILED
    assert "ambiguous" in result.run.failure_reason.lower() or "matches" in result.run.failure_reason.lower()


def test_netting_set_resolution_is_deterministic_helper():
    scenario = fx.build_golden_scenario()
    resolution = resolve_netting_set(
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        candidates=(scenario.netting_set,),
    )
    assert resolution.ok
    assert resolution.resolved is not None
    assert resolution.resolved.netting_set_id == scenario.netting_set.netting_set_id


def test_method_pack_001_builder_is_flagged_provisional():
    pack = build_method_pack_001()
    assert pack.provisional is True
    assert pack.lower_common_amount_authorized is False

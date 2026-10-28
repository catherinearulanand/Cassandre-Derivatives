"""Unit tests for the explicit authority/completeness/freshness/currency/
duplicate/support-matrix gates (plan Section 11.3 step 6)."""

from __future__ import annotations

import dataclasses
import datetime as dt

import riskos_fixtures as fx
from riskos.core.gates import (
    run_authority_gate,
    run_completeness_gate,
    run_currency_gate,
    run_duplicate_gate,
    run_freshness_gate,
    run_support_matrix_gate,
)
from riskos.domain.enums import CalculationPurpose, TradePopulationDisposition


def test_authority_gate_fails_when_netting_set_not_approved():
    scenario = fx.build_golden_scenario()
    unapproved = dataclasses.replace(scenario.netting_set, approved=False)
    result = run_authority_gate(
        calculation_entity=scenario.calculation_entity,
        counterparty_entity=scenario.counterparty_entity,
        netting_set=unapproved,
        agreement_terms=scenario.agreement_terms,
    )
    assert not result.ok
    assert any("netting set" in reason.lower() for reason in result.failed_gates)


def test_authority_gate_passes_for_golden_scenario():
    scenario = fx.build_golden_scenario()
    result = run_authority_gate(
        calculation_entity=scenario.calculation_entity,
        counterparty_entity=scenario.counterparty_entity,
        netting_set=scenario.netting_set,
        agreement_terms=scenario.agreement_terms,
    )
    assert result.ok


def test_completeness_gate_fails_on_empty_portfolio():
    scenario = fx.build_golden_scenario()
    empty_portfolio = dataclasses.replace(scenario.portfolio, trades=())
    result = run_completeness_gate(portfolio=empty_portfolio, collateral=scenario.collateral)
    assert not result.ok


def test_freshness_gate_fails_when_snapshot_older_than_window():
    scenario = fx.build_golden_scenario()
    stale_portfolio = dataclasses.replace(scenario.portfolio, as_of=scenario.now - dt.timedelta(hours=48))
    result = run_freshness_gate(portfolio=stale_portfolio, collateral=scenario.collateral, now=scenario.now)
    assert not result.ok
    assert any("FRESHNESS" in reason for reason in result.failed_gates)


def test_freshness_gate_passes_within_window():
    scenario = fx.build_golden_scenario()
    result = run_freshness_gate(portfolio=scenario.portfolio, collateral=scenario.collateral, now=scenario.now)
    assert result.ok


def test_currency_gate_fails_on_call_currency_mismatch():
    scenario = fx.build_golden_scenario()
    mismatched_terms = dataclasses.replace(scenario.agreement_terms, call_currency="EUR")
    result = run_currency_gate(agreement_terms=mismatched_terms, method_pack=scenario.method_pack)
    assert not result.ok


def test_duplicate_gate_fails_on_repeated_accepted_trade_id():
    scenario = fx.build_golden_scenario()
    trades = list(scenario.portfolio.trades)
    trades.append(dataclasses.replace(trades[0]))
    dup_portfolio = dataclasses.replace(scenario.portfolio, trades=tuple(trades))
    result = run_duplicate_gate(portfolio=dup_portfolio)
    assert not result.ok


def test_support_matrix_gate_fails_for_unsupported_purpose():
    scenario = fx.build_golden_scenario()
    result = run_support_matrix_gate(
        purpose=CalculationPurpose.STRESS,
        agreement_terms=scenario.agreement_terms,
        method_pack=scenario.method_pack,
    )
    assert not result.ok


def test_support_matrix_gate_passes_for_margin_call_with_supported_terms():
    scenario = fx.build_golden_scenario()
    result = run_support_matrix_gate(
        purpose=CalculationPurpose.MARGIN_CALL,
        agreement_terms=scenario.agreement_terms,
        method_pack=scenario.method_pack,
    )
    assert result.ok

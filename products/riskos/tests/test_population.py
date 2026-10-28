"""Unit tests for trade-population reconciliation against a source-control
manifest (plan Section 9.3/11.3)."""

from __future__ import annotations

import dataclasses

import riskos_fixtures as fx
from riskos.core.population import reconcile_population
from riskos.domain.enums import TradePopulationDisposition


def test_population_reconciles_when_counts_and_mtm_total_match():
    portfolio = fx.build_portfolio_snapshot()
    manifest = fx.build_manifest()
    result = reconcile_population(portfolio, manifest)
    assert result.ok
    assert result.mismatches == ()
    assert result.resolved_counts[TradePopulationDisposition.ACCEPTED] == 5
    assert str(result.resolved_accepted_mtm_total.amount) == "100536.00"


def test_population_blocks_on_accepted_count_mismatch():
    trades = list(fx.build_five_trades())
    trades[0] = dataclasses.replace(trades[0], disposition=TradePopulationDisposition.REJECTED)
    portfolio = fx.build_portfolio_snapshot(tuple(trades))
    manifest = fx.build_manifest()  # still claims 5 accepted
    result = reconcile_population(portfolio, manifest)
    assert not result.ok
    assert any("ACCEPTED" in m for m in result.mismatches)


def test_population_blocks_on_mtm_total_mismatch():
    trades = list(fx.build_five_trades())
    trades[0] = dataclasses.replace(trades[0], mtm=fx.mtm_money("999999.99"))
    portfolio = fx.build_portfolio_snapshot(tuple(trades))
    manifest = fx.build_manifest()  # manifest total unchanged, no longer matches
    result = reconcile_population(portfolio, manifest)
    assert not result.ok
    assert any("MTM total" in m for m in result.mismatches)


def test_population_ok_with_mixed_dispositions_matching_manifest():
    trades = list(fx.build_five_trades())
    trades[4] = dataclasses.replace(trades[4], disposition=TradePopulationDisposition.MATURED)
    portfolio = fx.build_portfolio_snapshot(tuple(trades))
    # Remaining accepted total: 125345.67 - 40200.10 + 10000.00 - 2500.00 = 92645.57
    manifest = fx.build_manifest(
        expected_accepted_count=4, expected_accepted_mtm_total="92645.57", expected_matured_count=1
    )
    result = reconcile_population(portfolio, manifest)
    assert result.ok, result.mismatches
    assert result.resolved_counts[TradePopulationDisposition.MATURED] == 1

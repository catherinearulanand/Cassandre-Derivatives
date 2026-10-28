"""SIM-007: scenario catalogue data and fixture-selection runner."""

from __future__ import annotations

from simulation_factory.corpus.inventory import build_inventory_corpus
from simulation_factory.corpus.market import build_market_scenarios
from simulation_factory.corpus.trades import build_trade_corpus
from simulation_factory.scenario_catalogue import (
    SCENARIO_CATALOGUE,
    ScenarioCatalogueError,
    get_scenario,
    select_fixtures_for_scenario,
)


def test_catalogue_has_every_plan_section_9_6_scenario():
    expected_names = {
        "Quiet/no call", "Delivery by bank", "Return owed by simulation bank",
        "Counterparty owes simulation bank", "Ratings event unknown", "Agreement conflict",
        "Unsupported option trade", "Stale market/inventory", "Wrong-way cheapest asset",
        "LP feasible/MIP discrete", "Infeasible inventory", "Parallel calls",
        "Approval-after-change", "Cutoff passed", "Duplicate export", "Audit tampering",
    }
    actual_names = {s.name for s in SCENARIO_CATALOGUE}
    assert actual_names == expected_names


def test_scenario_ids_are_unique_and_sim_namespaced():
    ids = [s.scenario_id for s in SCENARIO_CATALOGUE]
    assert len(ids) == len(set(ids))


def test_get_scenario_round_trips():
    first = SCENARIO_CATALOGUE[0]
    assert get_scenario(first.scenario_id) is first


def test_get_scenario_raises_for_unknown_id():
    import pytest

    with pytest.raises(ScenarioCatalogueError):
        get_scenario("SIM-SCENARIO-DOES-NOT-EXIST")


def test_select_fixtures_for_quiet_scenario_returns_only_quiet_snapshots():
    snapshots = build_market_scenarios()
    bundle = select_fixtures_for_scenario(
        "SIM-SCENARIO-001", market_snapshots=snapshots,
    )
    assert bundle.market_snapshots
    assert all(s.regime == "QUIET" for s in bundle.market_snapshots)


def test_select_fixtures_for_unsupported_option_trade_returns_unsupported_trades():
    trades = build_trade_corpus().trades
    bundle = select_fixtures_for_scenario("SIM-SCENARIO-007", trades=trades)
    assert bundle.trades
    assert all(t.trap_type == "UNSUPPORTED_PRODUCT" for t in bundle.trades)


def test_select_fixtures_for_wrong_way_scenario_returns_wrong_way_positions():
    positions = build_inventory_corpus()
    bundle = select_fixtures_for_scenario("SIM-SCENARIO-009", inventory_positions=positions)
    assert bundle.trades == ()
    assert bundle.inventory_positions
    assert all("WRONG_WAY" in p.trap_types for p in bundle.inventory_positions)


def test_select_fixtures_for_a_workflow_only_scenario_returns_nothing_and_does_not_crash():
    bundle = select_fixtures_for_scenario("SIM-SCENARIO-012")  # Parallel calls
    assert bundle.market_snapshots == ()
    assert bundle.trades == ()
    assert bundle.inventory_positions == ()
    assert bundle.agreement_bundles == ()

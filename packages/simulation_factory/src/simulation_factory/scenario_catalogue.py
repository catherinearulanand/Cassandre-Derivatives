"""SIM-007: the scenario catalogue and a pure-Python fixture-selection
runner skeleton, per plan Section 9.6.

`SCENARIO_CATALOGUE` implements the exact fifteen-row table from plan
Section 9.6 as data: scenario name -> expected control behavior description.
`ScenarioFixtureSelector` is a small, pure fixture-selection utility: given
a scenario id and the generated corpora (trades/market/inventory/
agreements), it returns the subset of fixtures tagged as relevant to that
scenario. It does not invoke ClauseOS, RiskOS, or CollateralOS -- wiring
this corpus into a product's own test suite is explicitly out of scope for
this work item (see the package README).
"""

from __future__ import annotations

from dataclasses import dataclass

from simulation_factory.corpus.agreements import AgreementBundle
from simulation_factory.corpus.inventory import InventoryPosition
from simulation_factory.corpus.market import MarketSnapshot
from simulation_factory.corpus.trades import TradeRecord
from simulation_factory.identifiers import require_sim_identifier


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    name: str
    expected_control_behavior: str
    # Tag-based selection hints -- how `select_fixtures_for_scenario` finds
    # matching corpus objects for this scenario. `None` means "this
    # scenario is a workflow/process-level case with no single fixture
    # subset to select" (e.g. "Parallel calls", "Audit tampering").
    market_regime: str | None = None
    trade_trap_type: str | None = None
    inventory_trap_type: str | None = None
    agreement_coverage_item: str | None = None

    def __post_init__(self) -> None:
        require_sim_identifier(self.scenario_id, field_name="scenario_id")


# The exact fifteen rows of plan Section 9.6, in table order.
SCENARIO_CATALOGUE: tuple[ScenarioDefinition, ...] = (
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-001",
        name="Quiet/no call",
        expected_control_behavior="RiskOS returns no transfer; CollateralOS records no action",
        market_regime="QUIET",
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-002",
        name="Delivery by bank",
        expected_control_behavior="Verified allocation, reservation, approval, preflight, export",
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-003",
        name="Return owed by simulation bank",
        expected_control_behavior=(
            "Determine holding/title arrangement; release only collateral the bank "
            "holds/controls under approved return rule; substitutions use "
            "delivery-before-release"
        ),
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-004",
        name="Counterparty owes simulation bank",
        expected_control_behavior=(
            "Record receivable call, validate inbound proposed assets and internal "
            "instruction data, simulate match/receipt, reconcile held balance; never "
            "optimise bank outbound inventory"
        ),
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-005",
        name="Ratings event unknown",
        expected_control_behavior="ClauseOS returns external state required; no derived active threshold",
        agreement_coverage_item="ratings-contingent threshold with missing current state",
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-006",
        name="Agreement conflict",
        expected_control_behavior="Publication blocked",
        agreement_coverage_item="conflicting clauses requiring review",
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-007",
        name="Unsupported option trade",
        expected_control_behavior="RiskOS rejects affected purpose or batch according to explicit policy",
        trade_trap_type="UNSUPPORTED_PRODUCT",
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-008",
        name="Stale market/inventory",
        expected_control_behavior="Calculation or plan non-actionable",
        market_regime=None,
        inventory_trap_type="STALE_PRICE",
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-009",
        name="Wrong-way cheapest asset",
        expected_control_behavior="Asset excluded with exact rule evidence",
        inventory_trap_type="WRONG_WAY",
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-010",
        name="LP feasible/MIP discrete",
        expected_control_behavior="LP preview differs; verified MIP plan governs",
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-011",
        name="Infeasible inventory",
        expected_control_behavior="Solver evidence and operational diagnosis; no fabricated plan",
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-012",
        name="Parallel calls",
        expected_control_behavior="One reservation wins; conflicts replan",
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-013",
        name="Approval-after-change",
        expected_control_behavior="Approval invalidated",
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-014",
        name="Cutoff passed",
        expected_control_behavior="Settlement preflight fails",
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-015",
        name="Duplicate export",
        expected_control_behavior="Idempotency prevents second export",
    ),
    ScenarioDefinition(
        scenario_id="SIM-SCENARIO-016",
        name="Audit tampering",
        expected_control_behavior="Verification fails at altered event",
    ),
)


@dataclass(frozen=True)
class ScenarioFixtureBundle:
    scenario: ScenarioDefinition
    market_snapshots: tuple[MarketSnapshot, ...]
    trades: tuple[TradeRecord, ...]
    inventory_positions: tuple[InventoryPosition, ...]
    agreement_bundles: tuple[AgreementBundle, ...]


class ScenarioCatalogueError(ValueError):
    pass


def get_scenario(scenario_id: str) -> ScenarioDefinition:
    for scenario in SCENARIO_CATALOGUE:
        if scenario.scenario_id == scenario_id:
            return scenario
    raise ScenarioCatalogueError(f"no scenario with scenario_id {scenario_id!r} in the catalogue")


def select_fixtures_for_scenario(
    scenario_id: str,
    *,
    market_snapshots: tuple[MarketSnapshot, ...] = (),
    trades: tuple[TradeRecord, ...] = (),
    inventory_positions: tuple[InventoryPosition, ...] = (),
    agreement_bundles: tuple[AgreementBundle, ...] = (),
) -> ScenarioFixtureBundle:
    """Pure fixture selection: filter each generated corpus down to the
    subset tagged as relevant to `scenario_id`. This never calls into
    ClauseOS/RiskOS/CollateralOS -- it is a cataloguing utility a later
    integration step can use to decide *which* already-generated fixtures to
    feed a product's own test harness."""

    scenario = get_scenario(scenario_id)

    matched_market = (
        tuple(s for s in market_snapshots if s.regime == scenario.market_regime)
        if scenario.market_regime
        else ()
    )
    matched_trades = (
        tuple(t for t in trades if t.trap_type == scenario.trade_trap_type)
        if scenario.trade_trap_type
        else ()
    )
    matched_inventory = (
        tuple(p for p in inventory_positions if scenario.inventory_trap_type in p.trap_types)
        if scenario.inventory_trap_type
        else ()
    )
    matched_agreements = (
        tuple(b for b in agreement_bundles if b.coverage_item == scenario.agreement_coverage_item)
        if scenario.agreement_coverage_item
        else ()
    )

    return ScenarioFixtureBundle(
        scenario=scenario,
        market_snapshots=matched_market,
        trades=matched_trades,
        inventory_positions=matched_inventory,
        agreement_bundles=matched_agreements,
    )

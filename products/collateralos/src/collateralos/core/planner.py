"""Planner orchestration (plan Section 12.4 steps 1-7, work items COL-006
through COL-009).

Simplification honestly recorded: this S1 slice assumes every inventory
position's `market_value.currency` is already expressed in the obligation's
valuation currency (plan Section 1.3: USD is the S1 reporting/CSA valuation
currency). A `CASH_EUR` position's `quantity.unit` records its native
currency face amount, but `market_value` is pre-converted USD; no
`money_types.FXConversion` lineage is modeled for inventory valuation in this
slice. This keeps the eligibility/haircut/coverage arithmetic in one currency
without inventing an FX rate source, and is recorded here rather than
silently assumed.

Lexicographic lot 1 of plan Section 12.5's three-stage objective (minimize
tail-liquidity/settlement risk, then cost, then churn) is simplified to a
single declared per-lot cost-rate proxy for this slice; the full three-stage
ordering is deferred scope (see README), not silently dropped.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from decimal import Decimal

from money_types import Money, Quantity

from collateralos.core.lp_preview import run_lp_preview
from collateralos.core.mip_planner import MILP_STATUS_OPTIMAL, run_mip_planner
from collateralos.core.model_inputs import PositionModelInput, build_eligible_model_inputs
from collateralos.domain.inventory import InventoryPosition
from collateralos.domain.obligation import Obligation
from collateralos.domain.plan import AllocationLine, Plan, SolverEvidence
from collateralos.domain.policy import PolicyBundle
from collateralos.domain.reservation import InventoryBalanceState
from collateralos.domain.status import SolverStatus
from collateralos.domain.verifier import (
    recompute_adjusted_value,
    required_coverage_threshold,
    verify_candidate_allocation,
)

SOLVER_BUILD_ID = "scipy-highs"


def _solver_build_label() -> str:
    import scipy

    return f"{SOLVER_BUILD_ID}-scipy{scipy.__version__}"


def _lines_from_lot_counts(
    lot_counts: dict[str, int],
    inputs_by_id: dict[str, PositionModelInput],
    positions_by_id: dict[str, InventoryPosition],
    policy: PolicyBundle,
) -> tuple[AllocationLine, ...]:
    lines = []
    for position_id, count in lot_counts.items():
        item = inputs_by_id[position_id]
        position = positions_by_id[position_id]
        delivered_amount = item.min_lot * count
        haircut = policy.haircut_for(position.asset_class)
        adjusted = recompute_adjusted_value(position, delivered_amount, haircut)
        lines.append(
            AllocationLine(
                position_id=position_id,
                delivered_quantity=Quantity(amount=str(delivered_amount), unit=position.quantity.unit),
                adjusted_value=Money(
                    amount=str(adjusted), currency=position.market_value.currency, rounding_context="candidate"
                ),
            )
        )
    return tuple(lines)


def _greedy_fallback(
    inputs: list[PositionModelInput],
    *,
    required_coverage: Decimal,
    concentration_cap_pct: Decimal,
    existing_held_by_issuer_group: dict[str, Decimal],
    existing_held_total: Decimal,
) -> dict[str, int]:
    """Bounded, cost-ascending greedy search used only when the MIP solver
    itself finds nothing or its candidate fails independent verification
    (plan Section 12.6: FEASIBLE_HEURISTIC_VERIFIED). At 20 positions this
    terminates in well under the solver's own time limit."""
    lot_counts: dict[str, int] = {}
    delivered_sum = Decimal("0")
    delivered_by_group = dict(existing_held_by_issuer_group)
    total_pool = existing_held_total

    for item in sorted(inputs, key=lambda i: i.cost_rate):
        if delivered_sum >= required_coverage:
            break
        for _ in range(item.max_lots):
            if delivered_sum >= required_coverage:
                break
            lot_value = item.unit_adjusted_price * item.min_lot
            candidate_total_pool = total_pool + lot_value
            if item.issuer_group_id is not None:
                candidate_group_total = delivered_by_group.get(item.issuer_group_id, Decimal("0")) + lot_value
                if candidate_group_total > concentration_cap_pct * candidate_total_pool:
                    break  # another lot of this position would breach the cap
            else:
                candidate_group_total = None

            lot_counts[item.position_id] = lot_counts.get(item.position_id, 0) + 1
            delivered_sum += lot_value
            total_pool = candidate_total_pool
            if item.issuer_group_id is not None:
                delivered_by_group[item.issuer_group_id] = candidate_group_total

    return lot_counts


@dataclass(frozen=True)
class PlanningContext:
    obligation: Obligation
    positions: tuple[InventoryPosition, ...]
    balances: dict[str, InventoryBalanceState]
    policy: PolicyBundle
    inventory_snapshot_id: str
    inventory_snapshot_version: str
    as_of: dt.datetime
    existing_held_by_issuer_group: dict[str, Decimal]
    existing_held_total: Decimal
    cost_rate_by_position: dict[str, Decimal] | None = None


def plan_obligation(ctx: PlanningContext, *, plan_id: str) -> Plan:
    positions_by_id = {p.position_id: p for p in ctx.positions}
    inputs = build_eligible_model_inputs(
        positions=ctx.positions,
        balances=ctx.balances,
        policy=ctx.policy,
        counterparty_id=ctx.obligation.counterparty_id,
        as_of=ctx.as_of,
        cost_rate_by_position=ctx.cost_rate_by_position,
    )
    inputs_by_id = {i.position_id: i for i in inputs}
    required_coverage = required_coverage_threshold(ctx.obligation.amount.amount, ctx.policy.coverage_tolerance_pct)

    run_lp_preview(
        inputs=inputs,
        required_coverage=required_coverage,
        concentration_cap_pct=ctx.policy.concentration_cap_pct,
        existing_held_by_issuer_group=ctx.existing_held_by_issuer_group,
        existing_held_total=ctx.existing_held_total,
    )  # diagnostic only; plan Section 12.4 step 4 -- not gating.

    start = time.monotonic()
    mip = run_mip_planner(
        inputs=inputs,
        required_coverage=required_coverage,
        concentration_cap_pct=ctx.policy.concentration_cap_pct,
        existing_held_by_issuer_group=ctx.existing_held_by_issuer_group,
        existing_held_total=ctx.existing_held_total,
        time_limit_seconds=ctx.policy.solver_time_limit_seconds,
        gap_tolerance_pct=ctx.policy.solver_gap_tolerance_pct,
    )
    runtime = Decimal(str(round(time.monotonic() - start, 6)))

    method = "MIP"
    lot_counts = mip.lot_counts_by_position
    notes: list[str] = [f"mip_status={mip.status} mip_message={mip.message!r}"]

    lines = _lines_from_lot_counts(lot_counts, inputs_by_id, positions_by_id, ctx.policy)
    reservable_now = {pid: ctx.balances[pid].reservable_now for pid in inputs_by_id}

    verification = verify_candidate_allocation(
        obligation_amount=ctx.obligation.amount,
        obligation_currency=ctx.obligation.amount.currency,
        counterparty_id=ctx.obligation.counterparty_id,
        candidate_lines=lines,
        positions_by_id=positions_by_id,
        policy=ctx.policy,
        as_of=ctx.as_of,
        reservable_now=reservable_now,
        existing_held_by_issuer_group=ctx.existing_held_by_issuer_group,
        existing_held_total=ctx.existing_held_total,
    )

    if not lines or not verification.ok:
        notes.append(
            "MIP candidate rejected by independent verifier: " + ",".join(verification.violations)
            if lines
            else "MIP found no candidate"
        )
        greedy_counts = _greedy_fallback(
            inputs,
            required_coverage=required_coverage,
            concentration_cap_pct=ctx.policy.concentration_cap_pct,
            existing_held_by_issuer_group=ctx.existing_held_by_issuer_group,
            existing_held_total=ctx.existing_held_total,
        )
        greedy_lines = _lines_from_lot_counts(greedy_counts, inputs_by_id, positions_by_id, ctx.policy)
        greedy_verification = verify_candidate_allocation(
            obligation_amount=ctx.obligation.amount,
            obligation_currency=ctx.obligation.amount.currency,
            counterparty_id=ctx.obligation.counterparty_id,
            candidate_lines=greedy_lines,
            positions_by_id=positions_by_id,
            policy=ctx.policy,
            as_of=ctx.as_of,
            reservable_now=reservable_now,
            existing_held_by_issuer_group=ctx.existing_held_by_issuer_group,
            existing_held_total=ctx.existing_held_total,
        )
        if greedy_lines and greedy_verification.ok:
            method = "GREEDY_HEURISTIC_VERIFIED"
            lines = greedy_lines
            verification = greedy_verification
            status = SolverStatus.FEASIBLE_HEURISTIC_VERIFIED
            notes.append("independently verified greedy fallback accepted")
        else:
            status = (
                SolverStatus.TIMEOUT_NO_FEASIBLE
                if mip.status == 1 and not lot_counts
                else SolverStatus.INFEASIBLE_SOLVER_CONFIRMED
            )
            notes.append("greedy fallback also failed independent verification or found nothing")
            lines = ()
    else:
        gap = mip.mip_gap if mip.mip_gap is not None else 0.0
        if mip.status == MILP_STATUS_OPTIMAL and gap <= 1e-9:
            status = SolverStatus.FEASIBLE_OPTIMAL
        elif gap <= float(ctx.policy.solver_gap_tolerance_pct):
            status = SolverStatus.FEASIBLE_BOUNDED_GAP
        else:
            status = SolverStatus.FEASIBLE_BOUNDED_GAP
            notes.append(f"solver gap {gap} exceeded declared tolerance {ctx.policy.solver_gap_tolerance_pct}; reported conservatively")

    evidence = SolverEvidence(
        method=method,
        solver_build=_solver_build_label(),
        status=status,
        objective_value=str(mip.objective_value) if mip.objective_value is not None else None,
        gap_pct=str(mip.mip_gap) if mip.mip_gap is not None else None,
        runtime_seconds=str(runtime),
        time_limit_seconds=str(ctx.policy.solver_time_limit_seconds),
        gap_tolerance_pct=str(ctx.policy.solver_gap_tolerance_pct),
        verified=verification.ok and bool(lines),
        verifier_notes=tuple(notes) + verification.violations,
    )

    return Plan(
        plan_id=plan_id,
        obligation_id=ctx.obligation.obligation_id,
        obligation_version=ctx.obligation.version,
        policy_id=ctx.policy.policy_id,
        policy_version=ctx.policy.version,
        inventory_snapshot_id=ctx.inventory_snapshot_id,
        inventory_snapshot_version=ctx.inventory_snapshot_version,
        lines=lines if (verification.ok and lines) else (),
        status=status,
        evidence=evidence,
    )

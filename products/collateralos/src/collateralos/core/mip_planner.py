"""MIP planner for discrete lots (plan Section 12.4 step 5, work item COL-007).

Decision variables are integer lot counts `n_i` (never fractional units),
enforcing plan Section 12.5's minimum-lot hard constraint directly in the
model rather than only in post-hoc verification. `scipy.optimize.milp` is
scipy's HiGHS-backed MIP entry point (available since scipy 1.9); this
satisfies ADR-008 without the separate `highspy` package. The result here is
still just a *candidate* -- `collateralos.domain.verifier` is the authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from scipy.optimize import Bounds, LinearConstraint, milp

from collateralos.core.model_inputs import PositionModelInput

# scipy.optimize.milp OptimizeResult.status values (per scipy docs):
MILP_STATUS_OPTIMAL = 0
MILP_STATUS_ITERATION_OR_TIME_LIMIT = 1
MILP_STATUS_INFEASIBLE = 2
MILP_STATUS_UNBOUNDED = 3


@dataclass(frozen=True)
class MIPResult:
    status: int
    success: bool
    message: str
    objective_value: float | None
    mip_gap: float | None
    lot_counts_by_position: dict[str, int]


def run_mip_planner(
    *,
    inputs: list[PositionModelInput],
    required_coverage: Decimal,
    concentration_cap_pct: Decimal,
    existing_held_by_issuer_group: dict[str, Decimal],
    existing_held_total: Decimal,
    time_limit_seconds: Decimal,
    gap_tolerance_pct: Decimal,
) -> MIPResult:
    if not inputs:
        return MIPResult(
            status=MILP_STATUS_INFEASIBLE,
            success=False,
            message="no eligible positions",
            objective_value=None,
            mip_gap=None,
            lot_counts_by_position={},
        )

    n = len(inputs)
    # Objective coefficient is cost per LOT (cost_rate * min_lot), so `n_i`
    # (lot counts) is the natural decision variable throughout.
    c = [float(i.cost_rate * i.min_lot) for i in inputs]
    bounds = Bounds(lb=[0] * n, ub=[i.max_lots for i in inputs])
    integrality = [1] * n

    constraints = []

    # Coverage: sum(unit_price_i * min_lot_i * n_i) >= required_coverage
    coverage_row = [float(i.unit_adjusted_price * i.min_lot) for i in inputs]
    constraints.append(LinearConstraint(A=[coverage_row], lb=[float(required_coverage)], ub=[float("inf")]))

    groups = {i.issuer_group_id for i in inputs if i.issuer_group_id is not None}
    groups |= set(existing_held_by_issuer_group)
    cap = float(concentration_cap_pct)
    for group in groups:
        existing_g = float(existing_held_by_issuer_group.get(group, Decimal("0")))
        row = [
            float(i.unit_adjusted_price * i.min_lot) * ((1.0 if i.issuer_group_id == group else 0.0) - cap)
            for i in inputs
        ]
        upper = cap * float(existing_held_total) - existing_g
        constraints.append(LinearConstraint(A=[row], lb=[float("-inf")], ub=[upper]))

    result = milp(
        c,
        constraints=constraints,
        bounds=bounds,
        integrality=integrality,
        options={
            "time_limit": float(time_limit_seconds),
            "mip_rel_gap": float(gap_tolerance_pct),
        },
    )

    lot_counts: dict[str, int] = {}
    if result.x is not None:
        for idx, item in enumerate(inputs):
            count = int(round(result.x[idx]))
            if count > 0:
                lot_counts[item.position_id] = count

    return MIPResult(
        status=int(result.status),
        success=bool(result.success),
        message=str(result.message),
        objective_value=float(result.fun) if result.success else None,
        mip_gap=float(result.mip_gap) if getattr(result, "mip_gap", None) is not None else None,
        lot_counts_by_position=lot_counts,
    )

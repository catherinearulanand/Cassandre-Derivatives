"""Fast LP preview (plan Section 12.4 step 4, work item COL-006).

Continuous relaxation for diagnostics only -- lot-size integrality is not
enforced here. `scipy.optimize.linprog(method="highs")` is scipy's HiGHS
backend (default LP method since scipy 1.6), satisfying ADR-008's intent
without the separate `highspy` package (see `docs/adr/ADR-008-solver.md`).
This result is never itself reserved/approved/exported; only the MIP
candidate, after independent Decimal verification, is actionable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from scipy.optimize import linprog

from collateralos.core.model_inputs import PositionModelInput


@dataclass(frozen=True)
class LPPreviewResult:
    success: bool
    status_message: str
    objective_value: float | None
    delivered_by_position: dict[str, float]


def run_lp_preview(
    *,
    inputs: list[PositionModelInput],
    required_coverage: Decimal,
    concentration_cap_pct: Decimal,
    existing_held_by_issuer_group: dict[str, Decimal],
    existing_held_total: Decimal,
) -> LPPreviewResult:
    if not inputs:
        return LPPreviewResult(success=False, status_message="no eligible positions", objective_value=None, delivered_by_position={})

    n = len(inputs)
    c = [float(i.cost_rate) for i in inputs]
    # Continuous relaxation: x_i in delivered-quantity units, bounded by
    # max_lots * min_lot (the same reservable capacity the MIP respects).
    bounds = [(0.0, float(i.max_lots * i.min_lot)) for i in inputs]

    a_ub: list[list[float]] = []
    b_ub: list[float] = []

    # Coverage: sum(unit_price_i * x_i) >= required_coverage  =>  -sum(...) <= -required_coverage
    a_ub.append([-float(i.unit_adjusted_price) for i in inputs])
    b_ub.append(-float(required_coverage))

    groups = {i.issuer_group_id for i in inputs if i.issuer_group_id is not None}
    groups |= set(existing_held_by_issuer_group)
    cap = float(concentration_cap_pct)
    for group in groups:
        existing_g = float(existing_held_by_issuer_group.get(group, Decimal("0")))
        row = [
            float(i.unit_adjusted_price) * ((1.0 if i.issuer_group_id == group else 0.0) - cap)
            for i in inputs
        ]
        a_ub.append(row)
        b_ub.append(cap * float(existing_held_total) - existing_g)

    result = linprog(c=c, A_ub=a_ub, b_ub=b_ub, bounds=bounds, method="highs")

    delivered = {}
    if result.x is not None:
        delivered = {inputs[idx].position_id: float(result.x[idx]) for idx in range(n)}

    return LPPreviewResult(
        success=bool(result.success),
        status_message=str(result.message),
        objective_value=float(result.fun) if result.success else None,
        delivered_by_position=delivered,
    )

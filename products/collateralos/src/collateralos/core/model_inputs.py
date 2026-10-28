"""Shared float-domain model construction for the LP preview and MIP planner
(plan Section 12.4 steps 4-5). This is the ONLY place in CollateralOS that is
allowed to convert `Decimal` money into `float` for scipy/HiGHS -- the
resulting candidate is always re-verified in exact `Decimal` by
`collateralos.domain.verifier` before it can be accepted (ADR-008, plan
Section 25.3: "solver-internal arithmetic may use floats ... but the final
accepted allocation must be independently re-verified in exact Decimal").
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from collateralos.domain.eligibility import compile_eligibility
from collateralos.domain.inventory import InventoryPosition
from collateralos.domain.policy import PolicyBundle
from collateralos.domain.reservation import InventoryBalanceState
from collateralos.domain.verifier import unit_adjusted_price


@dataclass(frozen=True)
class PositionModelInput:
    position_id: str
    unit_adjusted_price: Decimal  # per unit of quantity, post-haircut
    min_lot: Decimal
    max_lots: int  # floor(reservable_now / min_lot)
    issuer_group_id: str | None
    cost_rate: Decimal  # simplified all-in-cost proxy per unit delivered (Section 12.5 objective)


def build_eligible_model_inputs(
    *,
    positions: tuple[InventoryPosition, ...],
    balances: dict[str, InventoryBalanceState],
    policy: PolicyBundle,
    counterparty_id: str,
    as_of,
    cost_rate_by_position: dict[str, Decimal] | None = None,
) -> list[PositionModelInput]:
    """Only positions the eligibility compiler marks ELIGIBLE enter the
    solver's decision space at all -- ineligible assets are never offered as
    a candidate, not merely penalized in the objective (plan Section 12.3:
    "A model may explain a rejection but cannot activate, modify, or waive a
    rule")."""
    cost_rate_by_position = cost_rate_by_position or {}
    inputs: list[PositionModelInput] = []
    for position in positions:
        eligibility = compile_eligibility(position, policy=policy, counterparty_id=counterparty_id, as_of=as_of)
        if not eligibility.eligible:
            continue
        balance = balances.get(position.position_id)
        if balance is None:
            continue
        reservable = balance.reservable_now
        if reservable <= 0:
            continue
        min_lot = policy.min_lot.get(position.asset_class)
        if min_lot is None or min_lot <= 0:
            continue
        max_lots = int(reservable // min_lot)
        if max_lots <= 0:
            continue
        haircut = policy.haircut_for(position.asset_class)
        unit_price = unit_adjusted_price(position, haircut)
        inputs.append(
            PositionModelInput(
                position_id=position.position_id,
                unit_adjusted_price=unit_price,
                min_lot=min_lot,
                max_lots=max_lots,
                issuer_group_id=position.issuer_group_id,
                cost_rate=cost_rate_by_position.get(position.position_id, Decimal("0.0001")),
            )
        )
    return inputs

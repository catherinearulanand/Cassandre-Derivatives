"""Independent domain verifier (plan Section 12.4 step 6, 12.5, ADR-008).

This is the "independent domain verifier" ADR-008 requires: every candidate
allocation -- however it was produced (LP relaxation, MIP, brute force, or a
hand-built test fixture) -- is independently re-verified here in exact
`Decimal` arithmetic against the *full* hard-constraint set before it may be
accepted. A solver's own "optimal"/"feasible" status is never trusted on its
own (plan Section 25.3, ADR-008): this module recomputes coverage,
eligibility, haircuts, concentration, minimum lot, and availability from
scratch and rejects any violation regardless of what the solver reported.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from money_types import Money

from collateralos.domain.eligibility import compile_eligibility
from collateralos.domain.inventory import InventoryPosition
from collateralos.domain.plan import AllocationLine
from collateralos.domain.policy import PolicyBundle

TWO_DP = Decimal("0.01")


class VerifierInputError(ValueError):
    """Caller-supplied verifier inputs are incomplete -- fail closed rather
    than guessing (ADR-014), e.g. a delivered position missing from
    `positions_by_id` or `reservable_now`."""


@dataclass(frozen=True)
class VerificationOutcome:
    ok: bool
    violations: tuple[str, ...]
    recomputed_lines: tuple[AllocationLine, ...]
    delivered_sum: Decimal
    required_coverage_threshold: Decimal


def unit_adjusted_price(position: InventoryPosition, haircut: Decimal) -> Decimal:
    """Per-unit, post-haircut price: shared by the verifier and by
    `collateralos.core.model_inputs` (which converts this same Decimal value
    to float for the LP/MIP solvers -- the only place that conversion is
    allowed)."""
    if position.quantity.amount == 0:
        raise VerifierInputError(f"position {position.position_id} has zero quantity")
    unit_market_price = position.market_value.amount / position.quantity.amount
    return unit_market_price * (Decimal(1) - haircut)


def recompute_adjusted_value(position: InventoryPosition, delivered_amount: Decimal, haircut: Decimal) -> Decimal:
    """Conservative: round the delivered adjusted value DOWN (plan Section
    12.5: "conservatively round delivered value down")."""
    raw = unit_adjusted_price(position, haircut) * delivered_amount
    return raw.quantize(TWO_DP, rounding=ROUND_DOWN)


def required_coverage_threshold(obligation_amount: Decimal, tolerance_pct: Decimal) -> Decimal:
    """Conservative: round the required-coverage threshold UP (plan Section
    12.5: "round required coverage up")."""
    raw = obligation_amount * (Decimal(1) - tolerance_pct)
    return raw.quantize(TWO_DP, rounding=ROUND_UP)


def verify_candidate_allocation(
    *,
    obligation_amount: Money,
    obligation_currency: str,
    counterparty_id: str,
    candidate_lines: tuple[AllocationLine, ...],
    positions_by_id: dict[str, InventoryPosition],
    policy: PolicyBundle,
    as_of: dt.datetime,
    reservable_now: dict[str, Decimal],
    existing_held_by_issuer_group: dict[str, Decimal],
    existing_held_total: Decimal,
) -> VerificationOutcome:
    violations: list[str] = []
    recomputed_lines: list[AllocationLine] = []
    seen_position_ids: set[str] = set()

    if obligation_amount.currency != obligation_currency:
        raise VerifierInputError("obligation_amount.currency does not match obligation_currency")

    for line in candidate_lines:
        if line.position_id in seen_position_ids:
            violations.append(f"DUPLICATE_LINE_FOR_POSITION:{line.position_id}")
            continue
        seen_position_ids.add(line.position_id)

        position = positions_by_id.get(line.position_id)
        if position is None:
            violations.append(f"UNKNOWN_POSITION:{line.position_id}")
            continue

        if line.delivered_quantity.amount <= 0:
            violations.append(f"NON_POSITIVE_QUANTITY:{line.position_id}")
            continue

        eligibility = compile_eligibility(position, policy=policy, counterparty_id=counterparty_id, as_of=as_of)
        if not eligibility.eligible:
            violations.append(
                f"INELIGIBLE_POSITION:{line.position_id}:"
                + ",".join(r.value for r in eligibility.rejection_reasons)
            )
            continue

        available = reservable_now.get(line.position_id)
        if available is None:
            raise VerifierInputError(f"no reservable_now entry for delivered position {line.position_id}")
        if line.delivered_quantity.amount > available:
            violations.append(f"EXCEEDS_AVAILABLE_INVENTORY:{line.position_id}")
            continue

        min_lot = policy.min_lot.get(position.asset_class)
        if min_lot is None or min_lot <= 0:
            violations.append(f"NO_MINIMUM_LOT_POLICY:{line.position_id}")
            continue
        if (line.delivered_quantity.amount % min_lot) != 0:
            violations.append(f"MINIMUM_LOT_VIOLATION:{line.position_id}")
            continue

        haircut = policy.haircut_for(position.asset_class)
        true_adjusted = recompute_adjusted_value(position, line.delivered_quantity.amount, haircut)
        if line.adjusted_value.currency != position.market_value.currency:
            violations.append(f"CURRENCY_MISMATCH:{line.position_id}")
            continue
        # The candidate must not overstate its own value; understating is
        # merely suboptimal, not a hard-constraint violation.
        if line.adjusted_value.amount > true_adjusted:
            violations.append(f"STATED_VALUE_OVERSTATED:{line.position_id}")

        recomputed_lines.append(
            AllocationLine(
                position_id=position.position_id,
                delivered_quantity=line.delivered_quantity,
                adjusted_value=Money(
                    amount=str(true_adjusted),
                    currency=position.market_value.currency,
                    rounding_context="haircut_adjusted_verified",
                ),
            )
        )

    delivered_sum = sum((line.adjusted_value.amount for line in recomputed_lines), Decimal("0"))

    threshold = required_coverage_threshold(obligation_amount.amount, policy.coverage_tolerance_pct)
    if delivered_sum < threshold:
        violations.append(f"INSUFFICIENT_COVERAGE:{delivered_sum}<{threshold}")

    # Post-transfer concentration cap (plan Section 12.3: tested against
    # existing held collateral + proposed delivery, not the delivery alone).
    delivered_by_group: dict[str, Decimal] = {}
    for line in recomputed_lines:
        position = positions_by_id[line.position_id]
        if position.issuer_group_id is None:
            continue
        delivered_by_group[position.issuer_group_id] = (
            delivered_by_group.get(position.issuer_group_id, Decimal("0")) + line.adjusted_value.amount
        )

    post_transfer_total = existing_held_total + delivered_sum
    if post_transfer_total > 0:
        all_groups = set(existing_held_by_issuer_group) | set(delivered_by_group)
        for group in all_groups:
            group_existing = existing_held_by_issuer_group.get(group, Decimal("0"))
            group_delivered = delivered_by_group.get(group, Decimal("0"))
            post_transfer_group = group_existing + group_delivered
            cap_amount = policy.concentration_cap_pct * post_transfer_total
            if post_transfer_group > cap_amount:
                violations.append(
                    f"CONCENTRATION_CAP_EXCEEDED:{group}:{post_transfer_group}>{cap_amount}"
                )

    return VerificationOutcome(
        ok=len(violations) == 0,
        violations=tuple(violations),
        recomputed_lines=tuple(recomputed_lines),
        delivered_sum=delivered_sum,
        required_coverage_threshold=threshold,
    )

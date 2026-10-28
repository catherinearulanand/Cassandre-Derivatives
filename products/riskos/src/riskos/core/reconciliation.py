"""Reconciliation against the synthetic counterparty result, break
classification, and the undisputed-amount rule, per plan Sections 11.3 and
11.5.

DEC-OPS-02 (provisional, this slice): RiskOS reconciliation tolerance is
exact-match only — any difference in call status or call amount is a BREAK,
never WITHIN_TOLERANCE (that status exists in the enum for a future pack
with a declared tolerance, but this slice's method pack never emits it).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from money_types import Money

from riskos.domain.entities import ReconciliationBreak, ReconciliationCase
from riskos.domain.enums import (
    BreakType,
    CallStatus,
    DisputeAgreementCase,
    ReconciliationStatus,
    UndisputedDeterminationStatus,
)


@dataclass(frozen=True)
class PartyCallView:
    """One party's side of a margin-call comparison. `responded=False` means
    no counterparty response was received at all (plan 11.5's "one party has
    not responded" case)."""

    party_label: str
    call_status: CallStatus
    call_amount: Money
    exposure: Money | None = None
    credit_support_balance: Money | None = None
    trade_population_count: int | None = None
    as_of: dt.datetime | None = None
    acknowledged_amount: Money | None = None
    responded: bool = True


def classify_break(calculation_party: PartyCallView, counterparty: PartyCallView) -> BreakType:
    """Never force an unexplained difference into a plausible category (plan
    11.3) — checks proceed from the most specific, most attributable cause
    to the least, falling through to UNCLASSIFIED only when nothing
    structured explains the difference."""

    if (
        calculation_party.trade_population_count is not None
        and counterparty.trade_population_count is not None
        and calculation_party.trade_population_count != counterparty.trade_population_count
    ):
        return BreakType.TRADE_POPULATION

    if (
        calculation_party.exposure is not None
        and counterparty.exposure is not None
        and (
            calculation_party.exposure.amount != counterparty.exposure.amount
            or calculation_party.exposure.currency != counterparty.exposure.currency
        )
    ):
        return BreakType.MTM

    if (
        calculation_party.credit_support_balance is not None
        and counterparty.credit_support_balance is not None
        and calculation_party.credit_support_balance.amount != counterparty.credit_support_balance.amount
    ):
        return BreakType.COLLATERAL_STATE

    if (
        calculation_party.as_of is not None
        and counterparty.as_of is not None
        and calculation_party.as_of != counterparty.as_of
    ):
        return BreakType.TIMING

    difference = abs(calculation_party.call_amount.amount - counterparty.call_amount.amount)
    if difference != 0 and difference <= 1 and calculation_party.call_status == counterparty.call_status:
        # Same direction, same underlying inputs (all specific checks above
        # passed), only a small rounding-scale difference remains.
        return BreakType.ROUNDING

    if calculation_party.call_status != counterparty.call_status:
        return BreakType.CALL_LOGIC

    return BreakType.UNCLASSIFIED


def reconcile_calls(
    calculation_party: PartyCallView, counterparty: PartyCallView
) -> tuple[ReconciliationStatus, tuple[ReconciliationBreak, ...]]:
    matched = (
        calculation_party.call_status == counterparty.call_status
        and calculation_party.call_amount.amount == counterparty.call_amount.amount
        and calculation_party.call_amount.currency == counterparty.call_amount.currency
    )
    if matched:
        return ReconciliationStatus.MATCHED, ()

    break_type = classify_break(calculation_party, counterparty)
    if calculation_party.call_amount.currency == counterparty.call_amount.currency:
        difference = Money(
            amount=str(calculation_party.call_amount.amount - counterparty.call_amount.amount),
            currency=calculation_party.call_amount.currency,
            rounding_context=calculation_party.call_amount.rounding_context,
        )
    else:
        difference = calculation_party.call_amount
    brk = ReconciliationBreak(
        break_id="BRK-0001",
        break_type=break_type,
        description=(
            f"calculation party call_status={calculation_party.call_status.value} "
            f"amount={calculation_party.call_amount.amount} vs counterparty "
            f"call_status={counterparty.call_status.value} amount={counterparty.call_amount.amount}"
        ),
        calculation_party_amount=calculation_party.call_amount,
        counterparty_amount=counterparty.call_amount,
        difference=difference,
    )
    return ReconciliationStatus.BREAK, (brk,)


def determine_dispute_case(
    calculation_party: PartyCallView, counterparty: PartyCallView
) -> DisputeAgreementCase:
    if not counterparty.responded:
        return DisputeAgreementCase.ONE_PARTY_NOT_RESPONDED

    if (
        calculation_party.trade_population_count is not None
        and counterparty.trade_population_count is not None
        and calculation_party.trade_population_count != counterparty.trade_population_count
    ) or (
        calculation_party.credit_support_balance is not None
        and counterparty.credit_support_balance is not None
        and calculation_party.credit_support_balance.amount != counterparty.credit_support_balance.amount
    ):
        return DisputeAgreementCase.COLLATERAL_OR_POPULATION_DISAGREEMENT

    if counterparty.acknowledged_amount is not None and (
        counterparty.acknowledged_amount.amount < calculation_party.call_amount.amount
    ):
        return DisputeAgreementCase.PARTIAL_ACKNOWLEDGEMENT

    calc_is_transfer = calculation_party.call_status != CallStatus.NO_TRANSFER
    cp_is_transfer = counterparty.call_status != CallStatus.NO_TRANSFER
    if calc_is_transfer and cp_is_transfer and calculation_party.call_status != counterparty.call_status:
        return DisputeAgreementCase.OPPOSITE_DIRECTION

    if (
        calculation_party.call_status == counterparty.call_status
        and calculation_party.call_amount.amount == counterparty.call_amount.amount
    ):
        return DisputeAgreementCase.BOTH_AGREE_DIRECTION_AND_AMOUNT

    return DisputeAgreementCase.AGREE_DIRECTION_DIFFER_AMOUNT


def determine_undisputed_amount(
    *,
    dispute_case: DisputeAgreementCase,
    calculation_party_amount: Money,
    counterparty_amount: Money,
    lower_common_amount_authorized: bool,
) -> tuple[UndisputedDeterminationStatus, Money | None]:
    """Plan Section 11.5: only "both parties agree on direction and amount"
    produces a computed undisputed amount by default. The "lower common
    amount" exception is implemented but gated on an explicit
    `lower_common_amount_authorized` flag sourced from the method pack —
    METHOD-PACK-001 sets this False, so it is provably never taken by this
    slice's workflow."""

    if dispute_case is DisputeAgreementCase.BOTH_AGREE_DIRECTION_AND_AMOUNT:
        return UndisputedDeterminationStatus.DETERMINED, calculation_party_amount

    if dispute_case is DisputeAgreementCase.AGREE_DIRECTION_DIFFER_AMOUNT and lower_common_amount_authorized:
        lower = min(calculation_party_amount.amount, counterparty_amount.amount)
        lower_money = Money(
            amount=str(lower),
            currency=calculation_party_amount.currency,
            rounding_context=calculation_party_amount.rounding_context,
        )
        return UndisputedDeterminationStatus.DETERMINED, lower_money

    return UndisputedDeterminationStatus.UNDISPUTED_NOT_DETERMINED, None


def build_reconciliation_case(
    *,
    case_id: str,
    run_id: str,
    calculation_party: PartyCallView,
    counterparty: PartyCallView,
    lower_common_amount_authorized: bool,
) -> ReconciliationCase:
    reconciliation_status, breaks = reconcile_calls(calculation_party, counterparty)
    dispute_case = determine_dispute_case(calculation_party, counterparty)
    undisputed_status, undisputed_amount = determine_undisputed_amount(
        dispute_case=dispute_case,
        calculation_party_amount=calculation_party.call_amount,
        counterparty_amount=counterparty.call_amount,
        lower_common_amount_authorized=lower_common_amount_authorized,
    )
    return ReconciliationCase(
        case_id=case_id,
        run_id=run_id,
        calculation_party_call_status=calculation_party.call_status,
        calculation_party_call_amount=calculation_party.call_amount,
        counterparty_call_status=counterparty.call_status,
        counterparty_call_amount=counterparty.call_amount,
        dispute_case=dispute_case,
        undisputed_status=undisputed_status,
        undisputed_amount=undisputed_amount,
        reconciliation_status=reconciliation_status,
        breaks=breaks,
    )

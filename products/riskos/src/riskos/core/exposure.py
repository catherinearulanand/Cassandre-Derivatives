"""Deterministic exposure aggregation within one approved netting set (plan
Section 11.3 step 7), and Credit Support Balance derivation strictly through
the selected method pack (plan 11.3: "RiskOS derives Credit Support Balance
only through the selected method pack. It does not accept a naked scalar
balance except in an explicitly restricted diagnostic run.").
"""

from __future__ import annotations

from money_types import Money

from riskos.core.constants import METHOD_PACK_001_ROUNDING_CONTEXT, TRADE_MTM_ROUNDING_CONTEXT
from riskos.domain.entities import CollateralStatePackage, TradeMTM
from riskos.domain.enums import TradePopulationDisposition


def aggregate_exposure(trades: tuple[TradeMTM, ...], *, currency: str = "USD") -> Money:
    """Sum MTM for ACCEPTED trades only; MATURED/CANCELLED/AMENDED/REJECTED/
    DUPLICATE/UNSUPPORTED trades are population-accounted (see
    `riskos.core.population`) but never enter the exposure aggregate."""

    accepted = [t for t in trades if t.disposition is TradePopulationDisposition.ACCEPTED]
    total = Money(amount="0", currency=currency, rounding_context=TRADE_MTM_ROUNDING_CONTEXT)
    for trade in accepted:
        total = total + trade.mtm
    # Re-tag into the method pack's rounding context: same currency, same
    # amount, just handed off from the raw-MTM domain into the method-pack
    # calculation domain so it can be combined with threshold/CSB there.
    return Money(amount=str(total.amount), currency=total.currency, rounding_context=METHOD_PACK_001_ROUNDING_CONTEXT)


def derive_credit_support_balance(collateral: CollateralStatePackage, *, currency: str = "USD") -> Money:
    """Net collateral currently held minus posted, calculation-party
    perspective, per METHOD-PACK-001 (`docs/methods/METHOD-PACK-001-
    provisional.md`)."""

    held_total = Money(amount="0", currency=currency, rounding_context=METHOD_PACK_001_ROUNDING_CONTEXT)
    for position in collateral.collateral_held:
        held_total = held_total + Money(
            amount=str(position.adjusted_value.amount),
            currency=position.adjusted_value.currency,
            rounding_context=METHOD_PACK_001_ROUNDING_CONTEXT,
        )
    posted_total = Money(amount="0", currency=currency, rounding_context=METHOD_PACK_001_ROUNDING_CONTEXT)
    for position in collateral.collateral_posted:
        posted_total = posted_total + Money(
            amount=str(position.adjusted_value.amount),
            currency=position.adjusted_value.currency,
            rounding_context=METHOD_PACK_001_ROUNDING_CONTEXT,
        )
    return held_total + (-posted_total)

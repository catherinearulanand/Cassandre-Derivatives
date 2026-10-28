"""Encodes `docs/methods/METHOD-PACK-001-provisional.md` (DEC-RSK-01) as a
typed `MethodPack` object plus the pure calculation function that runs it.

STATUS: PROVISIONAL. This is a synthetic placeholder convention for the S1
simulation slice only — never present it as an approved production margin
methodology (see `docs/DECISION_REGISTER.md` entry `DEC-RSK-01`).

Convention implemented, verbatim from the method pack document:
- symmetric bilateral CSA; USD 0 threshold both parties; bilateral IA=0.
- Credit Support Balance = collateral held minus posted, calculation-party
  perspective, USD.
- MTA = USD 10,000, same both directions.
- MTA-before-rounding: compare the *raw* (unrounded) delivery/return amount
  to the MTA first, then round only the amount that clears the MTA gate.
- Rounding: round-half-up to whole USD.
- Delivery Amount = max(0, Exposure - Threshold - CreditSupportBalance);
  a Return Amount is symmetric on the negative side, gated the same way.
- Zero/negative-below-MTA nets to NO_TRANSFER, never a zero-dollar call.

Explicit convention decision this module makes because the method pack text
does not spell out inclusive/exclusive boundary behavior at exactly the MTA:
a raw amount whose magnitude is >= MTA clears the gate (inclusive). This is
recorded here, not silently assumed, and is exactly what the MTA-boundary
test (`test_method_pack.py`) pins down.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from money_types import Money

from riskos.core.constants import METHOD_PACK_001_ID, METHOD_PACK_001_ROUNDING_CONTEXT
from riskos.domain.entities import MethodPack
from riskos.domain.enums import CallStatus


def _zero(currency: str = "USD") -> Money:
    return Money(amount="0", currency=currency, rounding_context=METHOD_PACK_001_ROUNDING_CONTEXT)


def build_method_pack_001() -> MethodPack:
    """Construct the one method pack this slice implements. Every numeric
    convention traces back to DEC-RSK-01 / METHOD-PACK-001-provisional.md."""

    return MethodPack(
        method_id=METHOD_PACK_001_ID,
        version="0.1.0",
        supported_agreement_family="SIM-CSA-ZERO-THRESHOLD-BILATERAL",
        jurisdiction_label="SIMULATION_ONLY",
        effective_from=dt.date(2026, 1, 1),
        threshold_calculation_party=_zero(),
        threshold_counterparty=_zero(),
        independent_amount_calculation_party=_zero(),
        independent_amount_counterparty=_zero(),
        minimum_transfer_amount=Money(
            amount="10000", currency="USD", rounding_context=METHOD_PACK_001_ROUNDING_CONTEXT
        ),
        mta_rounding_order="MTA_BEFORE_ROUNDING",
        rounding_rule="ROUND_HALF_UP_WHOLE_USD",
        call_currency="USD",
        lower_common_amount_authorized=False,
        validation_status="RESTRICTED_USE",
        golden_case_reference="products/riskos/tests/test_method_pack.py::test_golden_case_delivery_amount",
        owner="DEC-RSK-01",
        maker="SIM-MAKER-RISKOS-001",
        checker="SIM-CHECKER-RISKOS-001",
        approval_hash="0" * 64,
        provisional=True,
    )


def _round_half_up_whole_unit(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class MethodPackCalculationResult:
    raw_net_amount: Decimal
    call_status: CallStatus
    call_amount: Money


def run_method_pack_001(
    *,
    method_pack: MethodPack,
    exposure: Money,
    credit_support_balance: Money,
    threshold_calculation_party: Money | None = None,
) -> MethodPackCalculationResult:
    """Run METHOD-PACK-001's state machine.

    `exposure` and `credit_support_balance` must already be USD (this pack's
    only supported call currency). `threshold_calculation_party` defaults to
    the pack's own threshold (USD 0 in this slice) but is accepted as a
    parameter so a future non-zero-threshold pack variant would not require
    changing this function's signature.
    """

    if method_pack.call_currency != "USD":
        raise ValueError("METHOD-PACK-001 supports USD call currency only")
    if exposure.currency != "USD" or credit_support_balance.currency != "USD":
        raise ValueError("METHOD-PACK-001 requires USD exposure and credit support balance")

    threshold = threshold_calculation_party or method_pack.threshold_calculation_party
    if threshold.currency != "USD":
        raise ValueError("METHOD-PACK-001 requires a USD threshold")

    raw = exposure.amount - threshold.amount - credit_support_balance.amount
    mta = method_pack.minimum_transfer_amount.amount

    if raw >= mta:
        # Delivery Amount = max(0, Exposure - Threshold - CSB); raw > 0 here
        # so max(0, raw) == raw.
        rounded = _round_half_up_whole_unit(raw)
        return MethodPackCalculationResult(
            raw_net_amount=raw,
            call_status=CallStatus.DELIVER,
            call_amount=Money(
                amount=str(rounded), currency="USD", rounding_context=METHOD_PACK_001_ROUNDING_CONTEXT
            ),
        )
    if raw <= -mta:
        rounded = _round_half_up_whole_unit(-raw)
        return MethodPackCalculationResult(
            raw_net_amount=raw,
            call_status=CallStatus.RETURN,
            call_amount=Money(
                amount=str(rounded), currency="USD", rounding_context=METHOD_PACK_001_ROUNDING_CONTEXT
            ),
        )
    return MethodPackCalculationResult(
        raw_net_amount=raw,
        call_status=CallStatus.NO_TRANSFER,
        call_amount=_zero(),
    )


def resolve_transfer_parties(
    *,
    call_status: CallStatus,
    calculation_entity_id: str,
    counterparty_entity_id: str,
) -> tuple[str | None, str | None]:
    """Explicit identity resolution per plan 11.3: direction is derived from
    named calculation-party/counterparty identities, never inferred purely
    from the sign of `raw_net_amount`. DELIVER means the counterparty
    delivers collateral to the calculation party (calc party is
    under-collateralized); RETURN means the calculation party returns
    excess collateral to the counterparty."""

    if call_status is CallStatus.DELIVER:
        return counterparty_entity_id, calculation_entity_id
    if call_status is CallStatus.RETURN:
        return calculation_entity_id, counterparty_entity_id
    if call_status is CallStatus.NO_TRANSFER:
        return None, None
    raise ValueError(f"unhandled CallStatus {call_status!r}")  # pragma: no cover - exhaustive enum

"""Unit tests for METHOD-PACK-001 (`docs/methods/METHOD-PACK-001-
provisional.md`, DEC-RSK-01). These are independent, hand-derived
expectations per plan 9.3: "Never generate expected RiskOS totals using the
same implementation under test."""

from __future__ import annotations

from money_types import Money

from riskos.core.constants import METHOD_PACK_001_ROUNDING_CONTEXT
from riskos.core.method_pack import build_method_pack_001, resolve_transfer_parties, run_method_pack_001
from riskos.domain.enums import CallStatus


def _m(amount: str) -> Money:
    return Money(amount=amount, currency="USD", rounding_context=METHOD_PACK_001_ROUNDING_CONTEXT)


def test_golden_case_delivery_amount():
    """Hand-derived golden case (required test a).

    Exposure = 100000.75, Threshold = 0, CSB = 15000.25, MTA = 10000.00.
    raw = 100000.75 - 0 - 15000.25 = 85000.50
    85000.50 >= MTA(10000.00) => DELIVER
    round-half-up(85000.50) to whole USD = 85001 (a half rounds up)
    => call_amount = 85001.00 USD, call_status = DELIVER
    """
    pack = build_method_pack_001()
    result = run_method_pack_001(
        method_pack=pack,
        exposure=_m("100000.75"),
        credit_support_balance=_m("15000.25"),
    )
    assert result.call_status is CallStatus.DELIVER
    assert str(result.call_amount.amount) == "85001"
    assert result.call_amount.currency == "USD"


def test_golden_case_transfer_parties_explicit_identity():
    transferor, transferee = resolve_transfer_parties(
        call_status=CallStatus.DELIVER,
        calculation_entity_id="SIM-ENTITY-CALC",
        counterparty_entity_id="SIM-ENTITY-CP",
    )
    # DELIVER: the counterparty delivers to the calculation party.
    assert transferor == "SIM-ENTITY-CP"
    assert transferee == "SIM-ENTITY-CALC"


def test_mta_boundary_just_below_no_transfer():
    pack = build_method_pack_001()
    # raw = 9999.99 (MTA - 0.01) => below MTA => NO_TRANSFER
    result = run_method_pack_001(method_pack=pack, exposure=_m("9999.99"), credit_support_balance=_m("0"))
    assert result.call_status is CallStatus.NO_TRANSFER
    assert result.call_amount.amount == 0


def test_mta_boundary_exactly_at_mta_triggers_call():
    pack = build_method_pack_001()
    # raw = 10000.00 (== MTA) => clears the gate (this build's explicit,
    # documented inclusive-boundary convention) => DELIVER
    result = run_method_pack_001(method_pack=pack, exposure=_m("10000.00"), credit_support_balance=_m("0"))
    assert result.call_status is CallStatus.DELIVER
    assert str(result.call_amount.amount) == "10000"


def test_mta_boundary_just_above_mta_triggers_call():
    pack = build_method_pack_001()
    # raw = 10000.01 (MTA + 0.01) => DELIVER; round-half-up(10000.01) to
    # whole USD rounds down to 10000 since .01 < .5 - demonstrates that the
    # MTA gate and the final rounding are two separate, ordered steps.
    result = run_method_pack_001(method_pack=pack, exposure=_m("10000.01"), credit_support_balance=_m("0"))
    assert result.call_status is CallStatus.DELIVER
    assert str(result.call_amount.amount) == "10000"


def test_mta_before_rounding_order_matters():
    """If rounding were applied before the MTA gate, 9999.60 would round to
    10000 and (wrongly) clear the MTA gate. MTA-before-rounding means the
    *raw* 9999.60 is compared to MTA first, so this correctly yields
    NO_TRANSFER."""
    pack = build_method_pack_001()
    result = run_method_pack_001(method_pack=pack, exposure=_m("9999.60"), credit_support_balance=_m("0"))
    assert result.call_status is CallStatus.NO_TRANSFER


def test_credit_support_balance_flips_delivery_to_no_transfer():
    """Required test (c): an existing CreditSupportBalance flips the call
    from Delivery to NO_TRANSFER.

    Without CSB: raw = 50000.00 - 0 - 0 = 50000.00 >= MTA => DELIVER 50000.
    With CSB = 45000.00: raw = 50000.00 - 0 - 45000.00 = 5000.00 < MTA(10000)
    => NO_TRANSFER.
    """
    pack = build_method_pack_001()
    without_csb = run_method_pack_001(method_pack=pack, exposure=_m("50000.00"), credit_support_balance=_m("0"))
    assert without_csb.call_status is CallStatus.DELIVER
    assert str(without_csb.call_amount.amount) == "50000"

    with_csb = run_method_pack_001(method_pack=pack, exposure=_m("50000.00"), credit_support_balance=_m("45000.00"))
    assert with_csb.call_status is CallStatus.NO_TRANSFER
    assert with_csb.call_amount.amount == 0


def test_return_direction_for_negative_exposure_beyond_mta():
    pack = build_method_pack_001()
    # raw = 0 - 0 - 20000.00 = -20000.00; magnitude 20000 >= MTA => RETURN
    result = run_method_pack_001(method_pack=pack, exposure=_m("0"), credit_support_balance=_m("20000.00"))
    assert result.call_status is CallStatus.RETURN
    assert str(result.call_amount.amount) == "20000"
    transferor, transferee = resolve_transfer_parties(
        call_status=result.call_status,
        calculation_entity_id="SIM-ENTITY-CALC",
        counterparty_entity_id="SIM-ENTITY-CP",
    )
    # RETURN: the calculation party returns excess collateral to the counterparty.
    assert transferor == "SIM-ENTITY-CALC"
    assert transferee == "SIM-ENTITY-CP"

"""Reconciliation, break classification, and undisputed-amount tests, per
plan Sections 11.3 and 11.5."""

from __future__ import annotations

import datetime as dt

from riskos.core.constants import METHOD_PACK_001_ROUNDING_CONTEXT
from riskos.core.reconciliation import (
    PartyCallView,
    build_reconciliation_case,
    classify_break,
    determine_dispute_case,
    determine_undisputed_amount,
    reconcile_calls,
)
from riskos.domain.enums import (
    BreakType,
    CallStatus,
    DisputeAgreementCase,
    ReconciliationStatus,
    UndisputedDeterminationStatus,
)
from money_types import Money


def _m(amount: str) -> Money:
    return Money(amount=amount, currency="USD", rounding_context=METHOD_PACK_001_ROUNDING_CONTEXT)


def test_matched_calls_produce_matched_status_and_determined_undisputed_amount():
    calc = PartyCallView(party_label="CALC", call_status=CallStatus.DELIVER, call_amount=_m("45536.00"))
    cp = PartyCallView(party_label="CP", call_status=CallStatus.DELIVER, call_amount=_m("45536.00"))
    status, breaks = reconcile_calls(calc, cp)
    assert status is ReconciliationStatus.MATCHED
    assert breaks == ()
    dispute_case = determine_dispute_case(calc, cp)
    assert dispute_case is DisputeAgreementCase.BOTH_AGREE_DIRECTION_AND_AMOUNT
    undisputed_status, undisputed_amount = determine_undisputed_amount(
        dispute_case=dispute_case,
        calculation_party_amount=calc.call_amount,
        counterparty_amount=cp.call_amount,
        lower_common_amount_authorized=False,
    )
    assert undisputed_status is UndisputedDeterminationStatus.DETERMINED
    assert undisputed_amount is not None
    assert str(undisputed_amount.amount) == "45536.00"


def test_break_classified_as_mtm_when_only_exposure_differs():
    """Required test (d): a reconciliation break, classified correctly. Both
    sides agree on trade population count and collateral state; only the
    exposure (and hence the resulting call amount) differs, so the honest
    classification is MTM."""

    calc = PartyCallView(
        party_label="CALC",
        call_status=CallStatus.DELIVER,
        call_amount=_m("45536.00"),
        exposure=_m("100536.00"),
        credit_support_balance=_m("55000.00"),
        trade_population_count=5,
    )
    cp = PartyCallView(
        party_label="CP",
        call_status=CallStatus.DELIVER,
        call_amount=_m("40000.00"),
        exposure=_m("95000.00"),
        credit_support_balance=_m("55000.00"),
        trade_population_count=5,
    )
    status, breaks = reconcile_calls(calc, cp)
    assert status is ReconciliationStatus.BREAK
    assert len(breaks) == 1
    assert breaks[0].break_type is BreakType.MTM


def test_break_classified_as_trade_population_when_counts_differ():
    calc = PartyCallView(
        party_label="CALC", call_status=CallStatus.DELIVER, call_amount=_m("45536.00"), trade_population_count=5
    )
    cp = PartyCallView(
        party_label="CP", call_status=CallStatus.DELIVER, call_amount=_m("40000.00"), trade_population_count=4
    )
    _, breaks = reconcile_calls(calc, cp)
    assert breaks[0].break_type is BreakType.TRADE_POPULATION


def test_break_classified_as_collateral_state_when_only_csb_differs():
    calc = PartyCallView(
        party_label="CALC",
        call_status=CallStatus.DELIVER,
        call_amount=_m("45536.00"),
        exposure=_m("100536.00"),
        credit_support_balance=_m("55000.00"),
    )
    cp = PartyCallView(
        party_label="CP",
        call_status=CallStatus.DELIVER,
        call_amount=_m("40536.00"),
        exposure=_m("100536.00"),
        credit_support_balance=_m("60000.00"),
    )
    _, breaks = reconcile_calls(calc, cp)
    assert breaks[0].break_type is BreakType.COLLATERAL_STATE


def test_break_classified_as_unclassified_when_no_structured_cause_matches():
    """Never force an unexplained difference into a plausible category
    (plan 11.3): with no population/exposure/CSB/as_of signal to explain a
    same-direction, materially-different amount, the honest answer is
    UNCLASSIFIED."""

    calc = PartyCallView(party_label="CALC", call_status=CallStatus.DELIVER, call_amount=_m("45536.00"))
    cp = PartyCallView(party_label="CP", call_status=CallStatus.DELIVER, call_amount=_m("12000.00"))
    _, breaks = reconcile_calls(calc, cp)
    assert breaks[0].break_type is BreakType.UNCLASSIFIED


def test_opposite_direction_dispute_returns_undisputed_not_determined():
    """Required test (e): parties claim opposite directions."""

    calc = PartyCallView(party_label="CALC", call_status=CallStatus.DELIVER, call_amount=_m("45536.00"))
    # From the counterparty's own perspective they also claim DELIVER, but
    # since this is symmetric-bilateral and both are non-NO_TRANSFER with
    # differing status semantics from each party's own vantage, model the
    # opposite-direction case explicitly as RETURN on the counterparty side.
    cp = PartyCallView(party_label="CP", call_status=CallStatus.RETURN, call_amount=_m("45536.00"))
    dispute_case = determine_dispute_case(calc, cp)
    assert dispute_case is DisputeAgreementCase.OPPOSITE_DIRECTION
    status, amount = determine_undisputed_amount(
        dispute_case=dispute_case,
        calculation_party_amount=calc.call_amount,
        counterparty_amount=cp.call_amount,
        lower_common_amount_authorized=False,
    )
    assert status is UndisputedDeterminationStatus.UNDISPUTED_NOT_DETERMINED
    assert amount is None


def test_one_party_not_responded_returns_undisputed_not_determined():
    calc = PartyCallView(party_label="CALC", call_status=CallStatus.DELIVER, call_amount=_m("45536.00"))
    cp = PartyCallView(
        party_label="CP", call_status=CallStatus.NO_TRANSFER, call_amount=_m("0"), responded=False
    )
    dispute_case = determine_dispute_case(calc, cp)
    assert dispute_case is DisputeAgreementCase.ONE_PARTY_NOT_RESPONDED
    status, amount = determine_undisputed_amount(
        dispute_case=dispute_case,
        calculation_party_amount=calc.call_amount,
        counterparty_amount=cp.call_amount,
        lower_common_amount_authorized=False,
    )
    assert status is UndisputedDeterminationStatus.UNDISPUTED_NOT_DETERMINED
    assert amount is None


def test_agree_direction_differ_amount_without_authorization_is_not_determined():
    """METHOD-PACK-001 does not authorize the "lower common amount"
    exception (`lower_common_amount_authorized=False`): even though both
    parties agree on direction, a differing amount must never be silently
    resolved to the lower figure."""

    calc = PartyCallView(party_label="CALC", call_status=CallStatus.DELIVER, call_amount=_m("45536.00"))
    cp = PartyCallView(party_label="CP", call_status=CallStatus.DELIVER, call_amount=_m("40000.00"))
    dispute_case = determine_dispute_case(calc, cp)
    assert dispute_case is DisputeAgreementCase.AGREE_DIRECTION_DIFFER_AMOUNT
    status, amount = determine_undisputed_amount(
        dispute_case=dispute_case,
        calculation_party_amount=calc.call_amount,
        counterparty_amount=cp.call_amount,
        lower_common_amount_authorized=False,
    )
    assert status is UndisputedDeterminationStatus.UNDISPUTED_NOT_DETERMINED
    assert amount is None


def test_agree_direction_differ_amount_with_explicit_authorization_uses_lower_common_amount():
    """The "lower common amount" exception exists, but only fires when the
    caller explicitly flags authorization (never true for this slice's
    workflow, which always passes METHOD-PACK-001's False)."""

    calc = PartyCallView(party_label="CALC", call_status=CallStatus.DELIVER, call_amount=_m("45536.00"))
    cp = PartyCallView(party_label="CP", call_status=CallStatus.DELIVER, call_amount=_m("40000.00"))
    dispute_case = determine_dispute_case(calc, cp)
    status, amount = determine_undisputed_amount(
        dispute_case=dispute_case,
        calculation_party_amount=calc.call_amount,
        counterparty_amount=cp.call_amount,
        lower_common_amount_authorized=True,
    )
    assert status is UndisputedDeterminationStatus.DETERMINED
    assert amount is not None
    assert str(amount.amount) == "40000.00"


def test_collateral_or_population_disagreement_case():
    calc = PartyCallView(
        party_label="CALC",
        call_status=CallStatus.DELIVER,
        call_amount=_m("45536.00"),
        credit_support_balance=_m("55000.00"),
    )
    cp = PartyCallView(
        party_label="CP",
        call_status=CallStatus.DELIVER,
        call_amount=_m("45536.00"),
        credit_support_balance=_m("10000.00"),
    )
    dispute_case = determine_dispute_case(calc, cp)
    assert dispute_case is DisputeAgreementCase.COLLATERAL_OR_POPULATION_DISAGREEMENT


def test_partial_acknowledgement_case():
    calc = PartyCallView(party_label="CALC", call_status=CallStatus.DELIVER, call_amount=_m("45536.00"))
    cp = PartyCallView(
        party_label="CP",
        call_status=CallStatus.DELIVER,
        call_amount=_m("45536.00"),
        acknowledged_amount=_m("20000.00"),
    )
    dispute_case = determine_dispute_case(calc, cp)
    assert dispute_case is DisputeAgreementCase.PARTIAL_ACKNOWLEDGEMENT


def test_build_reconciliation_case_end_to_end():
    calc = PartyCallView(
        party_label="CALC",
        call_status=CallStatus.DELIVER,
        call_amount=_m("45536.00"),
        exposure=_m("100536.00"),
        credit_support_balance=_m("55000.00"),
        trade_population_count=5,
        as_of=dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc),
    )
    cp = PartyCallView(
        party_label="CP",
        call_status=CallStatus.DELIVER,
        call_amount=_m("45536.00"),
        exposure=_m("100536.00"),
        credit_support_balance=_m("55000.00"),
        trade_population_count=5,
        as_of=dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc),
    )
    case = build_reconciliation_case(
        case_id="RECON-TEST-0001",
        run_id="RUN-0001",
        calculation_party=calc,
        counterparty=cp,
        lower_common_amount_authorized=False,
    )
    assert case.reconciliation_status is ReconciliationStatus.MATCHED
    assert case.undisputed_status is UndisputedDeterminationStatus.DETERMINED
    assert case.undisputed_amount is not None
    assert str(case.undisputed_amount.amount) == "45536.00"

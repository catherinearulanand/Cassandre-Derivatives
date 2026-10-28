"""RSK-X01 native valuation challenger tests (plan Section 11.6,
`docs/methods/RSK-X01-challenger-provisional.md`, `DEC-RSK-02`).

Golden cases below compute the expected PV by hand (in each test's
docstring) using the exact formulas documented in
`riskos.core.challenger_pricing`, then assert the code reproduces that
figure to the cent. The final test in this module proves the independence
claim: running a `CHALLENGER` valuation never alters, gates, or blocks an
existing `MARGIN_CALL`-purpose `CalculationRun`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import riskos_fixtures as fx
from audit_protocol.keys import generate_keypair
from money_types import Money

from riskos.adapters.in_memory import InMemoryRiskOSRepository
from riskos.application.service import RiskOSService
from riskos.core.challenger_pricing import price_fx_forward, price_interest_rate_swap
from riskos.core.challenger_workflow import execute_challenger_run
from riskos.core.constants import TRADE_MTM_ROUNDING_CONTEXT
from riskos.domain.entities import (
    ChallengerRun,
    CurvePoint,
    DiscountCurve,
    FxForwardTerms,
    FxSpotRate,
    InterestRateSwapTerms,
    MarketSnapshot,
    TradeMTM,
)
from riskos.domain.enums import (
    Actionability,
    CalculationPurpose,
    CalculationStatus,
    DataQualityStatus,
    ReconciliationStatus,
    TradeLifecycleState,
    TradePopulationDisposition,
    ValidationStatus,
)

FIXED_NOW = dt.datetime(2026, 1, 5, 12, 0, 0, tzinfo=dt.timezone.utc)


def _flat_curve(currency: str, zero_rate: str) -> DiscountCurve:
    return DiscountCurve(currency=currency, points=(CurvePoint(tenor_years=Decimal("1"), zero_rate=Decimal(zero_rate)),))


def _irs_market_snapshot(*, currency: str = "USD", zero_rate: str = "0.03") -> MarketSnapshot:
    return MarketSnapshot(
        snapshot_id="SIM-MKT-IRS-0001",
        as_of=dt.date(2026, 1, 1),
        calendars=("SIM.NONE",),
        discount_curves=(_flat_curve(currency, zero_rate),),
        fx_spot_rates=(),
    )


def _fx_market_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        snapshot_id="SIM-MKT-FX-0001",
        as_of=dt.date(2026, 1, 1),
        calendars=("SIM.NONE",),
        discount_curves=(_flat_curve("EUR", "0.02"), _flat_curve("USD", "0.05")),
        fx_spot_rates=(FxSpotRate(base_currency="EUR", quote_currency="USD", rate=Decimal("1.10")),),
    )


def _irs_terms(*, calculation_party_pays_fixed: bool) -> InterestRateSwapTerms:
    return InterestRateSwapTerms(
        trade_id="SIM-CHALLENGER-IRS-0001",
        calculation_entity_id="SIM-ENTITY-CALC-0001",
        counterparty_entity_id="SIM-ENTITY-CPTY-0001",
        netting_set_id="SIM-NETSET-CHALLENGER-0001",
        currency="USD",
        notional=Decimal("1000000"),
        fixed_rate=Decimal("0.04"),
        calculation_party_pays_fixed=calculation_party_pays_fixed,
        effective_date=dt.date(2026, 1, 1),
        maturity_date=dt.date(2028, 1, 1),
        payment_frequency_months=12,
    )


def _fx_forward_terms() -> FxForwardTerms:
    return FxForwardTerms(
        trade_id="SIM-CHALLENGER-FX-0001",
        calculation_entity_id="SIM-ENTITY-CALC-0001",
        counterparty_entity_id="SIM-ENTITY-CPTY-0001",
        netting_set_id="SIM-NETSET-CHALLENGER-0001",
        base_currency="EUR",
        quote_currency="USD",
        notional_base=Decimal("1000000"),
        contract_rate=Decimal("1.12"),
        calculation_party_buys_base=True,
        value_date=dt.date(2026, 5, 27),
    )


# ---------------------------------------------------------------------------
# Golden case 1: fixed-vs-floating IRS, flat 3% discount curve, receive fixed.
#
# notional = 1,000,000 USD; fixed_rate = 4%; annual payments at t=1, t=2
# years (2026-01-01 -> 2027-01-01 -> 2028-01-01, each leg exactly 365 days /
# 365 = 1.0 year under ACT/365F, so accrual_i = 1.0 for both periods).
#
# DF(1) = 1 / (1 + 0.03*1) = 1/1.03            = 0.9708737864077669...
# DF(2) = 1 / (1 + 0.03*2) = 1/1.06            = 0.9433962264150943...
#
# PV_fixed = 1,000,000 * 0.04 * (DF(1) + DF(2))
#          = 40,000 * 1.9142700128228612...    = 76,570.80051291445...
# PV_float = 1,000,000 * (1 - DF(2))            = 56,603.77358490566...
#
# receive-fixed NPV = PV_fixed - PV_float = 19,967.02692800885...
#   -> round-half-up to the cent: 19,967.03 USD
# pay-fixed NPV      = PV_float - PV_fixed = -19,967.02692800885...
#   -> round-half-up to the cent: -19,967.03 USD
# ---------------------------------------------------------------------------


def test_irs_golden_case_receive_fixed_pv_decimal():
    terms = _irs_terms(calculation_party_pays_fixed=False)
    snapshot = _irs_market_snapshot()
    pv = price_interest_rate_swap(terms, snapshot, as_of=dt.date(2026, 1, 1))
    assert pv.quantize(Decimal("0.01")) == Decimal("19967.03")


def test_irs_golden_case_pay_fixed_pv_is_the_negation():
    terms = _irs_terms(calculation_party_pays_fixed=True)
    snapshot = _irs_market_snapshot()
    pv = price_interest_rate_swap(terms, snapshot, as_of=dt.date(2026, 1, 1))
    assert pv.quantize(Decimal("0.01")) == Decimal("-19967.03")


def test_irs_golden_case_via_challenger_run_rounds_to_money():
    snapshot = _irs_market_snapshot()
    run = execute_challenger_run(
        run_id="SIM-CHALLENGER-RUN-IRS-GOLDEN",
        netting_set_id="SIM-NETSET-CHALLENGER-0001",
        market_snapshot=snapshot,
        as_of=dt.date(2026, 1, 1),
        now=FIXED_NOW,
        irs_trades=(_irs_terms(calculation_party_pays_fixed=False),),
    )
    assert run.calculation_status is CalculationStatus.COMPLETE
    assert len(run.comparisons) == 1
    comparison = run.comparisons[0]
    assert comparison.challenger_pv is not None
    assert comparison.challenger_pv.amount == Decimal("19967.03")
    assert comparison.challenger_pv.currency == "USD"
    # no incumbent TradeMTM supplied for this trade_id
    assert comparison.comparison_status is ReconciliationStatus.NOT_AVAILABLE


# ---------------------------------------------------------------------------
# Golden case 2: deliverable FX forward, covered interest rate parity.
#
# base=EUR, quote=USD, spot = 1.10 USD/EUR, r_base(EUR) = 2%, r_quote(USD) =
# 5%, t = 146 days / 365 = 0.4 years exactly (2026-01-01 -> 2026-05-27),
# notional_base = 1,000,000 EUR, contract_rate = 1.12, long EUR forward.
#
# forward_rate = 1.10 * (1 + 0.05*0.4) / (1 + 0.02*0.4)
#              = 1.10 * 1.02 / 1.008 = 1.11309523809523809...
# DF_quote(0.4) = 1 / 1.02 = 0.98039215686274509...
# PV = 1,000,000 * (1.11309523809... - 1.12) * 0.98039215686...
#    = 1,000,000 * (-0.00690476190...) * 0.98039215686...
#    = -6,769.37441643324...
#   -> round-half-up to the cent: -6,769.37 USD
# ---------------------------------------------------------------------------


def test_fx_forward_golden_case_pv_decimal():
    terms = _fx_forward_terms()
    snapshot = _fx_market_snapshot()
    pv = price_fx_forward(terms, snapshot, as_of=dt.date(2026, 1, 1))
    assert pv.quantize(Decimal("0.01")) == Decimal("-6769.37")


def test_fx_forward_golden_case_short_position_is_the_negation():
    long_terms = _fx_forward_terms()
    short_terms = FxForwardTerms(
        **{**long_terms.__dict__, "calculation_party_buys_base": False, "trade_id": "SIM-CHALLENGER-FX-0002"}
    )
    snapshot = _fx_market_snapshot()
    long_pv = price_fx_forward(long_terms, snapshot, as_of=dt.date(2026, 1, 1))
    short_pv = price_fx_forward(short_terms, snapshot, as_of=dt.date(2026, 1, 1))
    assert short_pv == -long_pv


# ---------------------------------------------------------------------------
# Comparison-status tests (DEC-RSK-02 tolerance convention).
# ---------------------------------------------------------------------------


def _incumbent(trade_id: str, mtm_amount: str, currency: str = "USD") -> TradeMTM:
    return TradeMTM(
        trade_id=trade_id,
        calculation_entity_id="SIM-ENTITY-CALC-0001",
        counterparty_entity_id="SIM-ENTITY-CPTY-0001",
        netting_set_id="SIM-NETSET-CHALLENGER-0001",
        product_family="IRS",
        lifecycle_state=TradeLifecycleState.ACTIVE,
        disposition=TradePopulationDisposition.ACCEPTED,
        mtm=Money(amount=mtm_amount, currency=currency, rounding_context=TRADE_MTM_ROUNDING_CONTEXT),
    )


def test_challenger_comparison_matched_within_tolerance_and_break_and_not_available():
    # Reuse the IRS golden case (challenger PV = 19,967.03 USD) against four
    # different incumbent MTMs to exercise every DEC-RSK-02 comparison
    # status in one run.
    matched_terms = _irs_terms(calculation_party_pays_fixed=False)
    within_tolerance_terms = InterestRateSwapTerms(
        **{**matched_terms.__dict__, "trade_id": "SIM-CHALLENGER-IRS-WITHIN-TOL"}
    )
    break_terms = InterestRateSwapTerms(**{**matched_terms.__dict__, "trade_id": "SIM-CHALLENGER-IRS-BREAK"})
    no_incumbent_terms = InterestRateSwapTerms(
        **{**matched_terms.__dict__, "trade_id": "SIM-CHALLENGER-IRS-NO-INCUMBENT"}
    )

    incumbents = (
        _incumbent(matched_terms.trade_id, "19967.03"),  # exact match
        _incumbent(within_tolerance_terms.trade_id, "19957.03"),  # diff 10.00 <= tolerance 25.00
        _incumbent(break_terms.trade_id, "19900.00"),  # diff 67.03 > tolerance 25.00
        # no_incumbent_terms.trade_id deliberately has no incumbent TradeMTM
    )

    run = execute_challenger_run(
        run_id="SIM-CHALLENGER-RUN-COMPARISON",
        netting_set_id="SIM-NETSET-CHALLENGER-0001",
        market_snapshot=_irs_market_snapshot(),
        as_of=dt.date(2026, 1, 1),
        now=FIXED_NOW,
        irs_trades=(matched_terms, within_tolerance_terms, break_terms, no_incumbent_terms),
        incumbent_trades=incumbents,
    )

    by_trade_id = {c.trade_id: c for c in run.comparisons}
    assert by_trade_id[matched_terms.trade_id].comparison_status is ReconciliationStatus.MATCHED
    assert by_trade_id[within_tolerance_terms.trade_id].comparison_status is ReconciliationStatus.WITHIN_TOLERANCE
    assert by_trade_id[break_terms.trade_id].comparison_status is ReconciliationStatus.BREAK
    assert by_trade_id[no_incumbent_terms.trade_id].comparison_status is ReconciliationStatus.NOT_AVAILABLE

    # Run-level status takes the worst outcome: BREAK beats everything else.
    assert run.reconciliation_status is ReconciliationStatus.BREAK
    # A challenger run's actionability is always indicative, never actionable,
    # regardless of how the comparisons came out.
    assert run.actionability is Actionability.INDICATIVE_ONLY
    assert run.validation_status is ValidationStatus.RESTRICTED_USE


def test_challenger_run_with_no_supported_trades_is_failed_not_silently_empty():
    run = execute_challenger_run(
        run_id="SIM-CHALLENGER-RUN-EMPTY",
        netting_set_id="SIM-NETSET-CHALLENGER-0001",
        market_snapshot=_irs_market_snapshot(),
        as_of=dt.date(2026, 1, 1),
        now=FIXED_NOW,
    )
    assert run.calculation_status is CalculationStatus.FAILED
    assert run.failure_reason is not None
    assert run.actionability is Actionability.REJECTED


def test_challenger_cannot_price_a_currency_with_no_curve_is_not_available_not_a_crash():
    terms = _irs_terms(calculation_party_pays_fixed=False)
    eur_only_snapshot = _irs_market_snapshot(currency="EUR", zero_rate="0.03")  # no USD curve
    run = execute_challenger_run(
        run_id="SIM-CHALLENGER-RUN-MISSING-CURVE",
        netting_set_id="SIM-NETSET-CHALLENGER-0001",
        market_snapshot=eur_only_snapshot,
        as_of=dt.date(2026, 1, 1),
        now=FIXED_NOW,
        irs_trades=(terms,),
    )
    assert run.calculation_status is CalculationStatus.COMPLETE
    comparison = run.comparisons[0]
    assert comparison.challenger_pv is None
    assert comparison.comparison_status is ReconciliationStatus.NOT_AVAILABLE
    assert comparison.note is not None


# ---------------------------------------------------------------------------
# Independence proof: a CHALLENGER run must never alter, gate, or block an
# existing MARGIN_CALL run.
# ---------------------------------------------------------------------------


def test_challenger_run_does_not_affect_margin_call_run():
    repo = InMemoryRiskOSRepository()
    keypair = generate_keypair("riskos-challenger-test-key")
    service = RiskOSService(repo, keypair=keypair)

    scenario = fx.build_golden_scenario()
    now = scenario.now
    service.register_legal_entity(scenario.calculation_entity, now=now)
    service.register_legal_entity(scenario.counterparty_entity, now=now)
    service.register_netting_set(scenario.netting_set, now=now)
    service.approve_agreement_terms(scenario.agreement_terms, now=now)
    service.ingest_portfolio_snapshot(scenario.portfolio, now=now)
    service.ingest_collateral_state(scenario.collateral, now=now)

    margin_call_run = service.run_calculation(
        run_id="SIM-RUN-MARGIN-CALL-BEFORE-CHALLENGER",
        purpose=CalculationPurpose.MARGIN_CALL,
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        agreement_id=scenario.agreement_terms.agreement_id,
        portfolio_snapshot_id=scenario.portfolio.snapshot_id,
        collateral_package_id=scenario.collateral.package_id,
        manifest=scenario.manifest,
        counterparty_call=scenario.counterparty_call,
        now=now,
    )
    assert margin_call_run.calculation_status is CalculationStatus.COMPLETE

    # Snapshot the persisted state before running the challenger.
    before = service.get_calculation_run(margin_call_run.run_id)
    before_reconciliation = service.get_reconciliation_case(before.reconciliation_case_id)

    # Now run an entirely separate CHALLENGER valuation against the same
    # netting set and the same already-ingested portfolio snapshot.
    market_snapshot = _irs_market_snapshot()
    service.ingest_market_snapshot(market_snapshot, now=now)
    challenger_run = service.run_challenger_valuation(
        run_id="SIM-RUN-CHALLENGER-0001",
        netting_set_id=scenario.netting_set.netting_set_id,
        market_snapshot_id=market_snapshot.snapshot_id,
        as_of=market_snapshot.as_of,
        now=now,
        portfolio_snapshot_id=scenario.portfolio.snapshot_id,
        irs_trades=(_irs_terms(calculation_party_pays_fixed=False),),
    )
    assert isinstance(challenger_run, ChallengerRun)
    assert challenger_run.calculation_status is CalculationStatus.COMPLETE
    assert challenger_run.actionability is Actionability.INDICATIVE_ONLY

    # The MARGIN_CALL run, re-fetched by the same run_id, is byte-for-byte
    # unchanged: same calculation/data-quality/reconciliation/validation
    # statuses, same waterfall, same reconciliation case.
    after = service.get_calculation_run(margin_call_run.run_id)
    assert after == before
    after_reconciliation = service.get_reconciliation_case(after.reconciliation_case_id)
    assert after_reconciliation == before_reconciliation

    # The two runs are not just equal but stored under disjoint identities:
    # a CHALLENGER run_id never collides with, and cannot be fetched as, a
    # MARGIN_CALL CalculationRun (and vice versa).
    assert challenger_run.run_id != margin_call_run.run_id
    try:
        service.get_calculation_run(challenger_run.run_id)
        assert False, "a challenger run_id must not resolve as a CalculationRun"
    except Exception as exc:  # RiskOSNotFoundError
        assert "unknown calculation run" in str(exc)

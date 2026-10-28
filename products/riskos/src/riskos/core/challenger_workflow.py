"""RSK-X01 native valuation challenger workflow (plan Section 11.6):
composes the pure pricing math in `riskos.core.challenger_pricing` into a
`ChallengerRun`.

Independence from the authoritative workflow (the brief's core requirement):
this module does not import `riskos.core.workflow`, `riskos.core.
method_pack`, `riskos.core.exposure`, `riskos.core.gates`, `riskos.core.
netting`, or `riskos.core.population`, and none of those modules import this
one. A `CHALLENGER`-purpose run built here shares only the low-level
`riskos.core.challenger_pricing` curve utilities — never a repository key,
a status field, or a code path with `execute_margin_call`. See
`products/riskos/tests/test_challenger.py::
test_challenger_run_does_not_affect_margin_call_run` for the proof.

DEC-RSK-02 (provisional, `docs/methods/RSK-X01-challenger-provisional.md`):
challenger-vs-incumbent tolerance = the greater of
`CHALLENGER_TOLERANCE_ABS_FLOOR` (an absolute currency amount) or
`CHALLENGER_TOLERANCE_RELATIVE_BPS` of `abs(incumbent_pv)`. A difference
within that tolerance is `WITHIN_TOLERANCE`, not `MATCHED` (an exact match
is its own, stricter status); anything wider is `BREAK`. A trade with no
incumbent MTM to compare against is `NOT_AVAILABLE` — never silently
dropped from the comparison set.
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from money_types import Money

from riskos.core.challenger_pricing import price_fx_forward, price_interest_rate_swap
from riskos.core.constants import (
    CHALLENGER_ROUNDING_CONTEXT,
    CHALLENGER_TOLERANCE_ABS_FLOOR,
    CHALLENGER_TOLERANCE_RELATIVE_BPS,
    FRESHNESS_WINDOW_HOURS,
)
from riskos.domain.entities import (
    ChallengerComparison,
    ChallengerRun,
    FxForwardTerms,
    InterestRateSwapTerms,
    MarketSnapshot,
    TradeMTM,
)
from riskos.domain.enums import (
    Actionability,
    CalculationStatus,
    DataQualityStatus,
    ReconciliationStatus,
    ValidationStatus,
)


def _round_money(amount: Decimal, *, currency: str) -> Money:
    """The one explicit, documented rounding step from internal `Decimal`
    pricing arithmetic to a reportable `Money`: round-half-up to 2 decimal
    places. This module only handles 2-decimal-minor-unit currencies (USD/
    EUR in this scope's fixtures); see `docs/methods/RSK-X01-challenger-
    provisional.md`."""

    rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Money(amount=str(rounded), currency=currency, rounding_context=CHALLENGER_ROUNDING_CONTEXT)


def compute_tolerance(incumbent_pv: Money) -> Money:
    """DEC-RSK-02's tolerance convention, in the incumbent trade's own
    currency."""

    floor = CHALLENGER_TOLERANCE_ABS_FLOOR
    relative = abs(incumbent_pv.amount) * CHALLENGER_TOLERANCE_RELATIVE_BPS
    return _round_money(max(floor, relative), currency=incumbent_pv.currency)


def _classify(*, challenger_pv: Money, incumbent_pv: Money, tolerance: Money) -> ReconciliationStatus:
    if challenger_pv.currency != incumbent_pv.currency:
        # A currency mismatch is never a rounding artifact worth tolerating
        # away; it is always a break, per the same "never force an
        # unexplained difference into a plausible category" ethos as
        # `riskos.core.reconciliation.classify_break`.
        return ReconciliationStatus.BREAK
    if challenger_pv.amount == incumbent_pv.amount:
        return ReconciliationStatus.MATCHED
    if abs(challenger_pv.amount - incumbent_pv.amount) <= tolerance.amount:
        return ReconciliationStatus.WITHIN_TOLERANCE
    return ReconciliationStatus.BREAK


def _build_comparison(
    *, trade_id: str, product_family: str, challenger_pv: Money | None, incumbent: TradeMTM | None, note: str | None = None
) -> ChallengerComparison:
    if challenger_pv is None:
        return ChallengerComparison(
            trade_id=trade_id,
            product_family=product_family,
            challenger_pv=None,
            incumbent_pv=incumbent.mtm if incumbent is not None else None,
            difference=None,
            tolerance=None,
            comparison_status=ReconciliationStatus.NOT_AVAILABLE,
            note=note or "challenger could not price this trade",
        )
    if incumbent is None:
        return ChallengerComparison(
            trade_id=trade_id,
            product_family=product_family,
            challenger_pv=challenger_pv,
            incumbent_pv=None,
            difference=None,
            tolerance=None,
            comparison_status=ReconciliationStatus.NOT_AVAILABLE,
            note="no incumbent external-MTM trade found for this trade_id",
        )
    tolerance = compute_tolerance(incumbent.mtm)
    status = _classify(challenger_pv=challenger_pv, incumbent_pv=incumbent.mtm, tolerance=tolerance)
    difference = (
        Money(
            amount=str(challenger_pv.amount - incumbent.mtm.amount),
            currency=challenger_pv.currency,
            rounding_context=CHALLENGER_ROUNDING_CONTEXT,
        )
        if challenger_pv.currency == incumbent.mtm.currency
        else None
    )
    return ChallengerComparison(
        trade_id=trade_id,
        product_family=product_family,
        challenger_pv=challenger_pv,
        incumbent_pv=incumbent.mtm,
        difference=difference,
        tolerance=tolerance,
        comparison_status=status,
    )


def price_and_compare_irs(
    terms: InterestRateSwapTerms, *, snapshot: MarketSnapshot, as_of: dt.date, incumbent: TradeMTM | None
) -> ChallengerComparison:
    try:
        pv_decimal = price_interest_rate_swap(terms, snapshot, as_of)
    except ValueError as exc:
        return _build_comparison(trade_id=terms.trade_id, product_family="IRS", challenger_pv=None, incumbent=incumbent, note=str(exc))
    challenger_pv = _round_money(pv_decimal, currency=terms.currency)
    return _build_comparison(trade_id=terms.trade_id, product_family="IRS", challenger_pv=challenger_pv, incumbent=incumbent)


def price_and_compare_fx_forward(
    terms: FxForwardTerms, *, snapshot: MarketSnapshot, as_of: dt.date, incumbent: TradeMTM | None
) -> ChallengerComparison:
    try:
        pv_decimal = price_fx_forward(terms, snapshot, as_of)
    except ValueError as exc:
        return _build_comparison(
            trade_id=terms.trade_id, product_family="FX_FORWARD", challenger_pv=None, incumbent=incumbent, note=str(exc)
        )
    challenger_pv = _round_money(pv_decimal, currency=terms.quote_currency)
    return _build_comparison(trade_id=terms.trade_id, product_family="FX_FORWARD", challenger_pv=challenger_pv, incumbent=incumbent)


def _aggregate_reconciliation_status(comparisons: tuple[ChallengerComparison, ...]) -> ReconciliationStatus:
    statuses = {c.comparison_status for c in comparisons}
    if ReconciliationStatus.BREAK in statuses:
        return ReconciliationStatus.BREAK
    if ReconciliationStatus.WITHIN_TOLERANCE in statuses:
        return ReconciliationStatus.WITHIN_TOLERANCE
    if ReconciliationStatus.NOT_AVAILABLE in statuses:
        return ReconciliationStatus.NOT_AVAILABLE
    return ReconciliationStatus.MATCHED


def _limitations() -> tuple[str, ...]:
    return (
        "RSK-X01 native valuation challenger is PROVISIONAL and ADDITIVE (DEC-RSK-02): it never replaces, "
        "gates, or blocks the external-MTM-based MARGIN_CALL workflow.",
        "Single discount curve per currency only: no multi-curve/OIS discounting, no options, exotic "
        "payoffs, credit derivatives, or cross-currency swaps (plan Section 11.6).",
        "actionability is always INDICATIVE_ONLY for a CHALLENGER run: this result is a comparison signal, "
        "never an actionable margin call.",
    )


def execute_challenger_run(
    *,
    run_id: str,
    netting_set_id: str,
    market_snapshot: MarketSnapshot,
    as_of: dt.date,
    now: dt.datetime,
    irs_trades: tuple[InterestRateSwapTerms, ...] = (),
    fx_forward_trades: tuple[FxForwardTerms, ...] = (),
    incumbent_trades: tuple[TradeMTM, ...] = (),
) -> ChallengerRun:
    """Build one `CalculationPurpose.CHALLENGER` result. Pure function: no
    repository access, no clock read (`now` is passed in), no I/O — mirrors
    `riskos.core.workflow.execute_margin_call`'s own purity, but is a
    wholly separate call graph."""

    incumbent_by_id = {t.trade_id: t for t in incumbent_trades}

    comparisons: list[ChallengerComparison] = [
        price_and_compare_irs(irs, snapshot=market_snapshot, as_of=as_of, incumbent=incumbent_by_id.get(irs.trade_id))
        for irs in irs_trades
    ] + [
        price_and_compare_fx_forward(
            fwd, snapshot=market_snapshot, as_of=as_of, incumbent=incumbent_by_id.get(fwd.trade_id)
        )
        for fwd in fx_forward_trades
    ]

    if not comparisons:
        return ChallengerRun(
            run_id=run_id,
            netting_set_id=netting_set_id,
            market_snapshot_id=market_snapshot.snapshot_id,
            calculation_status=CalculationStatus.FAILED,
            data_quality_status=DataQualityStatus.INCOMPLETE,
            reconciliation_status=ReconciliationStatus.NOT_AVAILABLE,
            validation_status=ValidationStatus.UNVALIDATED,
            actionability=Actionability.REJECTED,
            comparisons=(),
            created_at=now,
            limitations=_limitations(),
            failure_reason="no supported (IRS or deliverable FX forward) trades were supplied to price",
        )

    snapshot_age_hours = (now - dt.datetime.combine(market_snapshot.as_of, dt.time.min, tzinfo=dt.timezone.utc)).total_seconds() / 3600
    data_quality_status = (
        DataQualityStatus.STALE if snapshot_age_hours > FRESHNESS_WINDOW_HOURS else DataQualityStatus.APPROVED
    )

    return ChallengerRun(
        run_id=run_id,
        netting_set_id=netting_set_id,
        market_snapshot_id=market_snapshot.snapshot_id,
        calculation_status=CalculationStatus.COMPLETE,
        data_quality_status=data_quality_status,
        reconciliation_status=_aggregate_reconciliation_status(tuple(comparisons)),
        validation_status=ValidationStatus.RESTRICTED_USE,
        actionability=Actionability.INDICATIVE_ONLY,
        comparisons=tuple(comparisons),
        created_at=now,
        limitations=_limitations(),
    )

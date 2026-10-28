"""Native valuation challenger pricing math (RSK-X01, plan Section 11.6).

STATUS: PROVISIONAL / ADDITIVE (DEC-RSK-02,
`docs/methods/RSK-X01-challenger-provisional.md`). This module is the
pricing/curve-utility layer shared only by the challenger workflow
(`riskos.core.challenger_workflow`). It never imports, and is never
imported by, `riskos.core.workflow` (the S1 authoritative MARGIN_CALL
workflow) or `riskos.core.method_pack` / `riskos.core.exposure` /
`riskos.core.gates` — the two calculation paths are independent all the
way down except for this shared low-level curve math, per the brief: "fully
independent code paths sharing only the pricing/curve utilities, not the
authoritative workflow."

Scope, per plan 11.6:
- IRS: explicit single-curve limitation — one flat/bootstrapped discount
  curve per currency, used for both discounting and floating-rate
  projection (no separate OIS/Libor-style multi-curve stack). No options,
  exotic payoffs, credit derivatives, or cross-currency swaps.
- FX forward: one deliverable forward, priced by covered interest rate
  parity, drawing on the same discount-curve utility.

Discounting convention (this module's explicit choice, since plan 11.6 does
not mandate one): simple-interest annualized zero rates, linearly
interpolated by tenor between a curve's defined points and flat-
extrapolated beyond the first/last point —

    discount_factor(t) = 1 / (1 + z(t) * t)

Chosen over compounded/continuous discounting because it is the simplest
convention that still hand-verifies exactly (see the golden cases in
`products/riskos/tests/test_challenger.py`), and it is exactly the formula
plan 11.6 gives verbatim for the FX forward's own discounting step — using
one convention everywhere keeps the "shared curve utility" claim literally
true rather than aspirational.

Money/rounding discipline: every function here operates on `Decimal` only
(notional, rates, discount factors, PVs) — never on `money_types.Money`.
Floats are avoided entirely (not just "fine to use", per the brief, since
Decimal costs nothing at this module's data volumes and removes any float/
Decimal drift risk in a money-adjacent calculation). The one explicit,
documented rounding step from Decimal to `Money` happens only in
`riskos.core.challenger_workflow`, at the boundary where a priced trade
becomes a reportable `ChallengerComparison` — never inside this module.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from riskos.domain.entities import (
    DiscountCurve,
    FxForwardTerms,
    InterestRateSwapTerms,
    MarketSnapshot,
)

_ONE = Decimal("1")


def year_fraction(start: dt.date, end: dt.date) -> Decimal:
    """ACT/365F — this module's one documented day-count convention (plan
    11.6's single-curve scope: no per-agreement day-count election). Never
    negative in this module's own usage (`end` is always on/after `start`
    at every call site below)."""

    return Decimal((end - start).days) / Decimal(365)


def zero_rate(curve: DiscountCurve, tenor_years: Decimal) -> Decimal:
    """Linear interpolation on (tenor, zero_rate); flat extrapolation
    beyond the first/last defined point. `curve.points` must be sorted
    ascending by `tenor_years` and non-empty — a single point is a flat
    curve, exactly the shape this module's golden-case tests use."""

    points = curve.points
    if not points:
        raise ValueError(f"discount curve {curve.currency!r} has no points")
    if tenor_years <= points[0].tenor_years:
        return points[0].zero_rate
    if tenor_years >= points[-1].tenor_years:
        return points[-1].zero_rate
    for left, right in zip(points, points[1:]):
        if left.tenor_years <= tenor_years <= right.tenor_years:
            if right.tenor_years == left.tenor_years:
                return left.zero_rate
            weight = (tenor_years - left.tenor_years) / (right.tenor_years - left.tenor_years)
            return left.zero_rate + weight * (right.zero_rate - left.zero_rate)
    raise ValueError(  # pragma: no cover - unreachable given the bounds checks above
        f"tenor {tenor_years} not resolvable on curve {curve.currency!r}"
    )


def discount_factor(curve: DiscountCurve, tenor_years: Decimal) -> Decimal:
    """`DF(t) = 1 / (1 + z(t) * t)` — see module docstring for why simple
    interest was chosen over compounding for this scope. `DF(0) = 1`."""

    if tenor_years <= 0:
        return _ONE
    z = zero_rate(curve, tenor_years)
    return _ONE / (_ONE + z * tenor_years)


def _curve_for(snapshot: MarketSnapshot, currency: str) -> DiscountCurve:
    for curve in snapshot.discount_curves:
        if curve.currency == currency:
            return curve
    raise ValueError(f"no discount curve for currency {currency!r} in snapshot {snapshot.snapshot_id!r}")


def fx_spot(snapshot: MarketSnapshot, base_currency: str, quote_currency: str) -> Decimal:
    for spot in snapshot.fx_spot_rates:
        if spot.base_currency == base_currency and spot.quote_currency == quote_currency:
            return spot.rate
    raise ValueError(
        f"no FX spot rate for {base_currency}/{quote_currency} in snapshot {snapshot.snapshot_id!r}"
    )


def _payment_schedule(effective: dt.date, maturity: dt.date, frequency_months: int) -> tuple[dt.date, ...]:
    """Payment dates from `effective` to `maturity`, stepping back from
    maturity in `frequency_months` increments. Short/long-stub handling is
    out of this scope's stated simplicity limitation: this module's fixtures
    and golden cases use only whole-period tenors that divide evenly, so
    the walk-back always lands exactly on `effective`."""

    dates: list[dt.date] = []
    cursor = maturity
    while cursor > effective:
        dates.append(cursor)
        year = cursor.year
        month = cursor.month - frequency_months
        while month <= 0:
            month += 12
            year -= 1
        cursor = cursor.replace(year=year, month=month)
    dates.reverse()
    return tuple(dates)


def price_interest_rate_swap(
    terms: InterestRateSwapTerms, snapshot: MarketSnapshot, as_of: dt.date
) -> Decimal:
    """Calculation-party-perspective PV, in `terms.currency`, as a `Decimal`
    (not yet `Money` — see module docstring).

    Chosen simplification (per the brief: "pick the simpler approach and
    document which you chose and why"): the floating leg is assumed to
    reprice to par at every reset. Under this module's single-curve
    convention (same curve for discounting and projection, no spread),
    that assumption collapses the floating leg's PV to the closed form

        PV_float = notional * (1 - DF(maturity))

    This holds exactly when `as_of` coincides with a reset date with no
    accrued stub — which is what every fixture and golden case in this
    module's test suite uses. A full forward-rate-projection floating leg
    (bootstrapping implied forward rates from the same curve period by
    period) was the more complex alternative and was not needed to prove
    the pricing math at this scope; it is not implemented.

    Fixed leg PV = sum over each remaining payment date `t_i` of
    `notional * fixed_rate * accrual_i * DF(t_i)`, `accrual_i` in ACT/365F.

    Sign convention: `calculation_party_pays_fixed=False` (calc party
    receives fixed) -> `PV = PV_fixed - PV_float`; `True` (calc party pays
    fixed) -> `PV = PV_float - PV_fixed`.
    """

    curve = _curve_for(snapshot, terms.currency)
    schedule = _payment_schedule(terms.effective_date, terms.maturity_date, terms.payment_frequency_months)
    remaining = tuple(d for d in schedule if d > as_of)
    if not remaining:
        return Decimal("0")

    pv_fixed = Decimal("0")
    previous = as_of
    for payment_date in remaining:
        accrual = year_fraction(previous, payment_date)
        t = year_fraction(as_of, payment_date)
        df = discount_factor(curve, t)
        pv_fixed += terms.notional * terms.fixed_rate * accrual * df
        previous = payment_date

    maturity_t = year_fraction(as_of, terms.maturity_date)
    df_maturity = discount_factor(curve, maturity_t)
    pv_float = terms.notional * (_ONE - df_maturity)

    if terms.calculation_party_pays_fixed:
        return pv_float - pv_fixed
    return pv_fixed - pv_float


def price_fx_forward(terms: FxForwardTerms, snapshot: MarketSnapshot, as_of: dt.date) -> Decimal:
    """Calculation-party-perspective PV, in `terms.quote_currency`, as a
    `Decimal` (not yet `Money`), by covered interest rate parity — plan
    11.6's stated formula, used verbatim:

        forward_rate = spot * (1 + r_quote * t) / (1 + r_base * t)
        PV = notional_base * (forward_rate - contract_rate) * DF_quote(t)

    `r_base`/`r_quote` are each currency's own discount-curve zero rate at
    tenor `t = year_fraction(as_of, value_date)` (the same simple-interest
    convention `discount_factor` uses, so the forward rate and the
    discounting step are internally consistent — the "shared curve
    utility" this module and `price_interest_rate_swap` both draw on).
    `calculation_party_buys_base=True` (long the base currency forward)
    takes the PV as computed; `False` (short) negates it.
    """

    t = year_fraction(as_of, terms.value_date)
    if t <= 0:
        return Decimal("0")
    base_curve = _curve_for(snapshot, terms.base_currency)
    quote_curve = _curve_for(snapshot, terms.quote_currency)
    r_base = zero_rate(base_curve, t)
    r_quote = zero_rate(quote_curve, t)
    spot = fx_spot(snapshot, terms.base_currency, terms.quote_currency)

    forward_rate = spot * (_ONE + r_quote * t) / (_ONE + r_base * t)
    df_quote = _ONE / (_ONE + r_quote * t)
    pv = terms.notional_base * (forward_rate - terms.contract_rate) * df_quote
    return pv if terms.calculation_party_buys_base else -pv

import datetime as dt

import pytest
from pydantic import ValidationError

from money_types import FXConversion, Money, MoneyError, Price, PriceBasis, Quantity, QuoteDirection


def test_money_rejects_float_amount():
    with pytest.raises(ValidationError):
        Money(amount=1.5, currency="USD", rounding_context="std")


def test_money_rejects_int_amount():
    with pytest.raises(ValidationError):
        Money(amount=5, currency="USD", rounding_context="std")


def test_money_accepts_decimal_string():
    m = Money(amount="1234.56", currency="USD", rounding_context="std")
    assert str(m.amount) == "1234.56"


def test_money_rejects_unsupported_currency():
    with pytest.raises(ValidationError):
        Money(amount="1.00", currency="ZZZ", rounding_context="std")


def test_money_rejects_scientific_notation():
    with pytest.raises(ValidationError):
        Money(amount="1e10", currency="USD", rounding_context="std")


def test_money_add_same_currency():
    a = Money(amount="100.00", currency="USD", rounding_context="std")
    b = Money(amount="50.25", currency="USD", rounding_context="std")
    assert str((a + b).amount) == "150.25"


def test_money_add_cross_currency_rejected():
    a = Money(amount="100.00", currency="USD", rounding_context="std")
    b = Money(amount="50.25", currency="EUR", rounding_context="std")
    with pytest.raises(MoneyError):
        a + b


def test_quantity_unit_mismatch_rejected():
    a = Quantity(amount="10", unit="UST_FACE")
    b = Quantity(amount="5", unit="CASH_USD")
    with pytest.raises(MoneyError):
        a - b


def test_price_requires_tz_aware_as_of():
    with pytest.raises((ValidationError, MoneyError)):
        Price(
            amount="99.5",
            quote_currency="USD",
            price_basis=PriceBasis.CLEAN,
            as_of=dt.datetime(2026, 1, 1),
        )


def test_fx_conversion_rejects_bad_hash():
    with pytest.raises(ValidationError):
        FXConversion(
            source_currency="EUR",
            target_currency="USD",
            quote_direction=QuoteDirection.TARGET_PER_SOURCE,
            rate="1.08",
            valuation_time=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            source_snapshot_hash="not-a-hash",
            rounding_stage="post_conversion",
            rounding_rule="round_half_up",
        )


def test_fx_conversion_converts_money():
    fx = FXConversion(
        source_currency="EUR",
        target_currency="USD",
        quote_direction=QuoteDirection.TARGET_PER_SOURCE,
        rate="1.08",
        valuation_time=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        source_snapshot_hash="a" * 64,
        rounding_stage="post_conversion",
        rounding_rule="round_half_up",
    )
    eur = Money(amount="100.00", currency="EUR", rounding_context="std")
    usd = fx.convert(eur)
    assert usd.currency == "USD"
    assert str(usd.amount) == "108.00"


def test_fx_conversion_currency_mismatch_rejected():
    fx = FXConversion(
        source_currency="EUR",
        target_currency="USD",
        quote_direction=QuoteDirection.TARGET_PER_SOURCE,
        rate="1.08",
        valuation_time=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        source_snapshot_hash="a" * 64,
        rounding_stage="post_conversion",
        rounding_rule="round_half_up",
    )
    usd = Money(amount="100.00", currency="USD", rounding_context="std")
    with pytest.raises(MoneyError):
        fx.convert(usd)

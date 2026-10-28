"""Money/Quantity/Price/FXConversion primitives per plan Section 8.2.

Authoritative money is never a JSON float. Every amount is constructed only
from a string; passing an int or float raises MoneyError immediately, at
construction time, not somewhere downstream in a calculation.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, field_validator

# ISO-4217 alphabetic currency codes actually used or plausibly used by the
# S1 synthetic corpus plus common majors. Not the complete ISO list, but a
# real validated set rather than "any three letters".
_ISO_4217 = {
    "USD", "EUR", "INR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SEK",
    "NOK", "DKK", "SGD", "HKD", "CNY", "MXN", "BRL", "ZAR", "KRW", "PLN",
}

# ISO-4217 minor units. Plan Section 8.2: "currency minor units" is explicit,
# per-currency metadata, never assumed uniformly as 2 decimal places.
_MINOR_UNITS = {
    "JPY": 0, "KRW": 0,
    "USD": 2, "EUR": 2, "INR": 2, "GBP": 2, "CHF": 2, "CAD": 2, "AUD": 2,
    "NZD": 2, "SEK": 2, "NOK": 2, "DKK": 2, "SGD": 2, "HKD": 2, "CNY": 2,
    "MXN": 2, "BRL": 2, "ZAR": 2, "PLN": 2,
}


class MoneyError(ValueError):
    pass


_DECIMAL_STRING_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _parse_decimal_string(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str):
        raise MoneyError(
            f"amount must be a decimal string, got {type(value).__name__}: {value!r}"
        )
    if not _DECIMAL_STRING_RE.match(value):
        raise MoneyError(f"amount {value!r} is not a plain decimal string (no exponents)")
    try:
        return Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards this
        raise MoneyError(f"amount {value!r} is not a valid decimal") from exc


DecimalFromString = Annotated[Decimal, BeforeValidator(_parse_decimal_string)]


def _validate_currency(value: str) -> str:
    if not isinstance(value, str) or value.upper() != value or value not in _ISO_4217:
        raise MoneyError(f"{value!r} is not a supported ISO-4217 currency code for this build")
    return value


class PriceBasis(str, Enum):
    CLEAN = "clean"
    DIRTY = "dirty"
    CASH = "cash"
    MARKET_VALUE = "market_value"


class QuoteDirection(str, Enum):
    SOURCE_PER_TARGET = "source_per_target"
    TARGET_PER_SOURCE = "target_per_source"


class Money(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: DecimalFromString
    currency: str
    rounding_context: str

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        return _validate_currency(v)

    def __add__(self, other: "Money") -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        if other.currency != self.currency:
            raise MoneyError(
                f"cannot add Money across currencies without an explicit FXConversion "
                f"({self.currency} + {other.currency})"
            )
        if other.rounding_context != self.rounding_context:
            raise MoneyError(
                "cannot add Money with different rounding_context without an explicit "
                "reconciliation step"
            )
        return Money(
            amount=str(self.amount + other.amount),
            currency=self.currency,
            rounding_context=self.rounding_context,
        )

    def __neg__(self) -> "Money":
        return Money(amount=str(-self.amount), currency=self.currency, rounding_context=self.rounding_context)


class Quantity(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: DecimalFromString
    unit: str

    def __add__(self, other: "Quantity") -> "Quantity":
        if not isinstance(other, Quantity):
            return NotImplemented
        if other.unit != self.unit:
            raise MoneyError(f"cannot add Quantity across units ({self.unit} + {other.unit})")
        return Quantity(amount=str(self.amount + other.amount), unit=self.unit)

    def __sub__(self, other: "Quantity") -> "Quantity":
        if not isinstance(other, Quantity):
            return NotImplemented
        if other.unit != self.unit:
            raise MoneyError(f"cannot subtract Quantity across units ({self.unit} - {other.unit})")
        return Quantity(amount=str(self.amount - other.amount), unit=self.unit)


class Price(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: DecimalFromString
    quote_currency: str
    price_basis: PriceBasis
    as_of: dt.datetime

    @field_validator("quote_currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        return _validate_currency(v)

    @field_validator("as_of")
    @classmethod
    def _as_of_utc(cls, v: dt.datetime) -> dt.datetime:
        if v.tzinfo is None:
            raise MoneyError("as_of must be timezone-aware (RFC3339 UTC)")
        return v.astimezone(dt.timezone.utc)


class FXConversion(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_currency: str
    target_currency: str
    quote_direction: QuoteDirection
    rate: DecimalFromString
    valuation_time: dt.datetime
    source_snapshot_hash: str
    rounding_stage: str
    rounding_rule: str

    @field_validator("source_currency", "target_currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        return _validate_currency(v)

    @field_validator("source_snapshot_hash")
    @classmethod
    def _sha256_hex(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise MoneyError("source_snapshot_hash must be a 64-hex-char sha256 digest")
        return v

    @field_validator("valuation_time")
    @classmethod
    def _valuation_time_utc(cls, v: dt.datetime) -> dt.datetime:
        if v.tzinfo is None:
            raise MoneyError("valuation_time must be timezone-aware (RFC3339 UTC)")
        return v.astimezone(dt.timezone.utc)

    def convert(self, money: Money) -> Money:
        if money.currency != self.source_currency:
            raise MoneyError(
                f"FXConversion source_currency {self.source_currency} does not match "
                f"Money currency {money.currency}"
            )
        if self.quote_direction is QuoteDirection.SOURCE_PER_TARGET:
            converted = money.amount / self.rate
        else:
            converted = money.amount * self.rate
        minor_units = _MINOR_UNITS.get(self.target_currency)
        if minor_units is None:
            raise MoneyError(f"no declared minor-unit precision for {self.target_currency}")
        quantum = Decimal(1).scaleb(-minor_units)
        rounded = converted.quantize(quantum, rounding=ROUND_HALF_UP)
        return Money(
            amount=str(rounded),
            currency=self.target_currency,
            rounding_context=self.rounding_rule,
        )

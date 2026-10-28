"""Typed material-term values.

Money-denominated terms use `money_types.Money` (decimal-string amounts,
never float) per plan Section 8.2 / 25.3. Notification deadlines are a
day-count, not a currency amount, so they get their own tiny value type
rather than being forced into Money.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Union

from money_types import Money


class DayCountBasis(str, Enum):
    BUSINESS_DAYS = "BUSINESS_DAYS"
    CALENDAR_DAYS = "CALENDAR_DAYS"


@dataclass(frozen=True)
class MoneyTermValue:
    money: Money

    def as_canonical_dict(self) -> dict:
        return {
            "kind": "MONEY",
            "amount": str(self.money.amount),
            "currency": self.money.currency,
            "rounding_context": self.money.rounding_context,
        }


@dataclass(frozen=True)
class NotificationDeadlineValue:
    days: int
    basis: DayCountBasis

    def __post_init__(self) -> None:
        if self.days < 0:
            raise ValueError("notification deadline days must be non-negative")

    def as_canonical_dict(self) -> dict:
        return {"kind": "NOTIFICATION_DEADLINE", "days": self.days, "basis": self.basis.value}


TermValue = Union[MoneyTermValue, NotificationDeadlineValue]


def term_value_to_canonical_dict(value: TermValue) -> dict:
    return value.as_canonical_dict()

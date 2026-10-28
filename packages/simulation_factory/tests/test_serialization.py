"""Serialization boundary: never a JSON float for money, deterministic
byte output, timezone-aware datetimes only."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from money_types import Money

from simulation_factory.serialization import SerializationError, dump_json_bytes, to_jsonable


def test_bare_float_is_rejected():
    with pytest.raises(SerializationError):
        to_jsonable({"amount": 1.5})


def test_decimal_becomes_a_string():
    assert to_jsonable(Decimal("12.50")) == "12.50"


def test_money_amount_serializes_as_string_not_float():
    m = Money(amount="99.99", currency="USD", rounding_context="test")
    payload = to_jsonable(m)
    assert payload["amount"] == "99.99"
    assert isinstance(payload["amount"], str)


def test_naive_datetime_is_rejected():
    with pytest.raises(SerializationError):
        to_jsonable(dt.datetime(2026, 1, 1))


def test_aware_datetime_normalizes_to_utc_z_suffix():
    value = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
    assert to_jsonable(value) == "2026-01-01T12:00:00Z"


def test_dump_json_bytes_is_deterministic_across_key_order():
    a = dump_json_bytes({"b": 1, "a": 2})
    b = dump_json_bytes({"a": 2, "b": 1})
    assert a == b

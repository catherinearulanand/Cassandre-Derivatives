# ADR-005: Decimal strings at API money boundaries

## Decision
`Money`, `Quantity`, `Price`, `FXConversion` are always transmitted and stored
as decimal strings with explicit currency/unit/rounding metadata, per plan
Section 8.2. Never a JSON float.

## S1 implementation
`packages/money_types`: Pydantic models backed by Python `decimal.Decimal`,
constructed only from strings (a float constructor argument raises
`TypeError` immediately — see `money_types/core.py`). Arithmetic helpers
require an explicit `rounding_context`/`rounding_rule` and refuse silent
mixed-currency addition.

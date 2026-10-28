"""Shared constants for the core calculation layer.

`Money.rounding_context` is a required, load-bearing field (`Money.__add__`
refuses to combine amounts tagged with different rounding contexts without an
explicit reconciliation step). This module defines the two contexts this
slice actually uses so every producer/consumer agrees on the tag.
"""

from __future__ import annotations

from decimal import Decimal

TRADE_MTM_ROUNDING_CONTEXT = "riskos.trade_mtm.source_incumbent.raw"
METHOD_PACK_001_ROUNDING_CONTEXT = "riskos.method_pack.SIM-CSA-SYMMETRIC-USD-001"

METHOD_PACK_001_ID = "SIM-CSA-SYMMETRIC-USD-001"

FRESHNESS_WINDOW_HOURS = 24  # DEC-OPS-03 (provisional placeholder for this slice)

# RSK-X01 native valuation challenger (DEC-RSK-02, provisional; see
# docs/methods/RSK-X01-challenger-provisional.md). Additive only: never
# consumed by riskos.core.workflow / riskos.core.method_pack / riskos.core.
# gates / riskos.core.exposure (the S1 authoritative MARGIN_CALL path).
CHALLENGER_ROUNDING_CONTEXT = "riskos.challenger.RSK-X01-provisional"

# Tolerance = max(CHALLENGER_TOLERANCE_ABS_FLOOR, CHALLENGER_TOLERANCE_RELATIVE_BPS * abs(incumbent_pv)),
# in the incumbent trade's own currency. Chosen provisional convention
# (DEC-RSK-02): an absolute floor so small trades aren't flagged on noise,
# plus a relative component so large trades get a proportionally wider band.
CHALLENGER_TOLERANCE_ABS_FLOOR = Decimal("25.00")
CHALLENGER_TOLERANCE_RELATIVE_BPS = Decimal("0.0010")  # 10 basis points

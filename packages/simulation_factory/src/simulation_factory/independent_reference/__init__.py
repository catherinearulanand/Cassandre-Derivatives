"""Independently implemented reference scripts (plan Section 9.3's explicit
warning: "Never generate expected RiskOS totals using the same
implementation under test. Use hand-derived small cases and an independently
implemented reference script for larger synthetic portfolios.").

Nothing in this subpackage is imported by `riskos`, and nothing in it
imports `riskos`. It exists solely so `simulation_factory`'s own generated
corpus has an expected-results manifest that was computed by a second,
independent piece of code rather than by re-running (or refactoring into a
shared helper alongside) whatever aggregation logic a product happens to
implement.
"""

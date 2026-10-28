"""SIM-008: independently implemented reference script for SIM-004's trade
corpus (work item SIM-008, plan Section 9.3).

Plan Section 9.3, verbatim: "Never generate expected RiskOS totals using the
same implementation under test. Use hand-derived small cases and an
independently implemented reference script for larger synthetic
portfolios." `riskos_fixtures.py`'s five-trade scenario satisfies the
"hand-derived small case" half of that sentence (its docstring shows the
arithmetic by hand); this module is the "independently implemented
reference script for larger synthetic portfolios" half, for SIM-004's
300-trade population.

Independence, concretely:

- This module is never imported by `riskos` (it lives in
  `simulation_factory`, which product `core`/`domain` packages never
  depend on -- see `docs/INTERPRETATION.md` Section 4's dependency graph).
- `simulation_factory.corpus.trades` is never imported *by* this module for
  its bucket-size constants (`N_ACCEPTED`, etc.) -- this script re-derives
  every total by walking the actual `TradeRecord` sequence field-by-field,
  the same way a completely separate team, working only from the plan's
  prose description of what a source-control manifest is, would have to.
- The aggregation here is a single flat loop over plain attribute reads and
  a running `Decimal` total. It intentionally does not call, wrap, or share
  a helper function with anything under `simulation_factory.corpus` or any
  product's own aggregation/reconciliation code, so it is a genuinely
  different code path, not a thin re-export of "the same implementation
  under test" plan Section 9.3 warns against.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from money_types import Money

REFERENCE_VERSION = "1.0.0"


class _TradeLike(Protocol):
    trade_id: str
    disposition: str
    mtm: Money


@dataclass(frozen=True)
class ReferenceSourceControlManifest:
    """Independently computed control totals. Field names are chosen to
    read compatibly with `riskos.domain.entities.SourceControlManifest`
    (`expected_accepted_count`, `expected_accepted_mtm_total`, etc.) so a
    later integration step can map one to the other directly, but this
    dataclass is defined here, standalone -- `riskos` is never imported."""

    manifest_id: str
    expected_accepted_count: int
    expected_rejected_count: int
    expected_duplicate_count: int
    expected_matured_count: int
    expected_cancelled_count: int
    expected_amended_count: int
    expected_unsupported_count: int
    expected_accepted_mtm_total: Money
    expected_missing_trade_ids: tuple[str, ...]
    rejection_reason_counts: tuple[tuple[str, int], ...]


_KNOWN_DISPOSITIONS = (
    "ACCEPTED", "REJECTED", "DUPLICATE", "MATURED", "CANCELLED", "AMENDED", "UNSUPPORTED",
)


def compute_source_control_manifest(
    trades,
    *,
    manifest_id: str,
    expected_missing_trade_ids: tuple[str, ...] = (),
    mtm_currency: str = "USD",
    mtm_rounding_context: str = "SIM-TRADE-MTM-SOURCE",
) -> ReferenceSourceControlManifest:
    """Walk `trades` once, tallying each disposition bucket and summing the
    ACCEPTED MTMs, entirely by hand -- no shared aggregation helper, no
    generator internals. `trades` is any iterable of objects exposing
    `.disposition` (one of `_KNOWN_DISPOSITIONS`), `.mtm` (a `Money`), and
    optionally `.rejection_reason`."""

    counts = {name: 0 for name in _KNOWN_DISPOSITIONS}
    accepted_total = Decimal("0")
    rejection_reasons: dict[str, int] = {}
    seen_trade_ids: set[str] = set()

    for trade in trades:
        disposition = trade.disposition
        if disposition not in counts:
            raise ValueError(
                f"trade {getattr(trade, 'trade_id', '?')!r} has an unrecognized "
                f"disposition {disposition!r}; every disposition must be one of "
                f"{_KNOWN_DISPOSITIONS}, per plan Section 9.3/11.3's closed "
                "population-control vocabulary"
            )
        counts[disposition] += 1
        seen_trade_ids.add(trade.trade_id)

        if disposition == "ACCEPTED":
            accepted_total += trade.mtm.amount

        reason = getattr(trade, "rejection_reason", None)
        if disposition == "REJECTED" and reason:
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    for missing_id in expected_missing_trade_ids:
        if missing_id in seen_trade_ids:
            raise ValueError(
                f"{missing_id!r} is listed as an expected-missing trade but was "
                "actually found in the delivered population -- it is not missing"
            )

    return ReferenceSourceControlManifest(
        manifest_id=manifest_id,
        expected_accepted_count=counts["ACCEPTED"],
        expected_rejected_count=counts["REJECTED"],
        expected_duplicate_count=counts["DUPLICATE"],
        expected_matured_count=counts["MATURED"],
        expected_cancelled_count=counts["CANCELLED"],
        expected_amended_count=counts["AMENDED"],
        expected_unsupported_count=counts["UNSUPPORTED"],
        expected_accepted_mtm_total=Money(
            amount=str(accepted_total), currency=mtm_currency, rounding_context=mtm_rounding_context
        ),
        expected_missing_trade_ids=tuple(expected_missing_trade_ids),
        rejection_reason_counts=tuple(sorted(rejection_reasons.items())),
    )

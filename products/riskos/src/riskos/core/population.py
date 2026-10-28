"""Trade-population reconciliation against a source-control manifest, per
plan Section 9.3/11.3: "Before aggregation, reconcile accepted, rejected,
duplicate, matured, cancelled, amended, and unsupported trade counts and MTM
totals to the source-control manifest. A population failure blocks the
affected netting-set run atomically."

Returns an explicit result object rather than raising, matching the style of
`audit_protocol.chain.ChainVerificationResult` / `cassandre_contracts.
VerificationResult` elsewhere in this codebase: expected-but-failing business
conditions are data, not exceptions.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from money_types import Money, MoneyError

from riskos.core.constants import TRADE_MTM_ROUNDING_CONTEXT
from riskos.domain.entities import PortfolioSnapshot, SourceControlManifest
from riskos.domain.enums import TradePopulationDisposition


@dataclass(frozen=True)
class PopulationReconciliationResult:
    ok: bool
    resolved_counts: dict[TradePopulationDisposition, int]
    resolved_accepted_mtm_total: Money | None
    mismatches: tuple[str, ...] = field(default_factory=tuple)


def reconcile_population(
    portfolio: PortfolioSnapshot, manifest: SourceControlManifest
) -> PopulationReconciliationResult:
    counts: Counter[TradePopulationDisposition] = Counter(t.disposition for t in portfolio.trades)
    resolved = {d: counts.get(d, 0) for d in TradePopulationDisposition}

    expected = {
        TradePopulationDisposition.ACCEPTED: manifest.expected_accepted_count,
        TradePopulationDisposition.REJECTED: manifest.expected_rejected_count,
        TradePopulationDisposition.DUPLICATE: manifest.expected_duplicate_count,
        TradePopulationDisposition.MATURED: manifest.expected_matured_count,
        TradePopulationDisposition.CANCELLED: manifest.expected_cancelled_count,
        TradePopulationDisposition.AMENDED: manifest.expected_amended_count,
        TradePopulationDisposition.UNSUPPORTED: manifest.expected_unsupported_count,
    }

    mismatches: list[str] = []
    for disposition, expected_count in expected.items():
        actual_count = resolved[disposition]
        if actual_count != expected_count:
            mismatches.append(
                f"{disposition.value}: expected {expected_count} trades, resolved {actual_count}"
            )

    accepted_trades = [
        t for t in portfolio.trades if t.disposition is TradePopulationDisposition.ACCEPTED
    ]
    accepted_mtm_total: Money | None = None
    if accepted_trades:
        try:
            accepted_mtm_total = accepted_trades[0].mtm
            for trade in accepted_trades[1:]:
                accepted_mtm_total = accepted_mtm_total + trade.mtm
        except MoneyError as exc:
            mismatches.append(f"cannot aggregate accepted MTM totals: {exc}")
            accepted_mtm_total = None
    else:
        accepted_mtm_total = Money(
            amount="0", currency=manifest.expected_accepted_mtm_total.currency,
            rounding_context=TRADE_MTM_ROUNDING_CONTEXT,
        )

    if accepted_mtm_total is not None:
        if (
            accepted_mtm_total.currency != manifest.expected_accepted_mtm_total.currency
            or accepted_mtm_total.amount != manifest.expected_accepted_mtm_total.amount
        ):
            mismatches.append(
                "accepted MTM total mismatch: expected "
                f"{manifest.expected_accepted_mtm_total.amount} "
                f"{manifest.expected_accepted_mtm_total.currency}, resolved "
                f"{accepted_mtm_total.amount} {accepted_mtm_total.currency}"
            )

    return PopulationReconciliationResult(
        ok=not mismatches,
        resolved_counts=resolved,
        resolved_accepted_mtm_total=accepted_mtm_total,
        mismatches=tuple(mismatches),
    )

"""SIM-009: consolidated negative/adversarial fixture index, per the work
item's instruction to bring the "deliberately broken" cases scattered
across SIM-003 (missing incorporated document, conflicting clauses,
ratings-contingent missing state, rounding-election mismatch), SIM-004
(duplicate/wrong-entity/wrong-sign/unrecognized-counterparty/unsupported-
product/missing trades), SIM-005 (stale/missing/crossed market data), and
SIM-006 (ineligible/wrong-way/stale-price/wrong-location/encumbered/
pending/reserved/recalled/minimum-lot-conflicting inventory) into one
clearly labeled index.

This module does not generate new data -- it walks the already-built
corpora and emits one flat, uniformly shaped record per adversarial case,
so a later integration step (not this package's job -- see the README) has
a single place to look up "every deliberately broken fixture in the
simulation factory" instead of re-deriving that list from each corpus
module's internals.
"""

from __future__ import annotations

from dataclasses import dataclass

from simulation_factory.corpus.agreements import AgreementBundle
from simulation_factory.corpus.inventory import InventoryPosition
from simulation_factory.corpus.market import MarketSnapshot
from simulation_factory.corpus.trades import TradeCorpus
from simulation_factory.identifiers import require_sim_identifier


@dataclass(frozen=True)
class AdversarialCase:
    index_id: str
    domain: str  # "AGREEMENT" | "TRADE" | "MARKET" | "INVENTORY"
    case_type: str
    object_id: str
    description: str

    def __post_init__(self) -> None:
        require_sim_identifier(self.index_id, field_name="index_id")


def _agreement_cases(bundles: tuple[AgreementBundle, ...]) -> list[AdversarialCase]:
    cases = []
    for bundle in bundles:
        gt = bundle.ground_truth
        if gt.missing_incorporated_document:
            cases.append(("MISSING_INCORPORATED_DOCUMENT", bundle.bundle_id, "Bundle declares an incorporated document that is never supplied; expected status BUNDLE_INCOMPLETE."))
        if gt.has_conflicting_clauses:
            cases.append(("CONFLICTING_CLAUSES", bundle.bundle_id, "Bundle contains two irreconcilable statements of the same material term with no stated precedence; expected status REVIEW_REQUIRED."))
        if gt.rating_contingent_threshold and not gt.rating_state_known:
            cases.append(("RATINGS_STATE_MISSING", bundle.bundle_id, "Threshold depends on a ratings state this bundle never supplies; expected EXTERNAL_STATE_REQUIRED, never a defaulted threshold."))
        if not bundle.riskos_calculable and gt.rounding_order == "ROUNDING_BEFORE_MTA":
            cases.append(("UNSUPPORTED_ROUNDING_ELECTION", bundle.bundle_id, "Same CSA family as a calculable bundle but an election (rounding-before-MTA) the one implemented method pack does not support; expected UNSUPPORTED_AGREEMENT/METHOD_UNRESOLVED."))
        if gt.wrong_way_issuer_exclusion:
            cases.append(("WRONG_WAY_ISSUER_EXCLUSION_CLAUSE", bundle.bundle_id, "Bundle's eligible-collateral clause includes an explicit wrong-way issuer exclusion and concentration cap."))
    return [
        AdversarialCase(index_id=f"SIM-ADV-AGR-{i:03d}", domain="AGREEMENT", case_type=case_type, object_id=object_id, description=description)
        for i, (case_type, object_id, description) in enumerate(cases, start=1)
    ]


def _trade_cases(trade_corpus: TradeCorpus) -> list[AdversarialCase]:
    cases = []
    for trade in trade_corpus.trades:
        if trade.trap_type is not None:
            cases.append(
                (
                    trade.trap_type,
                    trade.trade_id,
                    f"Trade {trade.trade_id} (version {trade.version}) is a deliberate "
                    f"{trade.trap_type} population trap; disposition={trade.disposition}"
                    + (f", rejection_reason={trade.rejection_reason}" if trade.rejection_reason else "") + ".",
                )
            )
    for missing_id in trade_corpus.expected_missing_trade_ids:
        cases.append(
            (
                "MISSING_TRADE",
                missing_id,
                f"{missing_id} is named by the source-control manifest as expected but "
                "never appears in the delivered population.",
            )
        )
    return [
        AdversarialCase(index_id=f"SIM-ADV-TRD-{i:03d}", domain="TRADE", case_type=case_type, object_id=object_id, description=description)
        for i, (case_type, object_id, description) in enumerate(cases, start=1)
    ]


def _market_cases(snapshots: tuple[MarketSnapshot, ...]) -> list[AdversarialCase]:
    cases = []
    for snapshot in snapshots:
        neg = snapshot.negative_case
        if neg.is_stale:
            cases.append(("STALE_SNAPSHOT", snapshot.snapshot_id, f"Snapshot {snapshot.snapshot_id} ({snapshot.regime}) has stale_after before as_of: {neg.notes}"))
        if neg.has_missing_quote:
            cases.append(("MISSING_QUOTE", snapshot.snapshot_id, f"Snapshot {snapshot.snapshot_id} ({snapshot.regime}) omits an expected FX quote: {neg.notes}"))
        if neg.has_crossed_market:
            cases.append(("CROSSED_MARKET", snapshot.snapshot_id, f"Snapshot {snapshot.snapshot_id} ({snapshot.regime}) has a degenerate/crossed quote: {neg.notes}"))
    return [
        AdversarialCase(index_id=f"SIM-ADV-MKT-{i:03d}", domain="MARKET", case_type=case_type, object_id=object_id, description=description)
        for i, (case_type, object_id, description) in enumerate(cases, start=1)
    ]


def _inventory_cases(positions: tuple[InventoryPosition, ...]) -> list[AdversarialCase]:
    cases = []
    for position in positions:
        for trap in position.trap_types:
            cases.append((trap, position.position_id, f"Position {position.position_id} ({position.asset_class}) is tagged {trap}."))
    return [
        AdversarialCase(index_id=f"SIM-ADV-INV-{i:05d}", domain="INVENTORY", case_type=case_type, object_id=object_id, description=description)
        for i, (case_type, object_id, description) in enumerate(cases, start=1)
    ]


@dataclass(frozen=True)
class AdversarialIndex:
    cases: tuple[AdversarialCase, ...]

    def by_domain(self, domain: str) -> tuple[AdversarialCase, ...]:
        return tuple(c for c in self.cases if c.domain == domain)

    def by_case_type(self, case_type: str) -> tuple[AdversarialCase, ...]:
        return tuple(c for c in self.cases if c.case_type == case_type)


def build_adversarial_index(
    *,
    agreement_bundles: tuple[AgreementBundle, ...],
    trade_corpus: TradeCorpus,
    market_snapshots: tuple[MarketSnapshot, ...],
    inventory_positions: tuple[InventoryPosition, ...],
) -> AdversarialIndex:
    cases = (
        tuple(_agreement_cases(agreement_bundles))
        + tuple(_trade_cases(trade_corpus))
        + tuple(_market_cases(market_snapshots))
        + tuple(_inventory_cases(inventory_positions))
    )
    ids = [c.index_id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate index_id in adversarial index")
    return AdversarialIndex(cases=cases)

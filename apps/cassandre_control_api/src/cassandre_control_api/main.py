"""Cassandre control API — the optional federation layer (plan Section 3.4,
work items AET-001/002/003/006). Validates and correlates signed envelopes
from ClauseOS/RiskOS/CollateralOS without calling any product-internal code
(only `cassandre_contracts` is imported — see `scripts/check_import_graph.py`
for the enforced boundary).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cassandre_contracts import Envelope

from cassandre_control_api.domain import (
    FederatedAuditExport,
    RejectedEnvelopeError,
    assemble_federated_audit_export,
    record_envelope,
)
from cassandre_control_api.store import CorrelationStore, TrustRegistry

app = FastAPI(title="Cassandre Control API", version="0.1.0")

_trust_registry = TrustRegistry()
_correlation_store = CorrelationStore()


def get_trust_registry() -> TrustRegistry:
    return _trust_registry


def get_correlation_store() -> CorrelationStore:
    return _correlation_store


class TrustKeyIn(BaseModel):
    key_id: str
    public_key_b64: str


@app.post("/v1/trust-keys", status_code=201)
def register_trust_key(body: TrustKeyIn, registry: TrustRegistry = Depends(get_trust_registry)) -> dict[str, str]:
    registry.register(body.key_id, body.public_key_b64)
    return {"status": "registered", "key_id": body.key_id}


@app.post("/v1/trust-keys/{key_id}:revoke", status_code=200)
def revoke_trust_key(key_id: str, registry: TrustRegistry = Depends(get_trust_registry)) -> dict[str, str]:
    registry.revoke(key_id)
    return {"status": "revoked", "key_id": key_id}


class EnvelopeSubmitResult(BaseModel):
    accepted: bool
    correlation_key: str | None = None
    producer_product: str | None = None
    object_id: str | None = None


@app.post("/v1/envelopes:submit", response_model=EnvelopeSubmitResult)
def submit_envelope(
    envelope: Envelope,
    registry: TrustRegistry = Depends(get_trust_registry),
    store: CorrelationStore = Depends(get_correlation_store),
) -> EnvelopeSubmitResult:
    try:
        receipt = record_envelope(envelope, trust_bundle=registry.snapshot())
    except RejectedEnvelopeError as exc:
        raise HTTPException(status_code=422, detail={"failures": exc.failures}) from exc
    case = store.add_receipt(receipt)
    return EnvelopeSubmitResult(
        accepted=True,
        correlation_key=case.correlation_key,
        producer_product=envelope.producer_product.value,
        object_id=envelope.object_id,
    )


class CorrelatedCaseOut(BaseModel):
    correlation_key: str
    is_cross_product_complete: bool
    products_present: list[str]


@app.get("/v1/correlated-cases/{correlation_key}", response_model=CorrelatedCaseOut)
def get_correlated_case(correlation_key: str, store: CorrelationStore = Depends(get_correlation_store)) -> CorrelatedCaseOut:
    case = store.get(correlation_key)
    if case is None:
        raise HTTPException(status_code=404, detail="no correlated case for this key")
    return CorrelatedCaseOut(
        correlation_key=case.correlation_key,
        is_cross_product_complete=case.is_cross_product_complete,
        products_present=[p.value for p in case.receipts_by_product],
    )


@app.get("/v1/correlated-cases/{correlation_key}/federated-audit-export")
def get_federated_audit_export(
    correlation_key: str, store: CorrelationStore = Depends(get_correlation_store)
) -> dict[str, Any]:
    case = store.get(correlation_key)
    if case is None:
        raise HTTPException(status_code=404, detail="no correlated case for this key")
    export: FederatedAuditExport = assemble_federated_audit_export(case)
    return {
        "correlation_key": export.correlation_key,
        "assembled_at": export.assembled_at.isoformat(),
        "receipts": [
            {
                "producer_product": r.envelope.producer_product.value,
                "object_id": r.envelope.object_id,
                "object_version": r.envelope.object_version,
                "content_hash": r.envelope.content_hash,
                "authority_status": r.envelope.authority_status,
                "received_at": r.received_at.isoformat(),
            }
            for r in export.receipts
        ],
    }


# Plain HTML/CSS/vanilla-JS control UI, mounted last so it never shadows the
# API routes above (Starlette matches routes in registration order; the
# explicit @app.get/@app.post routes always win on exact-path matches, and
# this catch-all Mount only serves paths none of them claim, e.g. "/",
# "/app.js", "/style.css"). Resolved relative to this file rather than the
# process cwd, and skipped gracefully if the directory doesn't exist.
_WEB_STATIC_DIR = Path(__file__).resolve().parents[3] / "cassandre_control_web" / "static"
if _WEB_STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_WEB_STATIC_DIR, html=True), name="web-ui")

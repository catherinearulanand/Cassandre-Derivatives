"""ClauseOS minimal API (plan Section 10.7 / Section 25.5 item 10).

No OIDC/Keycloak wiring in this slice (see product README and
`docs/adr/ADR-009-oidc.md`'s recorded gap) -- `actor_id` is passed explicitly
in each request body as a stand-in for an authenticated identity. What is
NOT stubbed is the maker/checker business rule: `clauseos.domain.review`
raises `SameActorError` if the same `actor_id` is used for both decisions,
and the API surfaces that as an explicit 409, not a silently accepted call.
"""

from __future__ import annotations

import base64
import datetime as dt
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

from audit_protocol.keys import Ed25519KeyPair, generate_keypair
from clauseos.adapters.memory import InMemoryBundleRepository
from clauseos.application.bundle_service import BundleService
from clauseos.application.document_sealing import DocumentSealingService
from clauseos.application.ports import BundleRepository
from clauseos.application.security import ClauseOsAuthorizationService
from clauseos.adapters.db.fence_gateway import SqlAlchemyRevocationFenceGateway
from clauseos.domain.bundle import BundleManifest
from clauseos.domain.errors import (
    AuthorizationDeniedError,
    BundleIncompleteError,
    ClauseOsDomainError,
    DocumentIngestionRejectedError,
    IncompletePolicyError,
    InvalidTransitionError,
    ManifestNotApprovedError,
    RevocationFenceBlockedError,
    SameActorError,
    StaleReviewError,
)
from clauseos.domain.status import ReviewDecision

from clauseos_api.serialization import (
    assertion_to_response,
    bundle_to_response,
    policy_release_to_response,
    review_case_to_response,
)

# SEC-04: an authorization DENY is a 403, checked before the other domain
# error tuples below (a denied action is not merely a state conflict).
_FORBIDDEN_ERRORS = (AuthorizationDeniedError,)
_CONFLICT_ERRORS = (
    InvalidTransitionError,
    ManifestNotApprovedError,
    SameActorError,
    StaleReviewError,
    RevocationFenceBlockedError,  # SEC-14: stale/unknown/revoked fence state is a conflict, not a bad request.
)
_UNPROCESSABLE_ERRORS = (BundleIncompleteError, IncompletePolicyError, DocumentIngestionRejectedError)

# apps/clauseos_web's static files, mounted onto this API app (Gap 3: ClauseOS
# UI). Resolved relative to this file so it works regardless of cwd:
# apps/clauseos_api/src/clauseos_api/app.py -> ../../../clauseos_web/static
_WEB_STATIC_DIR = Path(__file__).resolve().parents[3] / "clauseos_web" / "static"


class CreateBundleRequest(BaseModel):
    scenario_id: str
    actor_id: str


class IngestDocumentRequest(BaseModel):
    """Exactly one of `content` (plain text/Markdown, unchanged since the
    initial slice) or `content_base64` (binary bytes -- e.g. a PDF -- for
    the Gap 1 ingestion pipeline) must be given."""

    document_name: str
    actor_id: str
    content: str | None = None
    content_base64: str | None = None

    @model_validator(mode="after")
    def _exactly_one_content_field(self) -> "IngestDocumentRequest":
        if (self.content is None) == (self.content_base64 is None):
            raise ValueError("exactly one of 'content' or 'content_base64' is required")
        return self

    def raw_bytes(self) -> bytes:
        if self.content is not None:
            return self.content.encode("utf-8")
        assert self.content_base64 is not None
        return base64.b64decode(self.content_base64)


class ApproveManifestRequest(BaseModel):
    document_type: str
    parties: list[str]
    execution_date: dt.date
    effective_date: dt.date
    incorporated_by_reference: list[str] = []
    actor_id: str


class AnalyzeRequest(BaseModel):
    actor_id: str
    # Gap CLO-X01: strictly additive, off by default. Never required for
    # authoritative publication (ADR-006) -- the rule proposer always runs
    # regardless of this flag.
    include_model_proposer: bool = False


class RevokePolicyReleaseRequest(BaseModel):
    actor_id: str


class ReviewDecisionRequest(BaseModel):
    actor_id: str
    decision: Literal["APPROVE", "REJECT"]


class PublishPolicyReleaseRequest(BaseModel):
    bundle_id: str
    actor_id: str


class ExportEvidenceBundleRequest(BaseModel):
    bundle_id: str
    # Optional, defaulted for backward compatibility: this endpoint
    # predates the SEC-04 authorization gate and existing tests call it
    # without an actor_id. A real deployment (once OIDC/Keycloak identity
    # is wired, per ADR-009's disclosed gap) should make this mandatory.
    actor_id: str = "SIM-USER-UNSPECIFIED-EXPORT"


def create_app(
    repository: BundleRepository | None = None,
    *,
    signing_keypair: Ed25519KeyPair | None = None,
    authorization: ClauseOsAuthorizationService | None = None,
    fence_gateway: SqlAlchemyRevocationFenceGateway | None = None,
    document_sealing: DocumentSealingService | None = None,
) -> FastAPI:
    repo = repository or InMemoryBundleRepository()
    service = BundleService(
        repo, authorization=authorization, fence_gateway=fence_gateway, document_sealing=document_sealing
    )
    keypair = signing_keypair or generate_keypair("clauseos-api-key-1")

    app = FastAPI(title="ClauseOS API", version="0.1.0")
    app.state.service = service
    app.state.keypair = keypair

    @app.exception_handler(ClauseOsDomainError)
    async def _domain_error_handler(request: Request, exc: ClauseOsDomainError) -> JSONResponse:
        if isinstance(exc, _FORBIDDEN_ERRORS):
            status_code = 403
        elif isinstance(exc, _UNPROCESSABLE_ERRORS):
            status_code = 422
        elif isinstance(exc, _CONFLICT_ERRORS):
            status_code = 409
        else:
            status_code = 400
        content: dict[str, str] = {"error": type(exc).__name__, "detail": str(exc)}
        if isinstance(exc, DocumentIngestionRejectedError):
            content["ingestion_status"] = exc.status.value
        return JSONResponse(status_code=status_code, content=content)

    @app.exception_handler(KeyError)
    async def _not_found_handler(request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "NOT_FOUND", "detail": str(exc)})

    def get_service() -> BundleService:
        return app.state.service

    @app.post("/v1/bundles")
    def create_bundle(body: CreateBundleRequest, svc: BundleService = Depends(get_service)):
        bundle = svc.create_bundle(scenario_id=body.scenario_id, actor_id=body.actor_id)
        return bundle_to_response(bundle)

    @app.post("/v1/bundles/{bundle_id}/documents")
    def add_document(
        bundle_id: str, body: IngestDocumentRequest, svc: BundleService = Depends(get_service)
    ):
        svc.ingest_document(
            bundle_id=bundle_id,
            document_name=body.document_name,
            raw_bytes=body.raw_bytes(),
            actor_id=body.actor_id,
        )
        bundle = svc.get_bundle(bundle_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail="bundle not found")
        return bundle_to_response(bundle)

    @app.post("/v1/bundles/{bundle_id}/manifest:approve")
    def approve_manifest(
        bundle_id: str, body: ApproveManifestRequest, svc: BundleService = Depends(get_service)
    ):
        manifest = BundleManifest(
            document_type=body.document_type,
            parties=tuple(body.parties),
            execution_date=body.execution_date,
            effective_date=body.effective_date,
            incorporated_by_reference=tuple(body.incorporated_by_reference),
        )
        bundle = svc.approve_manifest(bundle_id=bundle_id, manifest=manifest, actor_id=body.actor_id)
        return bundle_to_response(bundle)

    @app.post("/v1/bundles/{bundle_id}:analyze")
    def analyze_bundle(bundle_id: str, body: AnalyzeRequest, svc: BundleService = Depends(get_service)):
        assertions = svc.analyze_bundle(
            bundle_id=bundle_id,
            actor_id=body.actor_id,
            include_model_proposer=body.include_model_proposer,
        )
        return {"assertions": [assertion_to_response(a) for a in assertions]}

    @app.get("/v1/bundles")
    def list_bundles(svc: BundleService = Depends(get_service)):
        return {"bundles": [bundle_to_response(b) for b in svc.list_bundles()]}

    @app.get("/v1/bundles/{bundle_id}")
    def get_bundle(bundle_id: str, svc: BundleService = Depends(get_service)):
        bundle = svc.get_bundle(bundle_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail="bundle not found")
        return bundle_to_response(bundle)

    @app.get("/v1/bundles/{bundle_id}/documents/{document_name}")
    def get_document_text(bundle_id: str, document_name: str, svc: BundleService = Depends(get_service)):
        document = svc.get_document_text(bundle_id=bundle_id, document_name=document_name)
        if document is None:
            raise HTTPException(status_code=404, detail="document not found")
        return {
            "document_id": document.document_id,
            "document_name": document.document_name,
            "raw_bytes_sha256": document.raw_bytes_sha256,
            "normalized_text": document.normalized_text,
            "normalized_text_sha256": document.normalized_text_sha256,
        }

    @app.get("/v1/bundles/{bundle_id}/audit-events")
    def list_audit_events(bundle_id: str, svc: BundleService = Depends(get_service)):
        events = svc.list_audit_events(bundle_id=bundle_id)
        return {
            "audit_events": [
                {
                    "event_id": e.event_id,
                    "sequence": e.sequence,
                    "action": e.action,
                    "actor_id": e.actor_id,
                    "actor_type": e.actor_type,
                    "reason_code": e.reason_code,
                    "object_version": e.object_version,
                    "object_content_hash": e.object_content_hash,
                    "event_timestamp": e.event_timestamp.isoformat(),
                    "previous_event_hash": e.previous_event_hash,
                    "event_hash": e.event_hash,
                    "trace_id": e.trace_id,
                }
                for e in events
            ]
        }

    @app.get("/v1/assertions")
    def list_assertions(
        bundle_id: str | None = None,
        concept: str | None = None,
        status: str | None = None,
        svc: BundleService = Depends(get_service),
    ):
        assertions = svc.list_assertions(bundle_id=bundle_id, concept=concept, status=status)
        return {"assertions": [assertion_to_response(a) for a in assertions]}

    @app.get("/v1/bundles/{bundle_id}/reviews")
    def list_reviews(bundle_id: str, svc: BundleService = Depends(get_service)):
        review_cases = svc.list_review_cases(bundle_id=bundle_id)
        return {"reviews": [review_case_to_response(r) for r in review_cases]}

    @app.post("/v1/reviews/{review_id}/maker-decision")
    def maker_decision(
        review_id: str, body: ReviewDecisionRequest, svc: BundleService = Depends(get_service)
    ):
        review_case = svc.record_maker_decision(
            review_id=review_id, actor_id=body.actor_id, decision=ReviewDecision(body.decision)
        )
        return review_case_to_response(review_case)

    @app.post("/v1/reviews/{review_id}/checker-decision")
    def checker_decision(
        review_id: str, body: ReviewDecisionRequest, svc: BundleService = Depends(get_service)
    ):
        review_case = svc.record_checker_decision(
            review_id=review_id, actor_id=body.actor_id, decision=ReviewDecision(body.decision)
        )
        return review_case_to_response(review_case)

    @app.post("/v1/policy-releases")
    def publish_policy_release(
        body: PublishPolicyReleaseRequest, svc: BundleService = Depends(get_service)
    ):
        release, envelope = svc.publish_policy_release(
            bundle_id=body.bundle_id, actor_id=body.actor_id, keypair=app.state.keypair
        )
        return {
            "policy_release": policy_release_to_response(release),
            "envelope": envelope.model_dump(mode="json"),
        }

    @app.get("/v1/policy-releases")
    def list_policy_releases(
        bundle_id: str | None = None, svc: BundleService = Depends(get_service)
    ):
        releases = svc.list_policy_releases(bundle_id=bundle_id)
        return {"policy_releases": [policy_release_to_response(r) for r in releases]}

    @app.post("/v1/policy-releases/{release_id}:revoke")
    def revoke_policy_release(
        release_id: str, body: RevokePolicyReleaseRequest, svc: BundleService = Depends(get_service)
    ):
        release = svc.revoke_policy_release(release_id=release_id, actor_id=body.actor_id)
        return policy_release_to_response(release)

    @app.post("/v1/evidence-bundles:export")
    def export_evidence_bundle(
        body: ExportEvidenceBundleRequest, svc: BundleService = Depends(get_service)
    ):
        # SEC-04: deny-by-default authorization gate. Raises
        # AuthorizationDeniedError (-> 403) before any evidence is assembled.
        svc.authorize_export_evidence_bundle(bundle_id=body.bundle_id, actor_id=body.actor_id)
        bundle = svc.get_bundle(body.bundle_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail="bundle not found")
        assertions = svc.list_assertions(bundle_id=body.bundle_id)
        return {
            "bundle": bundle_to_response(bundle),
            "documents": [
                {
                    "document_id": d.document_id,
                    "document_name": d.document_name,
                    "raw_bytes_sha256": d.raw_bytes_sha256,
                    "normalized_text_sha256": d.normalized_text_sha256,
                }
                for d in bundle.documents.values()
            ],
            "assertions": [assertion_to_response(a) for a in assertions],
        }

    # Gap 3: ClauseOS UI. Mounted only if the static directory actually
    # exists, so this app still boots (and every pre-existing API test
    # still passes) in an environment that doesn't have apps/clauseos_web
    # checked out for some reason. `html=True` serves `index.html` for `/ui`
    # and `/ui/`.
    if _WEB_STATIC_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=str(_WEB_STATIC_DIR), html=True), name="clauseos_ui")

    return app


app = create_app()

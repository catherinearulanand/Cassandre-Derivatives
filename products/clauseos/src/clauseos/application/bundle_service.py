"""Orchestration for the Section 25.5 ClauseOS slice: ingest -> manifest
approval/completeness -> analyze (rule proposer + structural verifier) ->
maker/checker review -> signed policy release.

This is the only layer allowed to call a clock or generate an id/trace-id;
`domain`/`core` stay pure per plan Section 25.2/25.3.
"""

from __future__ import annotations

import datetime as dt
import uuid

from audit_protocol.keys import Ed25519KeyPair
from cassandre_contracts import ProducerProduct, SimulationProvenance
from cassandre_security.maker_checker import (
    DistinctActorViolation,
    NaturalPersonDirectory,
    enforce_distinct_natural_persons,
)
from cassandre_security.revocation import RevocationState

from clauseos.adapters.db.fence_gateway import SqlAlchemyRevocationFenceGateway, clauseos_fence_scope_key
from clauseos.application.document_sealing import DocumentSealingService
from clauseos.application.envelope_service import build_policy_release_envelope
from clauseos.application.ports import BundleRepository
from clauseos.application.security import (
    ACTION_ANALYZE_BUNDLE,
    ACTION_EXPORT_EVIDENCE_BUNDLE,
    ACTION_INGEST_DOCUMENT,
    ACTION_PUBLISH_POLICY_RELEASE,
    ACTION_RECORD_CHECKER_DECISION,
    ACTION_RECORD_MAKER_DECISION,
    PURPOSE_CHECKER_REVIEW_DECISION,
    PURPOSE_DOCUMENT_INGESTION,
    PURPOSE_EVIDENCE_EXPORT,
    PURPOSE_MAKER_REVIEW_DECISION,
    PURPOSE_POLICY_RELEASE_PUBLICATION,
    PURPOSE_PROPOSE_ASSERTION,
    ClauseOsAuthorizationService,
)
from clauseos.core.normalization import normalize_text, sha256_hex, sha256_hex_of_text
from clauseos.core.pdf_ingestion import DocumentMimeType, detect_mime_type, ingest_pdf_bytes
from clauseos.core.rule_proposer import propose_candidates
from clauseos.core.verification import verify_evidence_span
from clauseos.domain.assertion import ContractAssertion, compute_evidence_content_hash
from clauseos.domain.bundle import AgreementBundle, BundleManifest, DocumentNode
from clauseos.domain.errors import BundleIncompleteError, DocumentIngestionRejectedError, SameActorError
from clauseos.domain.policy import build_policy_release
from clauseos.domain.review import ReviewCase
from clauseos.domain.status import AssertionStatus, BundleStatus, ReviewDecision

ACTOR_TYPE_HUMAN = "HUMAN"


def _clock() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class BundleService:
    def __init__(
        self,
        repository: BundleRepository,
        *,
        authorization: ClauseOsAuthorizationService | None = None,
        fence_gateway: SqlAlchemyRevocationFenceGateway | None = None,
        document_sealing: DocumentSealingService | None = None,
        natural_person_directory: NaturalPersonDirectory | None = None,
    ) -> None:
        self._repo = repository
        # SEC-05 defense-in-depth (see record_checker_decision): S1 default
        # is the identity resolution (one account = one person), same as
        # cassandre_security's own default -- a real deployment plugs an
        # alias-aware directory in here.
        self._natural_person_directory = natural_person_directory
        # SEC-04: deny-by-default authorization gate (see
        # clauseos.application.security). Every existing call site/test
        # that constructs `BundleService(repo)` positionally gets the
        # default engine, which allowlists exactly the actions this
        # product's service layer performs -- so pre-existing behavior is
        # unchanged unless a caller injects a denying revocation_checker.
        self._authz = authorization or ClauseOsAuthorizationService.build_default()
        # SEC-14: revocation/security-fence gating for critical mutations
        # (publish policy release, checker approval). `None` (the default)
        # disables fencing entirely -- additive capability, not required
        # for any existing test/call site.
        self._fence_gateway = fence_gateway
        # SEC-08: additive envelope-encryption capability for restricted
        # document bytes (see clauseos.application.document_sealing). Not
        # required by ClauseOS's existing plain-text document storage.
        self._document_sealing = document_sealing or DocumentSealingService()

    def _tenant_id_for_bundle(self, bundle: AgreementBundle) -> str:
        # ClauseOS has no multi-tenant concept yet; scenario_id is the
        # closest existing scoping identifier in this synthetic
        # single-tenant deployment (see clauseos.application.security).
        return bundle.scenario_id

    def _record_audit(
        self,
        *,
        bundle: AgreementBundle,
        actor_id: str,
        action: str,
        reason_code: str | None = None,
    ) -> None:
        object_hash = sha256_hex(f"{bundle.bundle_id}:{bundle.status.value}:{bundle.version}".encode())
        self._repo.append_audit_event(
            aggregate_id=bundle.bundle_id,
            actor_id=actor_id,
            actor_type=ACTOR_TYPE_HUMAN,
            action=action,
            reason_code=reason_code,
            object_version=str(bundle.version),
            object_content_hash=object_hash,
            event_timestamp=_clock(),
            simulation_provenance={
                "is_simulated": True,
                "scenario_id": bundle.scenario_id,
                "generator_version": "0.1.0",
            },
            trace_id=str(uuid.uuid4()),
        )

    def create_bundle(self, *, scenario_id: str, actor_id: str) -> AgreementBundle:
        bundle = AgreementBundle(
            bundle_id=_new_id("bundle"),
            scenario_id=scenario_id,
            status=BundleStatus.CREATED,
            created_at=_clock(),
        )
        self._repo.save_bundle(bundle)
        self._record_audit(bundle=bundle, actor_id=actor_id, action="BUNDLE_CREATED")
        return bundle

    def get_bundle(self, bundle_id: str) -> AgreementBundle | None:
        return self._repo.get_bundle(bundle_id)

    def list_bundles(self) -> list[AgreementBundle]:
        return self._repo.list_bundles()

    def ingest_document(
        self, *, bundle_id: str, document_name: str, raw_bytes: bytes, actor_id: str
    ) -> DocumentNode:
        """Ingest one document's raw bytes into a bundle (plan Section 10.3).

        MIME is detected from the bytes themselves (magic-byte sniffing),
        never trusted from a filename/extension. Plain text/Markdown is
        normalized exactly as before this gap was closed (no behavior
        change for that path). A PDF is routed through
        `clauseos.core.pdf_ingestion`, which may itself refuse the document
        (malformed file, encrypted, oversized, too many pages, or an
        image-only scan with no extractable text layer) -- any such refusal
        raises `DocumentIngestionRejectedError` with the specific
        non-publishable status rather than silently admitting the document
        as if it had authoritative text.
        """
        bundle = self._repo.get_bundle(bundle_id)
        if bundle is None:
            raise KeyError(f"no such bundle: {bundle_id}")

        # SEC-04: deny-by-default authorization gate. Raises
        # AuthorizationDeniedError (mapped to HTTP 403 by clauseos_api) and
        # aborts before any bytes are admitted if denied.
        self._authz.authorize(
            action=ACTION_INGEST_DOCUMENT,
            purpose=PURPOSE_DOCUMENT_INGESTION,
            actor_id=actor_id,
            tenant_id=self._tenant_id_for_bundle(bundle),
            object_id=bundle_id,
            object_version=str(bundle.version),
            content_digest=f"sha256:{sha256_hex(raw_bytes)}",
            now=_clock(),
        )

        mime_type = detect_mime_type(raw_bytes)

        if mime_type is DocumentMimeType.PDF:
            result = ingest_pdf_bytes(raw_bytes)
            if not result.ok:
                self._record_audit(
                    bundle=bundle,
                    actor_id=actor_id,
                    action="DOCUMENT_INGESTION_REJECTED",
                    reason_code=f"{document_name}:{result.status.value}",
                )
                raise DocumentIngestionRejectedError(status=result.status, reason=result.reason or "")
            normalized = result.normalized_text
            normalized_sha256 = result.normalized_text_sha256
            assert normalized is not None and normalized_sha256 is not None
        elif mime_type is DocumentMimeType.PLAIN_TEXT:
            normalized = normalize_text(raw_bytes.decode("utf-8"))
            normalized_sha256 = sha256_hex_of_text(normalized)
        else:
            self._record_audit(
                bundle=bundle,
                actor_id=actor_id,
                action="DOCUMENT_INGESTION_REJECTED",
                reason_code=f"{document_name}:{BundleStatus.UNSUPPORTED_DOCUMENT.value}",
            )
            raise DocumentIngestionRejectedError(
                status=BundleStatus.UNSUPPORTED_DOCUMENT,
                reason="document bytes are neither valid UTF-8 text nor a %PDF- file",
            )

        document = DocumentNode(
            document_id=_new_id("doc"),
            bundle_id=bundle_id,
            document_name=document_name,
            raw_bytes_sha256=sha256_hex(raw_bytes),
            normalized_text=normalized,
            normalized_text_sha256=normalized_sha256,
        )
        bundle.add_document(document)
        self._repo.save_bundle(bundle)
        self._record_audit(
            bundle=bundle, actor_id=actor_id, action="DOCUMENT_INGESTED", reason_code=document_name
        )
        return document

    def approve_manifest(self, *, bundle_id: str, manifest: BundleManifest, actor_id: str) -> AgreementBundle:
        bundle = self._repo.get_bundle(bundle_id)
        if bundle is None:
            raise KeyError(f"no such bundle: {bundle_id}")
        bundle.approve_manifest(manifest)
        self._repo.save_bundle(bundle)
        self._record_audit(bundle=bundle, actor_id=actor_id, action="MANIFEST_APPROVED")
        try:
            bundle.check_completeness()
        except BundleIncompleteError as exc:
            self._repo.save_bundle(bundle)
            self._record_audit(
                bundle=bundle,
                actor_id=actor_id,
                action="BUNDLE_INCOMPLETE",
                reason_code=str(exc),
            )
            raise
        self._repo.save_bundle(bundle)
        self._record_audit(bundle=bundle, actor_id=actor_id, action="BUNDLE_PARSED")
        return bundle

    def _process_candidate(
        self,
        *,
        bundle_id: str,
        document_text: str,
        candidate,
        assertions: list[ContractAssertion],
    ) -> None:
        assertion = ContractAssertion(
            assertion_id=_new_id("assert"),
            bundle_id=bundle_id,
            concept=candidate.concept,
            candidate=candidate,
            status=AssertionStatus.PROPOSED,
            evidence_content_hash=compute_evidence_content_hash(candidate),
        )
        outcome = verify_evidence_span(document_text, candidate.evidence)
        if outcome.ok:
            assertion.mark_structurally_verified()
            assertion.mark_human_review_required()
        else:
            assertion.mark_conflict(outcome.reason or "structural verification failed")
        self._repo.save_assertion(assertion)
        assertions.append(assertion)

        if assertion.status is AssertionStatus.HUMAN_REVIEW_REQUIRED:
            review_case = ReviewCase(
                review_id=_new_id("review"),
                assertion_id=assertion.assertion_id,
                bundle_id=bundle_id,
                bound_evidence_hash=assertion.evidence_content_hash,
            )
            self._repo.save_review_case(review_case)

    def analyze_bundle(
        self,
        *,
        bundle_id: str,
        actor_id: str,
        include_model_proposer: bool = False,
        model_client=None,
        model_base_url: str | None = None,
        model_name: str | None = None,
        model_timeout: float | None = None,
    ) -> list[ContractAssertion]:
        """Run the deterministic rule proposer over every document in the
        bundle (unconditional, exactly as before this gap was closed -- the
        critical path never depends on a model, per ADR-006).

        `include_model_proposer=True` additionally runs the optional local
        model adapter (`core.model_adapter`, Gap CLO-X01) as a *third*,
        strictly additive proposer: its candidates go through the identical
        structural-verification and maker/checker review path as rule-based
        candidates -- there is no separate publication path for model
        output, and the model can never be required (this parameter
        defaults to `False`, so every existing call site and every
        pre-existing test is unaffected).
        """
        bundle = self._repo.get_bundle(bundle_id)
        if bundle is None:
            raise KeyError(f"no such bundle: {bundle_id}")

        # SEC-04: deny-by-default authorization gate for proposing
        # assertions (analysis).
        self._authz.authorize(
            action=ACTION_ANALYZE_BUNDLE,
            purpose=PURPOSE_PROPOSE_ASSERTION,
            actor_id=actor_id,
            tenant_id=self._tenant_id_for_bundle(bundle),
            object_id=bundle_id,
            object_version=str(bundle.version),
            content_digest=f"sha256:{sha256_hex(bundle_id.encode())}",
            now=_clock(),
        )

        assertions: list[ContractAssertion] = []
        for document in bundle.documents.values():
            candidates = propose_candidates(
                document_id=document.document_id,
                bundle_id=bundle_id,
                normalized_text=document.normalized_text,
                normalized_text_sha256=document.normalized_text_sha256,
            )
            for candidate in candidates:
                self._process_candidate(
                    bundle_id=bundle_id,
                    document_text=document.normalized_text,
                    candidate=candidate,
                    assertions=assertions,
                )

            if include_model_proposer:
                from clauseos.core.model_adapter import propose_candidates_from_model

                model_candidates = propose_candidates_from_model(
                    document_id=document.document_id,
                    bundle_id=bundle_id,
                    normalized_text=document.normalized_text,
                    normalized_text_sha256=document.normalized_text_sha256,
                    base_url=model_base_url,
                    model=model_name,
                    timeout=model_timeout,
                    client=model_client,
                )
                for candidate in model_candidates:
                    self._process_candidate(
                        bundle_id=bundle_id,
                        document_text=document.normalized_text,
                        candidate=candidate,
                        assertions=assertions,
                    )

        bundle.mark_analyzed()
        bundle.require_review()
        self._repo.save_bundle(bundle)
        self._record_audit(bundle=bundle, actor_id=actor_id, action="BUNDLE_ANALYZED")
        return assertions

    def list_assertions(
        self, *, bundle_id: str | None = None, concept: str | None = None, status: str | None = None
    ) -> list[ContractAssertion]:
        return self._repo.list_assertions(bundle_id=bundle_id, concept=concept, status=status)

    def list_review_cases(self, *, bundle_id: str | None = None) -> list[ReviewCase]:
        return self._repo.list_review_cases(bundle_id=bundle_id)

    def record_maker_decision(
        self, *, review_id: str, actor_id: str, decision: ReviewDecision
    ) -> ReviewCase:
        review_case = self._repo.get_review_case(review_id)
        if review_case is None:
            raise KeyError(f"no such review case: {review_id}")
        assertion = self._repo.get_assertion(review_case.assertion_id)
        if assertion is None:
            raise KeyError(f"no such assertion: {review_case.assertion_id}")
        bundle = self._repo.get_bundle(review_case.bundle_id)

        # SEC-04: deny-by-default authorization gate for the maker's
        # approve/reject decision.
        if bundle is not None:
            self._authz.authorize(
                action=ACTION_RECORD_MAKER_DECISION,
                purpose=PURPOSE_MAKER_REVIEW_DECISION,
                actor_id=actor_id,
                tenant_id=self._tenant_id_for_bundle(bundle),
                object_id=review_case.review_id,
                object_version=str(review_case.version),
                content_digest=f"sha256:{assertion.evidence_content_hash}",
                now=_clock(),
            )

        review_case.record_maker_decision(
            actor_id=actor_id,
            decision=decision,
            current_evidence_hash=assertion.evidence_content_hash,
            decided_at=_clock(),
        )
        self._repo.save_review_case(review_case)
        if bundle is not None:
            self._record_audit(
                bundle=bundle, actor_id=actor_id, action="MAKER_DECISION", reason_code=decision.value
            )
        return review_case

    def record_checker_decision(
        self,
        *,
        review_id: str,
        actor_id: str,
        decision: ReviewDecision,
        presented_revocation_epoch: int | None = None,
    ) -> ReviewCase:
        review_case = self._repo.get_review_case(review_id)
        if review_case is None:
            raise KeyError(f"no such review case: {review_id}")
        assertion = self._repo.get_assertion(review_case.assertion_id)
        if assertion is None:
            raise KeyError(f"no such assertion: {review_case.assertion_id}")
        bundle = self._repo.get_bundle(review_case.bundle_id)

        # SEC-04: deny-by-default authorization gate for the checker's
        # approve/reject decision.
        if bundle is not None:
            self._authz.authorize(
                action=ACTION_RECORD_CHECKER_DECISION,
                purpose=PURPOSE_CHECKER_REVIEW_DECISION,
                actor_id=actor_id,
                tenant_id=self._tenant_id_for_bundle(bundle),
                object_id=review_case.review_id,
                object_version=str(review_case.version),
                content_digest=f"sha256:{assertion.evidence_content_hash}",
                now=_clock(),
            )

        # SEC-05: maker/checker distinct-natural-person enforcement.
        # `clauseos.domain.review.ReviewCase.record_checker_decision` below
        # already enforces this itself (raises `SameActorError` on an exact
        # actor_id string match) -- this call is an ADDITIONAL,
        # defense-in-depth check using cassandre_security's
        # alias-resolution-ready `NaturalPersonDirectory` (S1 default:
        # identity function, so functionally equivalent to the domain
        # check today; the value is that a future alias-aware directory
        # plugged in here catches "two accounts, one person" self-approval
        # that the domain layer's plain string comparison cannot). It is
        # NOT a duplicate implementation of the domain rule -- the domain
        # layer remains the primary, authoritative enforcement point and
        # keeps its own `SameActorError`/409 contract; this call is
        # translated to that same error type so external behavior is
        # unchanged. Only runs once a maker decision actually exists (a
        # review case still OPEN has no maker to compare against, and the
        # domain method below already raises InvalidTransitionError for
        # that case unchanged).
        if review_case.maker_approval is not None:
            try:
                enforce_distinct_natural_persons(
                    review_case.maker_approval.actor_id,
                    actor_id,
                    directory=self._natural_person_directory,
                )
            except DistinctActorViolation as exc:
                raise SameActorError(str(exc)) from exc

        # SEC-14: revocation/security-fence gating for this critical
        # mutation (checker approval), when a fence gateway is configured.
        # Additive: `self._fence_gateway is None` (the default) skips this
        # entirely, so every pre-existing test/call site is unaffected.
        if self._fence_gateway is not None and bundle is not None:
            scope_key = clauseos_fence_scope_key(
                tenant_id=self._tenant_id_for_bundle(bundle), object_id=review_case.review_id
            )
            self._fence_gateway.lock_for_mutation(
                scope_key=scope_key,
                presented_epoch=presented_revocation_epoch if presented_revocation_epoch is not None else 0,
                now=_clock(),
            )

        review_case.record_checker_decision(
            actor_id=actor_id,
            decision=decision,
            current_evidence_hash=assertion.evidence_content_hash,
            decided_at=_clock(),
        )
        self._repo.save_review_case(review_case)
        if decision is ReviewDecision.APPROVE:
            assertion.mark_approved()
            self._repo.save_assertion(assertion)
        if bundle is not None:
            self._record_audit(
                bundle=bundle, actor_id=actor_id, action="CHECKER_DECISION", reason_code=decision.value
            )
        return review_case

    def publish_policy_release(
        self,
        *,
        bundle_id: str,
        actor_id: str,
        keypair: Ed25519KeyPair,
        issuer: str = "clauseos_api",
        presented_revocation_epoch: int | None = None,
    ):
        bundle = self._repo.get_bundle(bundle_id)
        if bundle is None:
            raise KeyError(f"no such bundle: {bundle_id}")

        # SEC-04: deny-by-default authorization gate for publishing a
        # policy release.
        self._authz.authorize(
            action=ACTION_PUBLISH_POLICY_RELEASE,
            purpose=PURPOSE_POLICY_RELEASE_PUBLICATION,
            actor_id=actor_id,
            tenant_id=self._tenant_id_for_bundle(bundle),
            object_id=bundle_id,
            object_version=str(bundle.version),
            content_digest=f"sha256:{sha256_hex(bundle_id.encode())}",
            now=_clock(),
        )

        # SEC-14: revocation/security-fence gating for this critical
        # mutation. Additive: `self._fence_gateway is None` (the default)
        # skips this entirely, so every pre-existing test/call site is
        # unaffected.
        if self._fence_gateway is not None:
            scope_key = clauseos_fence_scope_key(tenant_id=self._tenant_id_for_bundle(bundle), object_id=bundle_id)
            self._fence_gateway.lock_for_mutation(
                scope_key=scope_key,
                presented_epoch=presented_revocation_epoch if presented_revocation_epoch is not None else 0,
                now=_clock(),
            )

        assertions = {a.assertion_id: a for a in self._repo.list_assertions(bundle_id=bundle_id)}
        review_cases = {
            rc.assertion_id: rc for rc in self._repo.list_review_cases(bundle_id=bundle_id)
        }

        release = build_policy_release(
            release_id=_new_id("release"),
            bundle_id=bundle_id,
            scenario_id=bundle.scenario_id,
            created_at=_clock(),
            assertions=assertions,
            review_cases_by_assertion_id=review_cases,
        )

        bundle.mark_validated()
        bundle.mark_approved()
        bundle.mark_published()
        self._repo.save_bundle(bundle)

        release.mark_published()
        self._repo.save_policy_release(release)

        for assertion in assertions.values():
            if assertion.status is AssertionStatus.APPROVED:
                assertion.mark_published()
                self._repo.save_assertion(assertion)

        self._record_audit(bundle=bundle, actor_id=actor_id, action="POLICY_RELEASE_PUBLISHED")

        envelope = build_policy_release_envelope(
            release=release,
            issuer=issuer,
            keypair=keypair,
            created_at=_clock(),
        )
        return release, envelope

    def list_policy_releases(self, *, bundle_id: str | None = None):
        return self._repo.list_policy_releases(bundle_id=bundle_id)

    def revoke_policy_release(self, *, release_id: str, actor_id: str):
        release = self._repo.get_policy_release(release_id)
        if release is None:
            raise KeyError(f"no such policy release: {release_id}")
        release.revoke()
        self._repo.save_policy_release(release)
        bundle = self._repo.get_bundle(release.bundle_id)
        if bundle is not None:
            self._record_audit(
                bundle=bundle,
                actor_id=actor_id,
                action="POLICY_RELEASE_REVOKED",
                reason_code=release_id,
            )
        return release

    def get_document_text(self, *, bundle_id: str, document_name: str) -> DocumentNode | None:
        bundle = self._repo.get_bundle(bundle_id)
        if bundle is None:
            return None
        return bundle.documents.get(document_name)

    def list_audit_events(self, *, bundle_id: str):
        return self._repo.list_audit_events(bundle_id)

    def authorize_export_evidence_bundle(self, *, bundle_id: str, actor_id: str) -> None:
        """SEC-04: deny-by-default authorization gate for evidence export.
        Raises `AuthorizationDeniedError` (mapped to HTTP 403 by
        clauseos_api) if denied; callers must not proceed to assemble/
        return the export on a DENY. Kept as a small standalone gate
        (rather than folding the whole export assembly into this service
        method) so `apps/clauseos_api`'s existing response-shaping code is
        untouched."""
        bundle = self._repo.get_bundle(bundle_id)
        if bundle is None:
            raise KeyError(f"no such bundle: {bundle_id}")
        self._authz.authorize(
            action=ACTION_EXPORT_EVIDENCE_BUNDLE,
            purpose=PURPOSE_EVIDENCE_EXPORT,
            actor_id=actor_id,
            tenant_id=self._tenant_id_for_bundle(bundle),
            object_id=bundle_id,
            object_version=str(bundle.version),
            content_digest=f"sha256:{sha256_hex(bundle_id.encode())}",
            now=_clock(),
        )

    # -- SEC-08: additive envelope-encryption capability (see
    # clauseos.application.document_sealing). Does not replace or alter
    # DocumentNode's existing plain-text storage; a caller reaches for
    # this only when it wants a document's original bytes held under
    # envelope encryption at rest. --

    def seal_restricted_document_bytes(self, *, document_id: str, raw_bytes: bytes):
        return self._document_sealing.seal_document_bytes(raw_bytes=raw_bytes, document_id=document_id)

    def open_restricted_document_bytes(self, *, document_id: str, envelope) -> bytes:
        return self._document_sealing.open_document_bytes(envelope, document_id=document_id)

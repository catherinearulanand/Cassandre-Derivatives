"""Conversions between ClauseOS domain dataclasses and SQLAlchemy rows."""

from __future__ import annotations

import datetime as dt

from money_types import Money
from clauseos.adapters.db.models import (
    AgreementBundleRow,
    AssertionRow,
    DocumentNodeRow,
    EvidenceSpanRow,
    PolicyReleaseRow,
    ReviewCaseRow,
)
from clauseos.domain.assertion import AssertionCandidate, ContractAssertion
from clauseos.domain.bundle import AgreementBundle, BundleManifest, DocumentNode, DocumentReference
from clauseos.domain.evidence import EvidenceSpan
from clauseos.domain.policy import PolicyRelease, PublishedTerm
from clauseos.domain.review import Approval, ReviewCase
from clauseos.domain.status import (
    AssertionStatus,
    BundleStatus,
    MaterialTermConcept,
    PolicyReleaseStatus,
    ReviewCaseStatus,
    ReviewDecision,
)
from clauseos.domain.values import DayCountBasis, MoneyTermValue, NotificationDeadlineValue, TermValue


def _ensure_utc(value: dt.datetime) -> dt.datetime:
    """Some drivers (notably SQLite, used only for ad-hoc local smoke tests --
    the real target is Postgres per the database ownership map) return a
    naive datetime even from a `DateTime(timezone=True)` column. Every
    timestamp this system stores is already a UTC instant, so a naive value
    read back is assumed to already be UTC, never re-interpreted as local
    time via `astimezone`."""
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


def manifest_to_json(manifest: BundleManifest | None) -> dict | None:
    if manifest is None:
        return None
    return {
        "document_type": manifest.document_type,
        "parties": list(manifest.parties),
        "execution_date": manifest.execution_date.isoformat(),
        "effective_date": manifest.effective_date.isoformat(),
        "related_documents": [
            {"document_name": r.document_name, "relationship": r.relationship}
            for r in manifest.related_documents
        ],
        "incorporated_by_reference": list(manifest.incorporated_by_reference),
    }


def manifest_from_json(data: dict | None) -> BundleManifest | None:
    if data is None:
        return None
    return BundleManifest(
        document_type=data["document_type"],
        parties=tuple(data["parties"]),
        execution_date=dt.date.fromisoformat(data["execution_date"]),
        effective_date=dt.date.fromisoformat(data["effective_date"]),
        related_documents=tuple(
            DocumentReference(document_name=r["document_name"], relationship=r["relationship"])
            for r in data.get("related_documents", [])
        ),
        incorporated_by_reference=tuple(data.get("incorporated_by_reference", [])),
    )


def bundle_to_row(bundle: AgreementBundle) -> AgreementBundleRow:
    return AgreementBundleRow(
        bundle_id=bundle.bundle_id,
        scenario_id=bundle.scenario_id,
        status=bundle.status.value,
        created_at=bundle.created_at,
        manifest_json=manifest_to_json(bundle.manifest),
        manifest_approved=bundle.manifest_approved,
        incompleteness_reason=bundle.incompleteness_reason,
        version=bundle.version,
    )


def bundle_from_row(row: AgreementBundleRow, documents: dict[str, DocumentNode]) -> AgreementBundle:
    return AgreementBundle(
        bundle_id=row.bundle_id,
        scenario_id=row.scenario_id,
        status=BundleStatus(row.status),
        created_at=_ensure_utc(row.created_at),
        documents=documents,
        manifest=manifest_from_json(row.manifest_json),
        manifest_approved=row.manifest_approved,
        incompleteness_reason=row.incompleteness_reason,
        version=row.version,
    )


def document_to_row(document: DocumentNode) -> DocumentNodeRow:
    return DocumentNodeRow(
        document_id=document.document_id,
        bundle_id=document.bundle_id,
        document_name=document.document_name,
        raw_bytes_sha256=document.raw_bytes_sha256,
        normalized_text=document.normalized_text,
        normalized_text_sha256=document.normalized_text_sha256,
    )


def document_from_row(row: DocumentNodeRow) -> DocumentNode:
    return DocumentNode(
        document_id=row.document_id,
        bundle_id=row.bundle_id,
        document_name=row.document_name,
        raw_bytes_sha256=row.raw_bytes_sha256,
        normalized_text=row.normalized_text,
        normalized_text_sha256=row.normalized_text_sha256,
    )


def evidence_to_row(span: EvidenceSpan) -> EvidenceSpanRow:
    return EvidenceSpanRow(
        span_id=span.span_id,
        document_id=span.document_id,
        start_offset=span.start_offset,
        end_offset=span.end_offset,
        snippet=span.snippet,
        source_content_hash=span.source_content_hash,
    )


def evidence_from_row(row: EvidenceSpanRow) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=row.span_id,
        document_id=row.document_id,
        start_offset=row.start_offset,
        end_offset=row.end_offset,
        snippet=row.snippet,
        source_content_hash=row.source_content_hash,
    )


def term_value_to_json(value: TermValue) -> dict:
    return value.as_canonical_dict()


def term_value_from_json(data: dict) -> TermValue:
    if data["kind"] == "MONEY":
        return MoneyTermValue(
            money=Money(amount=data["amount"], currency=data["currency"], rounding_context=data["rounding_context"])
        )
    if data["kind"] == "NOTIFICATION_DEADLINE":
        return NotificationDeadlineValue(days=data["days"], basis=DayCountBasis(data["basis"]))
    raise ValueError(f"unknown term value kind: {data['kind']!r}")


def assertion_to_row(assertion: ContractAssertion) -> AssertionRow:
    return AssertionRow(
        assertion_id=assertion.assertion_id,
        bundle_id=assertion.bundle_id,
        document_id=assertion.candidate.document_id,
        span_id=assertion.candidate.evidence.span_id,
        concept=assertion.concept.value,
        status=assertion.status.value,
        proposer=assertion.candidate.proposer,
        candidate_id=assertion.candidate.candidate_id,
        proposed_value_json=term_value_to_json(assertion.candidate.proposed_value),
        evidence_content_hash=assertion.evidence_content_hash,
        conflict_reason=assertion.conflict_reason,
        version=assertion.version,
    )


def assertion_from_row(row: AssertionRow, evidence: EvidenceSpan) -> ContractAssertion:
    candidate = AssertionCandidate(
        candidate_id=row.candidate_id,
        bundle_id=row.bundle_id,
        document_id=row.document_id,
        concept=MaterialTermConcept(row.concept),
        proposed_value=term_value_from_json(row.proposed_value_json),
        evidence=evidence,
        proposer=row.proposer,
    )
    return ContractAssertion(
        assertion_id=row.assertion_id,
        bundle_id=row.bundle_id,
        concept=MaterialTermConcept(row.concept),
        candidate=candidate,
        status=AssertionStatus(row.status),
        evidence_content_hash=row.evidence_content_hash,
        version=row.version,
        conflict_reason=row.conflict_reason,
    )


def review_case_to_row(review_case: ReviewCase) -> ReviewCaseRow:
    return ReviewCaseRow(
        review_id=review_case.review_id,
        assertion_id=review_case.assertion_id,
        bundle_id=review_case.bundle_id,
        bound_evidence_hash=review_case.bound_evidence_hash,
        status=review_case.status.value,
        maker_actor_id=review_case.maker_approval.actor_id if review_case.maker_approval else None,
        maker_decision=review_case.maker_approval.decision.value if review_case.maker_approval else None,
        maker_decided_at=review_case.maker_approval.decided_at if review_case.maker_approval else None,
        checker_actor_id=review_case.checker_approval.actor_id if review_case.checker_approval else None,
        checker_decision=review_case.checker_approval.decision.value if review_case.checker_approval else None,
        checker_decided_at=review_case.checker_approval.decided_at if review_case.checker_approval else None,
        version=review_case.version,
    )


def review_case_from_row(row: ReviewCaseRow) -> ReviewCase:
    maker_approval = None
    if row.maker_actor_id is not None:
        maker_approval = Approval(
            actor_id=row.maker_actor_id,
            role="MAKER",
            decision=ReviewDecision(row.maker_decision),
            decided_at=_ensure_utc(row.maker_decided_at),
            bound_evidence_hash=row.bound_evidence_hash,
        )
    checker_approval = None
    if row.checker_actor_id is not None:
        checker_approval = Approval(
            actor_id=row.checker_actor_id,
            role="CHECKER",
            decision=ReviewDecision(row.checker_decision),
            decided_at=_ensure_utc(row.checker_decided_at),
            bound_evidence_hash=row.bound_evidence_hash,
        )
    return ReviewCase(
        review_id=row.review_id,
        assertion_id=row.assertion_id,
        bundle_id=row.bundle_id,
        bound_evidence_hash=row.bound_evidence_hash,
        status=ReviewCaseStatus(row.status),
        maker_approval=maker_approval,
        checker_approval=checker_approval,
        version=row.version,
    )


def policy_release_to_row(release: PolicyRelease) -> PolicyReleaseRow:
    return PolicyReleaseRow(
        release_id=release.release_id,
        bundle_id=release.bundle_id,
        scenario_id=release.scenario_id,
        created_at=release.created_at,
        status=release.status.value,
        terms_json=[
            {
                "concept": t.concept,
                "assertion_id": t.assertion_id,
                "evidence_content_hash": t.evidence_content_hash,
                "checker_actor_id": t.checker_actor_id,
                "value": t.value_dict,
            }
            for t in release.terms
        ],
    )


def policy_release_from_row(row: PolicyReleaseRow) -> PolicyRelease:
    return PolicyRelease(
        release_id=row.release_id,
        bundle_id=row.bundle_id,
        scenario_id=row.scenario_id,
        created_at=_ensure_utc(row.created_at),
        status=PolicyReleaseStatus(row.status),
        terms=tuple(
            PublishedTerm(
                concept=t["concept"],
                assertion_id=t["assertion_id"],
                evidence_content_hash=t["evidence_content_hash"],
                checker_actor_id=t["checker_actor_id"],
                value_dict=t["value"],
            )
            for t in row.terms_json
        ),
    )

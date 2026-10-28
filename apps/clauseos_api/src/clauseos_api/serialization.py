"""JSON-safe serialization of ClauseOS domain objects for API responses.
Decimal amounts always render as decimal strings, never JSON floats
(plan Section 25.3)."""

from __future__ import annotations

from typing import Any

from clauseos.domain.assertion import ContractAssertion
from clauseos.domain.bundle import AgreementBundle
from clauseos.domain.policy import PolicyRelease
from clauseos.domain.review import ReviewCase
from clauseos.domain.values import term_value_to_canonical_dict


def bundle_to_response(bundle: AgreementBundle) -> dict[str, Any]:
    return {
        "bundle_id": bundle.bundle_id,
        "scenario_id": bundle.scenario_id,
        "status": bundle.status.value,
        "version": bundle.version,
        "manifest_approved": bundle.manifest_approved,
        "incompleteness_reason": bundle.incompleteness_reason,
        "documents": sorted(bundle.documents.keys()),
        "manifest": (
            None
            if bundle.manifest is None
            else {
                "document_type": bundle.manifest.document_type,
                "parties": list(bundle.manifest.parties),
                "execution_date": bundle.manifest.execution_date.isoformat(),
                "effective_date": bundle.manifest.effective_date.isoformat(),
                "incorporated_by_reference": list(bundle.manifest.incorporated_by_reference),
                "related_documents": [
                    {"document_name": r.document_name, "relationship": r.relationship}
                    for r in bundle.manifest.related_documents
                ],
            }
        ),
    }


def assertion_to_response(assertion: ContractAssertion) -> dict[str, Any]:
    return {
        "assertion_id": assertion.assertion_id,
        "bundle_id": assertion.bundle_id,
        "concept": assertion.concept.value,
        "status": assertion.status.value,
        "version": assertion.version,
        "evidence_content_hash": assertion.evidence_content_hash,
        "conflict_reason": assertion.conflict_reason,
        "proposer": assertion.candidate.proposer,
        "proposed_value": term_value_to_canonical_dict(assertion.candidate.proposed_value),
        "evidence": {
            "document_id": assertion.candidate.evidence.document_id,
            "start_offset": assertion.candidate.evidence.start_offset,
            "end_offset": assertion.candidate.evidence.end_offset,
            "snippet": assertion.candidate.evidence.snippet,
            "source_content_hash": assertion.candidate.evidence.source_content_hash,
        },
    }


def review_case_to_response(review_case: ReviewCase) -> dict[str, Any]:
    return {
        "review_id": review_case.review_id,
        "assertion_id": review_case.assertion_id,
        "bundle_id": review_case.bundle_id,
        "status": review_case.status.value,
        "version": review_case.version,
        "maker": (
            None
            if review_case.maker_approval is None
            else {
                "actor_id": review_case.maker_approval.actor_id,
                "decision": review_case.maker_approval.decision.value,
            }
        ),
        "checker": (
            None
            if review_case.checker_approval is None
            else {
                "actor_id": review_case.checker_approval.actor_id,
                "decision": review_case.checker_approval.decision.value,
            }
        ),
    }


def policy_release_to_response(release: PolicyRelease) -> dict[str, Any]:
    return {
        "release_id": release.release_id,
        "bundle_id": release.bundle_id,
        "scenario_id": release.scenario_id,
        "status": release.status.value,
        "terms": [
            {
                "concept": t.concept,
                "assertion_id": t.assertion_id,
                "evidence_content_hash": t.evidence_content_hash,
                "checker_actor_id": t.checker_actor_id,
                "value": t.value_dict,
            }
            for t in release.terms
        ],
    }

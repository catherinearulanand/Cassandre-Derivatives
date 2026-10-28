"""AssertionCandidate (proposer output) and ContractAssertion (the tracked,
reviewable object built from a candidate), per plan Section 10.1/10.4/10.5.

`AssertionCandidate` is immutable -- it is exactly what a proposer emitted.
`ContractAssertion` carries the mutable review lifecycle (status, version)
around one candidate, plus `evidence_content_hash`, which is the value the
maker/checker compare-and-swap in `domain/review.py` binds to: if the
evidence changes (re-verification finds different text), this hash changes,
and a checker decision bound to the old hash must fail closed rather than
silently proceed (plan Section 10.5).
"""

from __future__ import annotations

from dataclasses import dataclass

from audit_protocol.canonical import content_hash
from clauseos.domain.evidence import EvidenceSpan
from clauseos.domain.status import AssertionStatus, MaterialTermConcept
from clauseos.domain.values import TermValue, term_value_to_canonical_dict

PROPOSER_RULE = "RULE"


@dataclass(frozen=True)
class AssertionCandidate:
    candidate_id: str
    bundle_id: str
    document_id: str
    concept: MaterialTermConcept
    proposed_value: TermValue
    evidence: EvidenceSpan
    proposer: str = PROPOSER_RULE


def compute_evidence_content_hash(candidate: AssertionCandidate) -> str:
    """Binds together the exact candidate value and the exact evidence span
    it was derived from. Any change to either changes this hash, which is
    what makes the maker/checker compare-and-swap in `review.py` detect a
    stale review."""
    payload = {
        "concept": candidate.concept.value,
        "proposed_value": term_value_to_canonical_dict(candidate.proposed_value),
        "evidence": {
            "document_id": candidate.evidence.document_id,
            "start_offset": candidate.evidence.start_offset,
            "end_offset": candidate.evidence.end_offset,
            "snippet": candidate.evidence.snippet,
            "source_content_hash": candidate.evidence.source_content_hash,
        },
    }
    return content_hash(payload)


@dataclass
class ContractAssertion:
    assertion_id: str
    bundle_id: str
    concept: MaterialTermConcept
    candidate: AssertionCandidate
    status: AssertionStatus
    evidence_content_hash: str
    version: int = 0
    conflict_reason: str | None = None

    def mark_structurally_verified(self) -> None:
        self.status = AssertionStatus.STRUCTURALLY_VERIFIED
        self.version += 1

    def mark_conflict(self, reason: str) -> None:
        self.status = AssertionStatus.CONFLICT
        self.conflict_reason = reason
        self.version += 1

    def mark_human_review_required(self) -> None:
        if self.status != AssertionStatus.STRUCTURALLY_VERIFIED:
            raise ValueError(
                f"assertion {self.assertion_id} must be STRUCTURALLY_VERIFIED before "
                "entering human review, not "
                f"{self.status.value}"
            )
        self.status = AssertionStatus.HUMAN_REVIEW_REQUIRED
        self.version += 1

    def mark_approved(self) -> None:
        if self.status != AssertionStatus.HUMAN_REVIEW_REQUIRED:
            raise ValueError(
                f"assertion {self.assertion_id} must be HUMAN_REVIEW_REQUIRED before "
                f"approval, not {self.status.value}"
            )
        self.status = AssertionStatus.APPROVED
        self.version += 1

    def mark_published(self) -> None:
        if self.status != AssertionStatus.APPROVED:
            raise ValueError(
                f"assertion {self.assertion_id} must be APPROVED before publish, not "
                f"{self.status.value}"
            )
        self.status = AssertionStatus.PUBLISHED
        self.version += 1

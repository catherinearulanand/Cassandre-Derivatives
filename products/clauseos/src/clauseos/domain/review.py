"""Maker/checker review workflow, per plan Section 10.5:

"The maker and checker must be distinct identities. Approval is bound to the
exact candidate/evidence/release hash. Compare-and-swap prevents a stale
review from publishing."

`ReviewCase.bound_evidence_hash` snapshots the assertion's
`evidence_content_hash` at review-case creation time. Both the maker and
checker decisions must present the assertion's *current* evidence hash;
if it no longer matches (the evidence was re-verified/changed since the
case opened, or since the maker acted), the decision is rejected with
`StaleReviewError` rather than silently proceeding.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from clauseos.domain.errors import InvalidTransitionError, SameActorError, StaleReviewError
from clauseos.domain.status import ReviewCaseStatus, ReviewDecision


@dataclass(frozen=True)
class Approval:
    actor_id: str
    role: str  # "MAKER" | "CHECKER"
    decision: ReviewDecision
    decided_at: dt.datetime
    bound_evidence_hash: str


@dataclass
class ReviewCase:
    review_id: str
    assertion_id: str
    bundle_id: str
    bound_evidence_hash: str
    status: ReviewCaseStatus = ReviewCaseStatus.OPEN
    maker_approval: Approval | None = None
    checker_approval: Approval | None = None
    version: int = 0

    def record_maker_decision(
        self,
        *,
        actor_id: str,
        decision: ReviewDecision,
        current_evidence_hash: str,
        decided_at: dt.datetime,
    ) -> None:
        if self.status != ReviewCaseStatus.OPEN:
            raise InvalidTransitionError(
                f"review case {self.review_id} is {self.status.value}, not OPEN; "
                "cannot record a maker decision"
            )
        if current_evidence_hash != self.bound_evidence_hash:
            raise StaleReviewError(
                f"review case {self.review_id}: assertion evidence has changed since "
                "this review case was opened; refusing to record a maker decision "
                "against stale evidence"
            )
        self.maker_approval = Approval(
            actor_id=actor_id,
            role="MAKER",
            decision=decision,
            decided_at=decided_at,
            bound_evidence_hash=current_evidence_hash,
        )
        self.status = (
            ReviewCaseStatus.MAKER_APPROVED
            if decision is ReviewDecision.APPROVE
            else ReviewCaseStatus.MAKER_REJECTED
        )
        self.version += 1

    def record_checker_decision(
        self,
        *,
        actor_id: str,
        decision: ReviewDecision,
        current_evidence_hash: str,
        decided_at: dt.datetime,
    ) -> None:
        if self.status != ReviewCaseStatus.MAKER_APPROVED:
            raise InvalidTransitionError(
                f"review case {self.review_id} is {self.status.value}, not "
                "MAKER_APPROVED; cannot record a checker decision"
            )
        assert self.maker_approval is not None  # guaranteed by MAKER_APPROVED status
        if actor_id == self.maker_approval.actor_id:
            raise SameActorError(
                f"review case {self.review_id}: checker {actor_id!r} must not be the "
                "same identity as the maker"
            )
        if current_evidence_hash != self.bound_evidence_hash:
            # Fail closed: a stale review case can reject, but it can never
            # be the vehicle for a checker approval to publish (ADR-014).
            self.status = ReviewCaseStatus.STALE_REJECTED
            self.version += 1
            raise StaleReviewError(
                f"review case {self.review_id}: assertion evidence changed after the "
                "maker decision; refusing the checker decision (compare-and-swap failed)"
            )
        self.checker_approval = Approval(
            actor_id=actor_id,
            role="CHECKER",
            decision=decision,
            decided_at=decided_at,
            bound_evidence_hash=current_evidence_hash,
        )
        self.status = (
            ReviewCaseStatus.CHECKER_APPROVED
            if decision is ReviewDecision.APPROVE
            else ReviewCaseStatus.CHECKER_REJECTED
        )
        self.version += 1

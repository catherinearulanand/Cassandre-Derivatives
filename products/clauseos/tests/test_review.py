import datetime as dt

import pytest

from clauseos.domain.errors import SameActorError, StaleReviewError
from clauseos.domain.review import ReviewCase
from clauseos.domain.status import ReviewCaseStatus, ReviewDecision

NOW = dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc)


def _open_case(evidence_hash: str = "hash-a") -> ReviewCase:
    return ReviewCase(
        review_id="review-1",
        assertion_id="assertion-1",
        bundle_id="bundle-1",
        bound_evidence_hash=evidence_hash,
    )


def test_maker_then_different_checker_approves():
    case = _open_case()
    case.record_maker_decision(
        actor_id="maker-1", decision=ReviewDecision.APPROVE, current_evidence_hash="hash-a", decided_at=NOW
    )
    assert case.status is ReviewCaseStatus.MAKER_APPROVED

    case.record_checker_decision(
        actor_id="checker-1", decision=ReviewDecision.APPROVE, current_evidence_hash="hash-a", decided_at=NOW
    )
    assert case.status is ReviewCaseStatus.CHECKER_APPROVED
    assert case.checker_approval.actor_id == "checker-1"


def test_maker_and_checker_must_be_distinct_identities():
    case = _open_case()
    case.record_maker_decision(
        actor_id="same-person", decision=ReviewDecision.APPROVE, current_evidence_hash="hash-a", decided_at=NOW
    )
    with pytest.raises(SameActorError):
        case.record_checker_decision(
            actor_id="same-person",
            decision=ReviewDecision.APPROVE,
            current_evidence_hash="hash-a",
            decided_at=NOW,
        )
    # A rejected attempt must not silently flip the case to checker-approved.
    assert case.status is ReviewCaseStatus.MAKER_APPROVED


def test_stale_evidence_blocks_maker_decision():
    case = _open_case(evidence_hash="hash-a")
    with pytest.raises(StaleReviewError):
        case.record_maker_decision(
            actor_id="maker-1",
            decision=ReviewDecision.APPROVE,
            current_evidence_hash="hash-b",  # evidence changed since case opened
            decided_at=NOW,
        )
    assert case.status is ReviewCaseStatus.OPEN


def test_stale_evidence_blocks_checker_decision_after_maker_approved():
    """Plan Section 10.5 compare-and-swap: if the assertion's evidence
    changed after the maker reviewed it, the checker decision must fail
    closed, not silently publish against stale evidence."""
    case = _open_case(evidence_hash="hash-a")
    case.record_maker_decision(
        actor_id="maker-1", decision=ReviewDecision.APPROVE, current_evidence_hash="hash-a", decided_at=NOW
    )
    with pytest.raises(StaleReviewError):
        case.record_checker_decision(
            actor_id="checker-1",
            decision=ReviewDecision.APPROVE,
            current_evidence_hash="hash-changed",
            decided_at=NOW,
        )
    assert case.status is ReviewCaseStatus.STALE_REJECTED
    assert case.checker_approval is None


def test_checker_decision_before_maker_decision_is_rejected():
    case = _open_case()
    with pytest.raises(Exception):
        case.record_checker_decision(
            actor_id="checker-1", decision=ReviewDecision.APPROVE, current_evidence_hash="hash-a", decided_at=NOW
        )

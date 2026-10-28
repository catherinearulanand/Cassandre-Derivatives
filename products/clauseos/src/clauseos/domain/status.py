"""Exhaustive, closed status enums for ClauseOS (plan Section 10.2, ADR-014).

These are deliberately the full state lists from the plan, not just the
subset this session's slice reaches, so that `match`-exhaustiveness checks
(and a future mypy/ruff exhaustiveness gate) catch an unhandled member at
review time rather than falling through to a silent default at runtime.
"""

from __future__ import annotations

from enum import Enum


class BundleStatus(str, Enum):
    CREATED = "CREATED"
    INGESTING = "INGESTING"
    PARSED = "PARSED"
    ANALYZED = "ANALYZED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"
    # Non-publishable terminal/exception states (plan Section 10.2).
    BUNDLE_INCOMPLETE = "BUNDLE_INCOMPLETE"
    ABSTAIN = "ABSTAIN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    OCR_UNCERTAIN = "OCR_UNCERTAIN"
    UNSUPPORTED_DOCUMENT = "UNSUPPORTED_DOCUMENT"
    EXTERNAL_STATE_REQUIRED = "EXTERNAL_STATE_REQUIRED"
    FAILED = "FAILED"


NON_PUBLISHABLE_BUNDLE_STATUSES = frozenset(
    {
        BundleStatus.BUNDLE_INCOMPLETE,
        BundleStatus.ABSTAIN,
        BundleStatus.NOT_FOUND,
        BundleStatus.CONFLICT,
        BundleStatus.OCR_UNCERTAIN,
        BundleStatus.UNSUPPORTED_DOCUMENT,
        BundleStatus.EXTERNAL_STATE_REQUIRED,
        BundleStatus.FAILED,
        BundleStatus.SUPERSEDED,
        BundleStatus.REVOKED,
    }
)


class AssertionStatus(str, Enum):
    PROPOSED = "PROPOSED"
    STRUCTURALLY_VERIFIED = "STRUCTURALLY_VERIFIED"
    SEMANTIC_REVIEW = "SEMANTIC_REVIEW"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    VERIFIED = "VERIFIED"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    # Non-publishable terminal/exception states (plan Section 10.2).
    ABSTAIN = "ABSTAIN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    OCR_UNCERTAIN = "OCR_UNCERTAIN"
    UNSUPPORTED_DOCUMENT = "UNSUPPORTED_DOCUMENT"
    EXTERNAL_STATE_REQUIRED = "EXTERNAL_STATE_REQUIRED"
    FAILED = "FAILED"


NON_PUBLISHABLE_ASSERTION_STATUSES = frozenset(
    {
        AssertionStatus.ABSTAIN,
        AssertionStatus.NOT_FOUND,
        AssertionStatus.CONFLICT,
        AssertionStatus.OCR_UNCERTAIN,
        AssertionStatus.UNSUPPORTED_DOCUMENT,
        AssertionStatus.EXTERNAL_STATE_REQUIRED,
        AssertionStatus.FAILED,
    }
)


class ReviewCaseStatus(str, Enum):
    OPEN = "OPEN"
    MAKER_APPROVED = "MAKER_APPROVED"
    MAKER_REJECTED = "MAKER_REJECTED"
    CHECKER_APPROVED = "CHECKER_APPROVED"
    CHECKER_REJECTED = "CHECKER_REJECTED"
    STALE_REJECTED = "STALE_REJECTED"


class ReviewDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class MaterialTermConcept(str, Enum):
    """Plan Section 10.5 lists many material terms; this slice implements
    the minimum the task calls out: threshold, MTA, notification deadline."""

    THRESHOLD = "THRESHOLD"
    MTA = "MTA"
    NOTIFICATION_DEADLINE = "NOTIFICATION_DEADLINE"


REQUIRED_MATERIAL_TERM_CONCEPTS = frozenset(
    {
        MaterialTermConcept.THRESHOLD,
        MaterialTermConcept.MTA,
        MaterialTermConcept.NOTIFICATION_DEADLINE,
    }
)


class PolicyReleaseStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    REVOKED = "REVOKED"

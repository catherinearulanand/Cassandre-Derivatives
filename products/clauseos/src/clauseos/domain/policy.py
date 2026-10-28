"""PolicyRelease: the published, checker-approved set of material terms for
a bundle (plan Section 10.6/10.1). Building one enforces that every required
material-term concept (Section 25.5 slice: THRESHOLD, MTA,
NOTIFICATION_DEADLINE) has a CHECKER_APPROVED review case bound to the
assertion's current evidence -- otherwise `IncompletePolicyError` is raised
rather than releasing a partial policy.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from clauseos.domain.assertion import ContractAssertion
from clauseos.domain.errors import IncompletePolicyError
from clauseos.domain.review import ReviewCase
from clauseos.domain.status import (
    REQUIRED_MATERIAL_TERM_CONCEPTS,
    AssertionStatus,
    PolicyReleaseStatus,
    ReviewCaseStatus,
)


@dataclass(frozen=True)
class PublishedTerm:
    concept: str
    assertion_id: str
    evidence_content_hash: str
    checker_actor_id: str
    value_dict: dict


@dataclass
class PolicyRelease:
    release_id: str
    bundle_id: str
    scenario_id: str
    created_at: dt.datetime
    terms: tuple[PublishedTerm, ...]
    status: PolicyReleaseStatus = PolicyReleaseStatus.DRAFT

    def as_canonical_dict(self) -> dict:
        return {
            "release_id": self.release_id,
            "bundle_id": self.bundle_id,
            "scenario_id": self.scenario_id,
            "created_at": self.created_at.astimezone(dt.timezone.utc).isoformat(),
            "terms": [
                {
                    "concept": t.concept,
                    "assertion_id": t.assertion_id,
                    "evidence_content_hash": t.evidence_content_hash,
                    "checker_actor_id": t.checker_actor_id,
                    "value": t.value_dict,
                }
                for t in sorted(self.terms, key=lambda t: t.concept)
            ],
        }

    def mark_published(self) -> None:
        self.status = PolicyReleaseStatus.PUBLISHED

    def revoke(self) -> None:
        self.status = PolicyReleaseStatus.REVOKED


def build_policy_release(
    *,
    release_id: str,
    bundle_id: str,
    scenario_id: str,
    created_at: dt.datetime,
    assertions: dict[str, ContractAssertion],
    review_cases_by_assertion_id: dict[str, ReviewCase],
) -> PolicyRelease:
    from clauseos.domain.values import term_value_to_canonical_dict

    terms: list[PublishedTerm] = []
    seen_concepts: set = set()
    for assertion in assertions.values():
        review_case = review_cases_by_assertion_id.get(assertion.assertion_id)
        if review_case is None:
            continue
        if review_case.status != ReviewCaseStatus.CHECKER_APPROVED:
            continue
        if assertion.status != AssertionStatus.APPROVED:
            continue
        if review_case.bound_evidence_hash != assertion.evidence_content_hash:
            # Stale relative to current assertion state; never publish it.
            continue
        assert review_case.checker_approval is not None
        terms.append(
            PublishedTerm(
                concept=assertion.concept.value,
                assertion_id=assertion.assertion_id,
                evidence_content_hash=assertion.evidence_content_hash,
                checker_actor_id=review_case.checker_approval.actor_id,
                value_dict=term_value_to_canonical_dict(assertion.candidate.proposed_value),
            )
        )
        seen_concepts.add(assertion.concept)

    missing = REQUIRED_MATERIAL_TERM_CONCEPTS - seen_concepts
    if missing:
        raise IncompletePolicyError(
            "cannot publish policy release: missing checker-approved material terms for "
            f"{sorted(m.value for m in missing)}"
        )

    return PolicyRelease(
        release_id=release_id,
        bundle_id=bundle_id,
        scenario_id=scenario_id,
        created_at=created_at,
        terms=tuple(terms),
    )

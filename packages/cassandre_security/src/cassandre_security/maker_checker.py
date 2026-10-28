"""Maker-checker and independent dual-approval enforcement (SEC-05).

Spec section 7: "Two accounts owned by one person do not satisfy
maker-checker... Critical identity enrollment/recovery, privileged-role
grants, trust-policy changes, egress-policy changes, and release admission
require approval by two independently entitled customer natural persons.
The requester, build/release signer, infrastructure administrator executing
the change, and affected business approver cannot satisfy the independent
approval unless an explicitly documented emergency procedure says
otherwise."

This module is a reusable helper each product's own maker/checker workflow
calls; it does not replace product-specific approval domain logic (ClauseOS
and CollateralOS already have their own `ReviewCase`/`PlanApproval` --
this enforces the identity-separation invariant those flows depend on).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class DistinctActorViolation(ValueError):
    pass


class NaturalPersonDirectory(Protocol):
    """Resolves an authenticated account/subject to the natural person
    behind it. S1 default: identity function (one account = one person).
    A real deployment plugs in its identity provider's "same human, several
    aliases" resolution here -- this package does not implement that
    resolution itself (spec section 7's "including aliases and multiple
    logins")."""

    def natural_person_for(self, actor_subject: str) -> str:
        ...


@dataclass(frozen=True)
class IdentityNaturalPersonDirectory:
    """Reference S1 implementation: account subject IS the natural-person
    identity. Explicitly NOT alias-aware -- see class docstring above."""

    def natural_person_for(self, actor_subject: str) -> str:
        return actor_subject


def enforce_distinct_natural_persons(
    maker_actor_subject: str,
    checker_actor_subject: str,
    *,
    directory: NaturalPersonDirectory | None = None,
) -> None:
    directory = directory or IdentityNaturalPersonDirectory()
    maker_person = directory.natural_person_for(maker_actor_subject)
    checker_person = directory.natural_person_for(checker_actor_subject)
    if maker_person == checker_person:
        raise DistinctActorViolation(
            f"maker and checker resolve to the same natural person ({maker_person!r}); "
            "self-approval is never permitted"
        )


class IndependentApprovalError(ValueError):
    pass


@dataclass(frozen=True)
class CriticalActionApproval:
    approver_actor_subject: str
    reason: str


def require_independent_dual_approval(
    *,
    requester_actor_subject: str,
    approvals: tuple[CriticalActionApproval, ...],
    directory: NaturalPersonDirectory | None = None,
    minimum_approvers: int = 2,
) -> None:
    """Spec section 7's critical-mutation dual-approval rule, generalized
    for role grants / trust-policy changes / egress-policy changes /
    release admission -- anything the spec calls "critical identity
    enrollment/recovery, privileged-role grants, trust-policy changes,
    egress-policy changes, and release admission". Raises unless at least
    `minimum_approvers` DISTINCT natural persons, none of whom is the
    requester, approved.
    """
    directory = directory or IdentityNaturalPersonDirectory()
    requester_person = directory.natural_person_for(requester_actor_subject)

    approver_persons: set[str] = set()
    for approval in approvals:
        person = directory.natural_person_for(approval.approver_actor_subject)
        if person == requester_person:
            raise IndependentApprovalError(
                "the requester cannot be counted as one of their own required independent approvers"
            )
        approver_persons.add(person)

    if len(approver_persons) < minimum_approvers:
        raise IndependentApprovalError(
            f"critical action requires {minimum_approvers} independently entitled natural-person approvers; "
            f"got {len(approver_persons)}"
        )

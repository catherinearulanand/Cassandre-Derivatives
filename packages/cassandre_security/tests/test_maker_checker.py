from __future__ import annotations

import pytest

from cassandre_security.maker_checker import (
    CriticalActionApproval,
    DistinctActorViolation,
    IndependentApprovalError,
    enforce_distinct_natural_persons,
    require_independent_dual_approval,
)


def test_distinct_actors_pass():
    enforce_distinct_natural_persons("SIM-USER-MAKER", "SIM-USER-CHECKER")


def test_same_actor_as_maker_and_checker_rejected():
    with pytest.raises(DistinctActorViolation):
        enforce_distinct_natural_persons("SIM-USER-1", "SIM-USER-1")


class _AliasDirectory:
    """Two account subjects resolving to the same real person -- proves
    the alias-aware directory hook actually gets consulted, not just
    string equality."""

    def natural_person_for(self, actor_subject: str) -> str:
        aliases = {"SIM-USER-ALIAS-A": "SIM-PERSON-X", "SIM-USER-ALIAS-B": "SIM-PERSON-X"}
        return aliases.get(actor_subject, actor_subject)


def test_distinct_account_ids_same_natural_person_rejected():
    with pytest.raises(DistinctActorViolation):
        enforce_distinct_natural_persons("SIM-USER-ALIAS-A", "SIM-USER-ALIAS-B", directory=_AliasDirectory())


def test_independent_dual_approval_succeeds_with_two_distinct_non_requester_approvers():
    require_independent_dual_approval(
        requester_actor_subject="SIM-USER-REQUESTER",
        approvals=(
            CriticalActionApproval(approver_actor_subject="SIM-USER-A", reason="ok"),
            CriticalActionApproval(approver_actor_subject="SIM-USER-B", reason="ok"),
        ),
    )


def test_independent_dual_approval_rejects_requester_as_approver():
    with pytest.raises(IndependentApprovalError):
        require_independent_dual_approval(
            requester_actor_subject="SIM-USER-REQUESTER",
            approvals=(
                CriticalActionApproval(approver_actor_subject="SIM-USER-REQUESTER", reason="self"),
                CriticalActionApproval(approver_actor_subject="SIM-USER-B", reason="ok"),
            ),
        )


def test_independent_dual_approval_rejects_same_approver_twice():
    with pytest.raises(IndependentApprovalError):
        require_independent_dual_approval(
            requester_actor_subject="SIM-USER-REQUESTER",
            approvals=(
                CriticalActionApproval(approver_actor_subject="SIM-USER-A", reason="ok"),
                CriticalActionApproval(approver_actor_subject="SIM-USER-A", reason="ok again"),
            ),
        )


def test_independent_dual_approval_rejects_two_aliases_of_the_same_person():
    with pytest.raises(IndependentApprovalError):
        require_independent_dual_approval(
            requester_actor_subject="SIM-USER-REQUESTER",
            approvals=(
                CriticalActionApproval(approver_actor_subject="SIM-USER-ALIAS-A", reason="ok"),
                CriticalActionApproval(approver_actor_subject="SIM-USER-ALIAS-B", reason="ok"),
            ),
            directory=_AliasDirectory(),
        )

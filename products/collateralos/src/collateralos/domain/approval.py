"""Maker/checker approval binding (plan Section 12.4 step 10, Section 13).

Approval is bound to the exact obligation/plan/reservation/policy/inventory/
instruction hash. If any of those change between maker approval and checker
approval, checker approval must fail closed with `APPROVAL_INVALIDATED`
rather than silently proceeding against a stale binding.
"""

from __future__ import annotations

from dataclasses import dataclass

from audit_protocol.canonical import content_hash

from collateralos.domain.identifiers import require_sim_identifier
from collateralos.domain.status import ApprovalStatus


@dataclass(frozen=True)
class ApprovalBindingInputs:
    obligation_id: str
    obligation_version: str
    plan_id: str
    plan_hash: str
    reservation_id: str
    reservation_version: int
    policy_id: str
    policy_version: str
    inventory_snapshot_id: str
    inventory_snapshot_version: str
    instruction_id: str
    instruction_hash: str

    def compute_binding_hash(self) -> str:
        return content_hash(
            {
                "obligation_id": self.obligation_id,
                "obligation_version": self.obligation_version,
                "plan_id": self.plan_id,
                "plan_hash": self.plan_hash,
                "reservation_id": self.reservation_id,
                "reservation_version": self.reservation_version,
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "inventory_snapshot_id": self.inventory_snapshot_id,
                "inventory_snapshot_version": self.inventory_snapshot_version,
                "instruction_id": self.instruction_id,
                "instruction_hash": self.instruction_hash,
            }
        )


class SelfApprovalError(Exception):
    """Plan Section 13: maker and checker must be distinct identities."""


@dataclass
class PlanApproval:
    approval_id: str
    plan_id: str
    reservation_id: str
    maker_id: str | None = None
    checker_id: str | None = None
    binding_hash: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING_MAKER

    def __post_init__(self) -> None:
        require_sim_identifier(self.approval_id, field_name="approval_id")

    def maker_approve(self, maker_id: str, inputs: ApprovalBindingInputs) -> None:
        if self.status is not ApprovalStatus.PENDING_MAKER:
            raise ValueError(f"cannot maker-approve from status {self.status}")
        self.maker_id = maker_id
        self.binding_hash = inputs.compute_binding_hash()
        self.status = ApprovalStatus.PENDING_CHECKER

    def checker_approve(self, checker_id: str, inputs: ApprovalBindingInputs) -> None:
        """Recomputes the binding hash from the *current* state at
        checker-approval time. Any drift since maker approval -- a changed
        plan, a re-reserved inventory version, a replaced policy -- produces
        a hash mismatch and fails closed to `APPROVAL_INVALIDATED` rather
        than silently approving a different object than the maker saw."""
        if self.status is not ApprovalStatus.PENDING_CHECKER:
            raise ValueError(f"cannot checker-approve from status {self.status}")
        if checker_id == self.maker_id:
            raise SelfApprovalError("maker and checker must be distinct identities")

        current_hash = inputs.compute_binding_hash()
        if current_hash != self.binding_hash:
            self.status = ApprovalStatus.APPROVAL_INVALIDATED
            raise ValueError(
                "APPROVAL_INVALIDATED: bound object set changed since maker approval "
                f"(maker saw {self.binding_hash}, checker sees {current_hash})"
            )

        self.checker_id = checker_id
        self.status = ApprovalStatus.APPROVED

    def reject(self, actor_id: str) -> None:
        self.status = ApprovalStatus.REJECTED

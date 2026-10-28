"""`Plan` / `AllocationLine` / `SolverEvidence` (plan Section 12.1, 12.4, 12.6).

Pure data types. The solver itself (scipy-backed) lives in `collateralos.core`;
this module only defines what a plan *is* and how its content hash (used to
bind maker/checker approval, Section 12.4 step 10) is computed.
"""

from __future__ import annotations

from dataclasses import dataclass

from audit_protocol.canonical import content_hash
from money_types import Money, Quantity

from collateralos.domain.identifiers import require_sim_identifier
from collateralos.domain.status import SolverStatus


@dataclass(frozen=True)
class AllocationLine:
    position_id: str
    delivered_quantity: Quantity
    adjusted_value: Money

    def content_hash_fields(self) -> dict:
        return {
            "position_id": self.position_id,
            "delivered_quantity": {
                "amount": str(self.delivered_quantity.amount),
                "unit": self.delivered_quantity.unit,
            },
            "adjusted_value": {
                "amount": str(self.adjusted_value.amount),
                "currency": self.adjusted_value.currency,
            },
        }


@dataclass(frozen=True)
class SolverEvidence:
    method: str  # "LP_PREVIEW" | "MIP" | "GREEDY_HEURISTIC_VERIFIED"
    solver_build: str
    status: SolverStatus
    objective_value: str | None
    gap_pct: str | None
    runtime_seconds: str
    time_limit_seconds: str
    gap_tolerance_pct: str
    verified: bool
    verifier_notes: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    plan_id: str
    obligation_id: str
    obligation_version: str
    policy_id: str
    policy_version: str
    inventory_snapshot_id: str
    inventory_snapshot_version: str
    lines: tuple[AllocationLine, ...]
    status: SolverStatus
    evidence: SolverEvidence

    def __post_init__(self) -> None:
        require_sim_identifier(self.plan_id, field_name="plan_id")

    def plan_hash(self) -> str:
        """Content hash binding obligation/policy/inventory-version/allocation
        (plan Section 12.4 step 10). Used by maker/checker approval binding
        and by settlement-instruction derivation."""
        return content_hash(
            {
                "plan_id": self.plan_id,
                "obligation_id": self.obligation_id,
                "obligation_version": self.obligation_version,
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "inventory_snapshot_id": self.inventory_snapshot_id,
                "inventory_snapshot_version": self.inventory_snapshot_version,
                "lines": [line.content_hash_fields() for line in self.lines],
                "status": self.status.value,
            }
        )

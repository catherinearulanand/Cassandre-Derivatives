"""`SettlementInstruction` / `SettlementPreflight` (plan Section 12.1, 12.4
steps 11-12, 12.8, 12.9).

Nothing in this module (or anywhere in `products/collateralos`) transmits an
instruction anywhere -- there is no settlement transport component (ADR-012).
An instruction record is validated internal data only; Section 12.9's closing
paragraph is why every payload built from this module carries an explicit
`conformance_scope` field rather than letting a reader assume more.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from audit_protocol.canonical import content_hash

from collateralos.domain.identifiers import require_sim_identifier
from collateralos.domain.plan import AllocationLine

CONFORMANCE_SCOPE_NOTE = (
    "Proves consistency with the approved obligation, allocation, reservation, "
    "and internal schema ONLY. This is NOT a SWIFT, ISO 20022, MT527, "
    "custodian, or CSD conformance claim, and this record is never "
    "transmitted to any external system (ADR-012)."
)


@dataclass(frozen=True)
class SettlementInstruction:
    instruction_id: str
    plan_id: str
    plan_hash: str
    reservation_id: str
    obligation_id: str
    lines: tuple[AllocationLine, ...]

    def __post_init__(self) -> None:
        require_sim_identifier(self.instruction_id, field_name="instruction_id")

    def instruction_hash(self) -> str:
        return content_hash(
            {
                "instruction_id": self.instruction_id,
                "plan_id": self.plan_id,
                "plan_hash": self.plan_hash,
                "reservation_id": self.reservation_id,
                "obligation_id": self.obligation_id,
                "lines": [line.content_hash_fields() for line in self.lines],
            }
        )

    def to_record(self) -> dict:
        return {
            "instruction_id": self.instruction_id,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "reservation_id": self.reservation_id,
            "obligation_id": self.obligation_id,
            "instruction_hash": self.instruction_hash(),
            "lines": [line.content_hash_fields() for line in self.lines],
            "conformance_scope": CONFORMANCE_SCOPE_NOTE,
            "transmitted": False,
        }


@dataclass(frozen=True)
class SettlementPreflight:
    preflight_id: str
    instruction_id: str
    performed_at: dt.datetime
    passed: bool
    failure_reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        require_sim_identifier(self.preflight_id, field_name="preflight_id")
        if self.performed_at.tzinfo is None:
            raise ValueError("performed_at must be timezone-aware")

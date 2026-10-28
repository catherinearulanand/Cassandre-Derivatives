"""`Obligation` aggregate (plan Section 12.1, 12.2 truth gate).

Pure domain data. No I/O, no clock, no fastapi/sqlalchemy/scipy import.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from money_types import Money

from collateralos.domain.identifiers import require_sim_identifier
from collateralos.domain.status import ObligationDirection, ObligationStatus


@dataclass(frozen=True)
class Obligation:
    obligation_id: str
    counterparty_id: str
    entity_id: str
    agreement_id: str
    direction: ObligationDirection
    amount: Money
    due_at: dt.datetime
    source: str
    version: str
    status: ObligationStatus

    def __post_init__(self) -> None:
        require_sim_identifier(self.obligation_id, field_name="obligation_id")
        require_sim_identifier(self.counterparty_id, field_name="counterparty_id")
        require_sim_identifier(self.entity_id, field_name="entity_id")
        require_sim_identifier(self.agreement_id, field_name="agreement_id")
        if self.due_at.tzinfo is None:
            raise ValueError("due_at must be timezone-aware")
        if self.direction is not ObligationDirection.BANK_DELIVERS:
            # Plan Section 12.7: a posting party cannot optimize assets held
            # by the other party, and the bank must not reserve/optimize its
            # own outbound inventory for a receivable call. This slice only
            # implements the outbound (bank-delivers) planning/reservation
            # workflow named in Section 25.5; the receivable-call path is an
            # explicit deferred scope gap, not a silent mishandling.
            raise NotImplementedError(
                "CollateralOS S1 slice implements only the BANK_DELIVERS planning "
                "workflow (plan Section 25.5); COUNTERPARTY_DELIVERS handling "
                "(Section 12.7 receivable-call path) is out of scope this session"
            )

    def content_hash_fields(self) -> dict:
        return {
            "obligation_id": self.obligation_id,
            "counterparty_id": self.counterparty_id,
            "entity_id": self.entity_id,
            "agreement_id": self.agreement_id,
            "direction": self.direction.value,
            "amount": {"amount": str(self.amount.amount), "currency": self.amount.currency},
            "due_at": self.due_at.astimezone(dt.timezone.utc).isoformat(),
            "version": self.version,
        }

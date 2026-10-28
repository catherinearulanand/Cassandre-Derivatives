"""Closed status enums for CollateralOS (plan Section 12.6, ADR-014).

`SolverStatus` is used verbatim as listed in plan Section 12.6 -- nothing
added, nothing renamed. It doubles as the `Plan` lifecycle status field
because the plan text itself groups solver-outcome statuses together with
later-lifecycle failure statuses (`RESERVATION_CONFLICT`,
`APPROVAL_INVALIDATED`, `PREFLIGHT_FAILED`) in the same closed list, and a
plan's status legitimately moves through both kinds of value over its life.
`INFEASIBLE_PROVEN` is intentionally absent: plan Section 12.6 reserves it
for a checkable mathematical certificate this build does not produce.
"""

from __future__ import annotations

from enum import Enum


class SolverStatus(str, Enum):
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    POLICY_UNRESOLVED = "POLICY_UNRESOLVED"
    STALE_INPUT = "STALE_INPUT"
    FEASIBLE_OPTIMAL = "FEASIBLE_OPTIMAL"
    FEASIBLE_BOUNDED_GAP = "FEASIBLE_BOUNDED_GAP"
    FEASIBLE_HEURISTIC_VERIFIED = "FEASIBLE_HEURISTIC_VERIFIED"
    INFEASIBLE_SOLVER_CONFIRMED = "INFEASIBLE_SOLVER_CONFIRMED"
    TIMEOUT_NO_FEASIBLE = "TIMEOUT_NO_FEASIBLE"
    RESERVATION_CONFLICT = "RESERVATION_CONFLICT"
    APPROVAL_INVALIDATED = "APPROVAL_INVALIDATED"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"


ACTIONABLE_STATUSES = frozenset(
    {
        SolverStatus.FEASIBLE_OPTIMAL,
        SolverStatus.FEASIBLE_BOUNDED_GAP,
        SolverStatus.FEASIBLE_HEURISTIC_VERIFIED,
    }
)


class ObligationDirection(str, Enum):
    BANK_DELIVERS = "BANK_DELIVERS"
    COUNTERPARTY_DELIVERS = "COUNTERPARTY_DELIVERS"


class ObligationStatus(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"


class PolicyBundleStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"


class ReservationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class ApprovalStatus(str, Enum):
    PENDING_MAKER = "PENDING_MAKER"
    PENDING_CHECKER = "PENDING_CHECKER"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPROVAL_INVALIDATED = "APPROVAL_INVALIDATED"


class RejectionReason(str, Enum):
    ASSET_CLASS_NOT_ELIGIBLE = "ASSET_CLASS_NOT_ELIGIBLE"
    WRONG_WAY_EXCLUDED = "WRONG_WAY_EXCLUDED"
    SELF_ISSUED = "SELF_ISSUED"
    AFFILIATE_ISSUED = "AFFILIATE_ISSUED"
    ENCUMBERED = "ENCUMBERED"
    RECALLED = "RECALLED"
    STALE_PRICE = "STALE_PRICE"
    POLICY_NOT_ACTIVE = "POLICY_NOT_ACTIVE"

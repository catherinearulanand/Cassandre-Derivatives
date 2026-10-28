"""Reservation ledger protocol -- pure arithmetic (plan Section 12.4's
numbered reservation-ledger list).

No I/O, no lock, no database. `reservable_now` is the single derived
availability figure (plan Section 12.4 item 1: "Do not store a second
ambiguous `available` balance."). Concurrency safety (item 3: lock rows,
item 6: atomic all-or-nothing insert) is the caller's job -- see
`collateralos.application.inventory_repository.InMemoryInventoryRepository`
for the lock-protected stub used by the concurrency test, since Docker/
Postgres `SELECT ... FOR UPDATE` is unavailable on this machine.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from collateralos.domain.identifiers import require_sim_identifier
from collateralos.domain.status import ReservationStatus


@dataclass(frozen=True)
class InventoryBalanceState:
    position_id: str
    gross_settled: Decimal
    encumbered: Decimal
    pending_outbound: Decimal
    blocked_or_recalled: Decimal
    reserved: Decimal
    version: int

    def __post_init__(self) -> None:
        require_sim_identifier(self.position_id, field_name="position_id")

    @property
    def reservable_now(self) -> Decimal:
        """Plan Section 12.4 item 5, computed exactly once here:
        `gross_settled - encumbered - pending_outbound - blocked_or_recalled - reserved`.
        """
        return (
            self.gross_settled
            - self.encumbered
            - self.pending_outbound
            - self.blocked_or_recalled
            - self.reserved
        )

    def with_reserved_delta(self, delta: Decimal) -> "InventoryBalanceState":
        return replace(self, reserved=self.reserved + delta, version=self.version + 1)


class ReservationConflictError(Exception):
    """At least one requested line's `reservable_now < requested`. Plan
    Section 12.4 item 7: roll back every line if any line fails -- partial
    reservation is forbidden."""

    def __init__(self, shortfalls: dict[str, Decimal]):
        self.shortfalls = shortfalls
        super().__init__(f"reservation conflict on positions: {sorted(shortfalls)}")


@dataclass(frozen=True)
class ReservationPlanResult:
    """Pure, side-effect-free result of attempting to reserve `requests`
    against `balances`: the new balance states to persist, iff every line
    had sufficient `reservable_now`."""

    new_balances: dict[str, InventoryBalanceState]


def balances_from_positions(positions) -> dict[str, "InventoryBalanceState"]:
    """Derive each position's initial `InventoryBalanceState` from its own
    custody quantity and encumbrance/recall flags. Shared by the in-memory
    application store and the 20-position test fixture so both compute
    availability the same way (plan Section 9.5's mutually-exclusive
    quantity-category rule)."""
    balances: dict[str, InventoryBalanceState] = {}
    for position in positions:
        qty = position.quantity.amount
        balances[position.position_id] = InventoryBalanceState(
            position_id=position.position_id,
            gross_settled=qty,
            encumbered=qty if position.is_encumbered else Decimal("0"),
            pending_outbound=Decimal("0"),
            blocked_or_recalled=qty if position.is_recalled else Decimal("0"),
            reserved=Decimal("0"),
            version=1,
        )
    return balances


def sorted_position_keys(position_ids: list[str]) -> list[str]:
    """Plan Section 12.4 item 2: sort requested position keys deterministically
    to prevent lock-order deadlocks."""
    return sorted(set(position_ids))


def attempt_reserve(
    balances: dict[str, InventoryBalanceState],
    requests: dict[str, Decimal],
) -> ReservationPlanResult:
    """All-or-nothing: raises `ReservationConflictError` (no mutation
    performed, since inputs/outputs are immutable dataclasses) if any
    requested line exceeds `reservable_now`."""
    shortfalls: dict[str, Decimal] = {}
    for position_id in sorted_position_keys(list(requests.keys())):
        requested = requests[position_id]
        if requested <= 0:
            raise ValueError(f"requested quantity for {position_id} must be positive")
        balance = balances.get(position_id)
        if balance is None:
            raise KeyError(f"no balance row for position {position_id}")
        if balance.reservable_now < requested:
            shortfalls[position_id] = balance.reservable_now

    if shortfalls:
        raise ReservationConflictError(shortfalls)

    new_balances = dict(balances)
    for position_id, requested in requests.items():
        new_balances[position_id] = balances[position_id].with_reserved_delta(requested)
    return ReservationPlanResult(new_balances=new_balances)


@dataclass
class Reservation:
    reservation_id: str
    plan_id: str
    lines: dict[str, Decimal]  # position_id -> reserved quantity
    status: ReservationStatus
    expires_at: object  # dt.datetime, kept loosely typed to avoid a clock import here

    def __post_init__(self) -> None:
        require_sim_identifier(self.reservation_id, field_name="reservation_id")

    def release(self, balances: dict[str, InventoryBalanceState]) -> dict[str, InventoryBalanceState]:
        """Idempotent: releasing an already-released/expired reservation is a
        no-op (plan Section 12.4 item 8), never a second decrement."""
        if self.status is not ReservationStatus.ACTIVE:
            return balances
        new_balances = dict(balances)
        for position_id, quantity in self.lines.items():
            balance = new_balances.get(position_id)
            if balance is not None:
                new_balances[position_id] = balance.with_reserved_delta(-quantity)
        self.status = ReservationStatus.RELEASED
        return new_balances

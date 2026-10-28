"""Lock-protected in-memory inventory repository (plan Section 12.4's
reservation-ledger list, item 3: "lock all balance rows FOR UPDATE").

Docker Desktop is not runnable on this machine (see `docs/adr/ADR-002...md`),
so this is the closest feasible proxy for Postgres `SELECT ... FOR UPDATE`
row-locking: one process-wide `threading.Lock` guards the entire
attempt-reserve/release critical section, giving the same all-or-nothing,
serialized semantics the ledger protocol requires, in-process. This is
explicitly NOT a substitute for a real multi-connection Postgres concurrency
test (see README "deferred scope").
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from decimal import Decimal

from collateralos.domain.reservation import (
    InventoryBalanceState,
    ReservationConflictError,
    attempt_reserve,
)
from collateralos.domain.status import ReservationStatus


@dataclass
class ReservationRecord:
    reservation_id: str
    plan_id: str
    lines: dict[str, Decimal]
    status: ReservationStatus


class InMemoryInventoryRepository:
    """Not thread-per-request-safe by accident -- correctness depends on
    every mutating method acquiring `self._lock` for its entire
    check-then-act critical section, mirroring the Postgres transaction
    boundary the real adapter will use."""

    def __init__(self, balances: dict[str, InventoryBalanceState]):
        self._lock = threading.Lock()
        self._balances: dict[str, InventoryBalanceState] = dict(balances)
        self._reservations: dict[str, ReservationRecord] = {}

    def snapshot_balances(self) -> dict[str, InventoryBalanceState]:
        with self._lock:
            return dict(self._balances)

    def reserve(self, reservation_id: str, plan_id: str, requests: dict[str, Decimal]) -> ReservationRecord:
        with self._lock:
            if reservation_id in self._reservations:
                raise ValueError(f"reservation_id {reservation_id!r} already used (idempotency violation)")
            try:
                result = attempt_reserve(self._balances, requests)
            except ReservationConflictError:
                raise
            self._balances = result.new_balances
            record = ReservationRecord(
                reservation_id=reservation_id,
                plan_id=plan_id,
                lines=dict(requests),
                status=ReservationStatus.ACTIVE,
            )
            self._reservations[reservation_id] = record
            return record

    def release(self, reservation_id: str) -> None:
        with self._lock:
            record = self._reservations.get(reservation_id)
            if record is None:
                raise KeyError(f"no reservation {reservation_id!r}")
            if record.status is not ReservationStatus.ACTIVE:
                return  # idempotent: already released/expired, no second decrement
            for position_id, quantity in record.lines.items():
                balance = self._balances.get(position_id)
                if balance is not None:
                    self._balances[position_id] = balance.with_reserved_delta(-quantity)
            record.status = ReservationStatus.RELEASED

    def get_reservation(self, reservation_id: str) -> ReservationRecord:
        with self._lock:
            record = self._reservations.get(reservation_id)
            if record is None:
                raise KeyError(f"no reservation {reservation_id!r}")
            return record

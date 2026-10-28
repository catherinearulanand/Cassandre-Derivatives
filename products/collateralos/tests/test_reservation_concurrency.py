"""Reservation-ledger concurrency test (plan Section 12.4 items 3, 6, 7;
work item COL-010).

`InMemoryInventoryRepository` is the lock-protected stand-in for Postgres
`SELECT ... FOR UPDATE` row locking (Docker Desktop is unavailable this
session -- see the repository module's own docstring and
`docs/adr/ADR-002-database-per-product.md`). This test fires many concurrent
`reserve()` calls at one repository instance from real OS threads and
asserts two invariants that only hold if the lock actually serializes the
check-then-act critical section:

1. No over-reservation: the sum of every successful reservation never
   exceeds the position's original available quantity, and the repository's
   own post-hoc balance agrees with that sum exactly.
2. Every attempt that could not be satisfied fails closed with
   `ReservationConflictError` (the in-process equivalent of the
   `RESERVATION_CONFLICT` status the store/API layer surfaces -- see
   `collateralos.application.store.CollateralOSStore.reserve_plan`, which
   catches exactly this exception and sets `SolverStatus.RESERVATION_CONFLICT`)
   -- never a silent partial fill and never a double-decrement.
"""

from __future__ import annotations

import threading
from decimal import Decimal

from collateralos.application.inventory_repository import InMemoryInventoryRepository
from collateralos.domain.reservation import InventoryBalanceState, ReservationConflictError

POSITION_ID = "SIM-POS-CONCURRENCY-0001"
AVAILABLE = Decimal("500000")
REQUEST_PER_THREAD = Decimal("20000")
THREAD_COUNT = 35  # plan brief: 20-50 concurrent attempts
EXPECTED_SUCCESSES = int(AVAILABLE // REQUEST_PER_THREAD)  # 25, deterministic regardless of interleaving


def _initial_balances() -> dict[str, InventoryBalanceState]:
    return {
        POSITION_ID: InventoryBalanceState(
            position_id=POSITION_ID,
            gross_settled=AVAILABLE,
            encumbered=Decimal("0"),
            pending_outbound=Decimal("0"),
            blocked_or_recalled=Decimal("0"),
            reserved=Decimal("0"),
            version=1,
        )
    }


def test_concurrent_reservations_never_over_reserve_and_conflicts_fail_closed():
    repo = InMemoryInventoryRepository(_initial_balances())

    successes: list[str] = []
    conflicts: list[ReservationConflictError] = []
    unexpected: list[BaseException] = []
    results_lock = threading.Lock()

    def attempt(index: int) -> None:
        reservation_id = f"SIM-RES-CONC-{index:04d}"
        try:
            repo.reserve(reservation_id, "SIM-PLAN-CONCURRENCY-0001", {POSITION_ID: REQUEST_PER_THREAD})
        except ReservationConflictError as exc:
            with results_lock:
                conflicts.append(exc)
        except BaseException as exc:  # pragma: no cover - would indicate a real bug
            with results_lock:
                unexpected.append(exc)
        else:
            with results_lock:
                successes.append(reservation_id)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(THREAD_COUNT)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "a reservation thread hung -- the lock is not releasing correctly"

    assert not unexpected, f"unexpected exceptions during concurrent reservation: {unexpected}"

    # Invariant 1: no over-reservation, and the count of successes is exactly
    # what the arithmetic predicts no matter which threads won the race.
    assert len(successes) == EXPECTED_SUCCESSES
    assert len(conflicts) == THREAD_COUNT - EXPECTED_SUCCESSES

    final_balance = repo.snapshot_balances()[POSITION_ID]
    assert final_balance.reserved == EXPECTED_SUCCESSES * REQUEST_PER_THREAD
    assert final_balance.reserved <= AVAILABLE
    assert final_balance.reservable_now == AVAILABLE - EXPECTED_SUCCESSES * REQUEST_PER_THREAD

    # Invariant 2: every rejected attempt failed closed with the conflict
    # error (the RESERVATION_CONFLICT semantics), reporting the exact
    # position and the shortfall the ledger actually had left.
    for exc in conflicts:
        assert POSITION_ID in exc.shortfalls
        assert exc.shortfalls[POSITION_ID] < REQUEST_PER_THREAD

    # No reservation_id was double-recorded and no reservation is silently
    # partial: every successful reservation_id has an ACTIVE record for the
    # full requested quantity.
    for reservation_id in successes:
        record = repo.get_reservation(reservation_id)
        assert record.lines[POSITION_ID] == REQUEST_PER_THREAD

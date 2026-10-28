"""Real Postgres row-locking replacement for
`collateralos.application.inventory_repository.InMemoryInventoryRepository`
(work item COL-010; see that module's own docstring: its single process-wide
`threading.Lock` is explicitly "NOT a substitute for a real multi-connection
Postgres concurrency test").

This module is the substitute: `reserve_positions_for_update` locks every
requested position's `inventory_balance` row with a real
`SELECT ... FOR UPDATE`, in the same deterministic sorted order
`collateralos.domain.reservation.sorted_position_keys` already documents
(plan Section 12.4 item 2: "sort requested position keys deterministically
to prevent lock-order deadlocks"), and then delegates the actual
reserve-or-conflict arithmetic to the pure, already-tested
`collateralos.domain.reservation.attempt_reserve` -- this module never
re-implements that logic, it only replaces the in-memory dict-with-lock
storage underneath it with real locked rows.

`products/collateralos/tests/test_sql_store.py::test_two_connections_race_to_
reserve_the_same_position_under_real_postgres_locking` is what actually
retires the deferred-scope callout: it drives this function from two
genuinely separate SQLAlchemy `Session`/connections racing for the same
`position_id`, and only one can win because Postgres itself serializes the
row lock, not any in-process coordination.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from collateralos.adapters.models import InventoryBalanceRow
from collateralos.domain.reservation import (
    InventoryBalanceState,
    ReservationPlanResult,
    attempt_reserve,
    sorted_position_keys,
)


def _row_to_state(row: InventoryBalanceRow) -> InventoryBalanceState:
    return InventoryBalanceState(
        position_id=row.position_id,
        gross_settled=row.gross_settled,
        encumbered=row.encumbered,
        pending_outbound=row.pending_outbound,
        blocked_or_recalled=row.blocked_or_recalled,
        reserved=row.reserved,
        version=row.version,
    )


def _lock_balance_rows(session: Session, position_ids: list[str]) -> dict[str, InventoryBalanceRow]:
    stmt = (
        select(InventoryBalanceRow)
        .where(InventoryBalanceRow.position_id.in_(position_ids))
        .order_by(InventoryBalanceRow.position_id)
        .with_for_update()
    )
    rows = session.execute(stmt).scalars().all()
    return {row.position_id: row for row in rows}


def reserve_positions_for_update(session: Session, requests: dict[str, Decimal]) -> ReservationPlanResult:
    """All-or-nothing reservation against real, row-locked Postgres state.

    Blocks (on the DB, not in this process) until every requested
    `position_id`'s row is locked, then evaluates `attempt_reserve` against
    the balances as they exist *right now* -- which, for a transaction that
    was blocked behind another writer's lock, means the balances *after*
    that writer committed. Raises `ReservationConflictError` with zero rows
    mutated if any line is short; the caller is responsible for committing
    (or rolling back) the transaction.
    """
    position_ids = sorted_position_keys(list(requests.keys()))
    rows = _lock_balance_rows(session, position_ids)
    balances = {pid: _row_to_state(rows[pid]) for pid in position_ids if pid in rows}

    result = attempt_reserve(balances, requests)  # raises ReservationConflictError, no mutation, if short

    for position_id, new_state in result.new_balances.items():
        row = rows[position_id]
        row.reserved = new_state.reserved
        row.version = new_state.version
    return result


def release_positions_for_update(session: Session, lines: dict[str, Decimal]) -> None:
    """Idempotent release counterpart to `reserve_positions_for_update`,
    also under real row locks -- mirrors
    `InMemoryInventoryRepository.release`'s decrement, but as locked
    UPDATEs instead of in-process dict mutation."""
    position_ids = sorted_position_keys(list(lines.keys()))
    rows = _lock_balance_rows(session, position_ids)
    for position_id, quantity in lines.items():
        row = rows.get(position_id)
        if row is not None:
            row.reserved = row.reserved - quantity
            row.version = row.version + 1

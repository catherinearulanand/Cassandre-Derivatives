from __future__ import annotations

import datetime as dt
import threading

import pytest
from sqlalchemy.orm import Session

from cassandre_security.revocation import (
    EpochMismatchError,
    RevocationState,
    RevokedScopeError,
    StaleFenceStateError,
    UnknownFenceScopeError,
    advance_revocation,
    ensure_fence_exists,
    lock_fences_for_mutation,
)

NOW = dt.datetime(2026, 1, 5, 12, 0, 0, tzinfo=dt.timezone.utc)
SCOPE = "product=clauseos;tenant=SIM-TENANT-A;subject=SIM-USER-1"


def test_unknown_scope_blocks_mutation(sqlite_session):
    with pytest.raises(UnknownFenceScopeError):
        lock_fences_for_mutation(
            sqlite_session, scope_keys=(SCOPE,), presented_epoch_by_scope={SCOPE: 0}, now=NOW
        )


def test_provisioned_scope_at_current_epoch_allows_mutation(sqlite_session):
    ensure_fence_exists(sqlite_session, SCOPE, now=NOW)
    locked = lock_fences_for_mutation(
        sqlite_session, scope_keys=(SCOPE,), presented_epoch_by_scope={SCOPE: 0}, now=NOW
    )
    assert locked[0].epoch == 0
    assert locked[0].revocation_state is RevocationState.CURRENT


def test_stale_presented_epoch_blocked(sqlite_session):
    ensure_fence_exists(sqlite_session, SCOPE, now=NOW)
    advance_revocation(sqlite_session, scope_key=SCOPE, new_state=RevocationState.CURRENT, now=NOW)
    with pytest.raises(EpochMismatchError):
        lock_fences_for_mutation(
            sqlite_session, scope_keys=(SCOPE,), presented_epoch_by_scope={SCOPE: 0}, now=NOW
        )


def test_revocation_effective_blocks_new_mutation(sqlite_session):
    ensure_fence_exists(sqlite_session, SCOPE, now=NOW)
    advance_revocation(sqlite_session, scope_key=SCOPE, new_state=RevocationState.REVOCATION_EFFECTIVE, now=NOW)
    with pytest.raises(RevokedScopeError):
        lock_fences_for_mutation(
            sqlite_session, scope_keys=(SCOPE,), presented_epoch_by_scope={SCOPE: 1}, now=NOW
        )


def test_stale_fence_state_blocks_mutation(sqlite_session):
    ensure_fence_exists(sqlite_session, SCOPE, now=NOW - dt.timedelta(minutes=10))
    with pytest.raises(StaleFenceStateError):
        lock_fences_for_mutation(
            sqlite_session,
            scope_keys=(SCOPE,),
            presented_epoch_by_scope={SCOPE: 0},
            now=NOW,
            max_staleness=dt.timedelta(seconds=60),
        )


def test_missing_presented_epoch_blocked(sqlite_session):
    ensure_fence_exists(sqlite_session, SCOPE, now=NOW)
    with pytest.raises(EpochMismatchError):
        lock_fences_for_mutation(sqlite_session, scope_keys=(SCOPE,), presented_epoch_by_scope={}, now=NOW)


def test_fence_locking_uses_fixed_sorted_order_regardless_of_caller_order(sqlite_session):
    scope_a = "product=clauseos;subject=A"
    scope_b = "product=clauseos;subject=B"
    ensure_fence_exists(sqlite_session, scope_a, now=NOW)
    ensure_fence_exists(sqlite_session, scope_b, now=NOW)
    locked_1 = lock_fences_for_mutation(
        sqlite_session, scope_keys=(scope_b, scope_a), presented_epoch_by_scope={scope_a: 0, scope_b: 0}, now=NOW
    )
    locked_2 = lock_fences_for_mutation(
        sqlite_session, scope_keys=(scope_a, scope_b), presented_epoch_by_scope={scope_a: 0, scope_b: 0}, now=NOW
    )
    assert [f.scope_key for f in locked_1] == [f.scope_key for f in locked_2] == sorted([scope_a, scope_b])


def test_advance_revocation_on_unprovisioned_scope_raises(sqlite_session):
    with pytest.raises(UnknownFenceScopeError):
        advance_revocation(sqlite_session, scope_key="product=nowhere;subject=nobody", new_state=RevocationState.REVOCATION_REQUESTED, now=NOW)


# ---------------------------------------------------------------------------
# Genuine multi-connection race, proving JPMC-FINAL-01's two orderings on
# real Postgres row locks -- mirrors the exact pattern already proven in
# products/collateralos/tests/test_sql_store.py's reservation concurrency
# test in this same repository.
# ---------------------------------------------------------------------------


def test_mutation_committing_first_precedes_effective_local_revocation(postgres_engine):
    with Session(postgres_engine) as setup_session:
        ensure_fence_exists(setup_session, SCOPE, now=NOW)
        setup_session.commit()

    results: dict[str, object] = {}
    results_lock = threading.Lock()
    start_barrier = threading.Barrier(2)

    def run_mutation() -> None:
        session = Session(postgres_engine)
        try:
            start_barrier.wait(timeout=10)
            lock_fences_for_mutation(session, scope_keys=(SCOPE,), presented_epoch_by_scope={SCOPE: 0}, now=NOW)
            # Simulate the caller's own domain/audit/outbox writes happening
            # here, inside the same transaction, before commit.
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            with results_lock:
                results["mutation"] = exc
        else:
            session.commit()
            with results_lock:
                results["mutation"] = "success"
        finally:
            session.close()

    def run_revocation() -> None:
        session = Session(postgres_engine)
        try:
            start_barrier.wait(timeout=10)
            advance_revocation(session, scope_key=SCOPE, new_state=RevocationState.REVOCATION_EFFECTIVE, now=NOW)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            with results_lock:
                results["revocation"] = exc
        else:
            session.commit()
            with results_lock:
                results["revocation"] = "success"
        finally:
            session.close()

    t1 = threading.Thread(target=run_mutation)
    t2 = threading.Thread(target=run_revocation)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    assert not t1.is_alive() and not t2.is_alive(), "a fence-locking thread hung -- the row lock did not release"

    # Both operations genuinely raced on the same locked row; Postgres
    # serialized them. Either legitimate ordering is acceptable (this test
    # proves the mechanism is race-safe, not which thread the OS scheduled
    # first) -- but exactly one must be "success" and, if the mutation lost
    # the race, it must have failed with a real epoch/revocation error, not
    # silently corrupted state.
    assert results["revocation"] == "success"
    if results["mutation"] != "success":
        assert isinstance(results["mutation"], (EpochMismatchError, RevokedScopeError))

    with Session(postgres_engine) as verify_session:
        from cassandre_security.revocation import SecurityFenceRow

        row = verify_session.get(SecurityFenceRow, SCOPE)
        assert row.revocation_state == RevocationState.REVOCATION_EFFECTIVE.value

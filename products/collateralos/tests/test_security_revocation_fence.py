"""SEC-14 revocation-fence wiring (`cassandre_security.revocation`) into
`SqlAlchemyCollateralOSStore.reserve_plan`/`export_internal`
(`adapters/sql_store.py`; see `application/security.py::fence_scope_key`/
`peek_fence_epoch`).

Everything here runs only against a real Postgres, via the
`collateralos_sql_engine` fixture (skips cleanly without
`CASSANDRE_TEST_DATABASE_URL`, per `tests/conftest.py` / ADR-002) --
revocation fencing is fundamentally a real-row-locking guarantee, so this is
deliberately not faked against SQLite or the in-memory store (see the
product README's "Security integration" section for why the in-memory store
does not get this wiring).

Covers:

* a deterministic proof that a scope marked `REVOCATION_EFFECTIVE` blocks a
  subsequent `reserve_plan` call outright (`RevokedScopeError`), and that no
  reservation/inventory-balance mutation occurred;
* the genuine two-connection race this repository's established pattern
  calls for (mirrors `test_sql_store.py`'s own two-connection reservation
  race and `packages/cassandre_security/tests/test_revocation.py`'s own
  fence-vs-revocation race): a real `reserve_plan` call and a real
  `advance_revocation` call, each its own `Session`/connection, synchronized
  with a `threading.Barrier` so whichever commits first genuinely decides
  the DB-visible outcome -- not simulated, not mocked.
"""

from __future__ import annotations

import datetime as dt
import threading

import pytest
from sqlalchemy.orm import Session, sessionmaker

from audit_protocol.keys import generate_keypair
from money_types import Money

from cassandre_security.revocation import RevocationState, RevokedScopeError, advance_revocation
from collateralos.adapters.models import InventoryBalanceRow, ReservationRow
from collateralos.adapters.sql_store import SqlAlchemyCollateralOSStore
from collateralos.application.security import fence_scope_key
from collateralos.domain.inventory import InventorySnapshot
from collateralos.domain.obligation import Obligation
from collateralos.domain.status import ObligationDirection, ObligationStatus

from fixtures.inventory_corpus import AS_OF, COUNTERPARTY_ID, build_twenty_position_corpus

OPS_ID = "SIM-USER-OPS-0001"


def _obligation(obligation_id: str) -> Obligation:
    return Obligation(
        obligation_id=obligation_id,
        counterparty_id=COUNTERPARTY_ID,
        entity_id="SIM-ENTITY-CASSANDRE-BANK-NA",
        agreement_id=f"SIM-AGREEMENT-{obligation_id}",
        direction=ObligationDirection.BANK_DELIVERS,
        amount=Money(amount="100000.00", currency="USD", rounding_context="obligation"),
        due_at=AS_OF + dt.timedelta(days=1),
        source="test",
        version="v1",
        status=ObligationStatus.VALIDATED,
    )


def _build_planned_scenario(session_factory: sessionmaker[Session], *, seq: str):
    """Like `test_sql_store.py`'s own `_build_reserved_plan_scenario`, but
    stops at `create_plan` (does NOT reserve) -- these tests call
    `reserve_plan` themselves, sometimes from a background thread, so it
    must not have been called already."""
    store = SqlAlchemyCollateralOSStore(session_factory, keypair=generate_keypair(f"collateralos-fence-test-{seq}"))

    obligation_id = f"SIM-OBL-FENCE-{seq}"
    snapshot_id = f"SIM-SNAP-FENCE-{seq}"
    plan_id = f"SIM-PLAN-FENCE-{seq}"

    store.add_obligation(_obligation(obligation_id), actor_id=OPS_ID, now=AS_OF)
    draft_policy = store.add_draft_policy()
    store.validate_policy(draft_policy.policy_id, actor_id=OPS_ID, now=AS_OF)
    store.activate_policy(draft_policy.policy_id, actor_id=OPS_ID, now=AS_OF)

    positions = build_twenty_position_corpus()
    store.add_snapshot(
        InventorySnapshot(snapshot_id=snapshot_id, version="v1", as_of=AS_OF, positions=positions),
        actor_id=OPS_ID, now=AS_OF,
    )
    plan = store.create_plan(
        plan_id=plan_id, obligation_id=obligation_id, policy_id=draft_policy.policy_id, snapshot_id=snapshot_id,
        as_of=AS_OF, actor_id=OPS_ID, now=AS_OF,
    )
    assert plan.evidence.verified and plan.lines, (plan.status, plan.evidence.verifier_notes)

    return store, obligation_id, plan_id


def _session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


# --- deterministic block ----------------------------------------------------


def test_reserve_plan_is_blocked_once_its_obligation_scope_is_revoked(collateralos_sql_engine):
    store, obligation_id, plan_id = _build_planned_scenario(_session_factory(collateralos_sql_engine), seq="0001")

    scope_key = fence_scope_key(obligation_id=obligation_id)
    # `add_obligation` already provisioned the fence at epoch 0 (SEC-14); an
    # operator/incident-response action revokes it directly, exactly like
    # `advance_revocation`'s own docstring describes -- effective local
    # revocation racing against (here, strictly preceding) a mutation.
    with Session(collateralos_sql_engine) as session:
        advance_revocation(session, scope_key=scope_key, new_state=RevocationState.REVOCATION_EFFECTIVE, now=AS_OF)
        session.commit()

    with pytest.raises(RevokedScopeError):
        store.reserve_plan(
            plan_id, reservation_id="SIM-RES-FENCE-0001", instruction_id="SIM-INSTR-FENCE-0001",
            actor_id=OPS_ID, now=AS_OF,
        )

    # The blocked mutation left no trace: no reservation row, no
    # inventory_balance row was ever decremented.
    with Session(collateralos_sql_engine) as session:
        assert session.get(ReservationRow, "SIM-RES-FENCE-0001") is None
        plan = store.get_plan(plan_id)
        for line in plan.lines:
            balance_row = session.get(InventoryBalanceRow, line.position_id)
            assert balance_row.reserved == 0


def test_reserve_plan_presenting_a_stale_epoch_is_blocked_after_revocation_advances_it(collateralos_sql_engine):
    """Exercises the epoch-mismatch path specifically (not the
    REVOCATION_EFFECTIVE-state path above): revoke with
    `REVOCATION_REQUESTED` (which does not itself set
    REVOCATION_EFFECTIVE) while still bumping the epoch, and confirm
    `reserve_plan`'s presented (now stale) epoch is rejected."""
    store, obligation_id, plan_id = _build_planned_scenario(_session_factory(collateralos_sql_engine), seq="0002")

    scope_key = fence_scope_key(obligation_id=obligation_id)
    with Session(collateralos_sql_engine) as session:
        locked = advance_revocation(session, scope_key=scope_key, new_state=RevocationState.REVOCATION_REQUESTED, now=AS_OF)
        session.commit()
    assert locked.epoch == 1  # epoch advanced even though the state isn't (yet) REVOCATION_EFFECTIVE

    from cassandre_security.revocation import EpochMismatchError

    # `reserve_plan` peeks the epoch itself right before locking, so under
    # normal sequential execution it would see the new epoch and match --
    # to prove the specific epoch-mismatch code path (not just the
    # REVOCATION_EFFECTIVE-state path), call the lower-level helpers
    # directly with a deliberately stale presented epoch, exactly as a
    # caller holding an authorization decision issued before the revocation
    # would.
    from cassandre_security.revocation import lock_fences_for_mutation

    with Session(collateralos_sql_engine) as session:
        with pytest.raises(EpochMismatchError):
            lock_fences_for_mutation(
                session, scope_keys=(scope_key,), presented_epoch_by_scope={scope_key: 0}, now=AS_OF,
            )


# --- genuine two-connection race ---------------------------------------------


def test_revocation_committing_first_blocks_a_concurrently_attempted_reservation_under_real_postgres_locking(
    collateralos_sql_engine,
):
    """Two genuinely separate connections race on the same fence row: one
    thread calls the real, fully-wired `SqlAlchemyCollateralOSStore.
    reserve_plan` (which internally opens its own `Session` and locks the
    fence row via `lock_fences_for_mutation` before the reservation's own
    `SELECT ... FOR UPDATE`); the other calls `advance_revocation` directly
    against a second, independent `Session`. `threading.Barrier`
    synchronizes their start exactly like `test_sql_store.py`'s own
    two-connection reservation race and `packages/cassandre_security/
    tests/test_revocation.py`'s fence-vs-revocation race.

    Both legitimate commit orderings satisfy JPMC-FINAL-01 (a mutation that
    commits first precedes revocation; a revocation that commits first
    rejects the mutation) -- this test asserts the invariants that must
    hold regardless of which one the OS scheduler let win, exactly like
    those two existing tests do, rather than asserting one specific
    ordering.
    """
    store, obligation_id, plan_id = _build_planned_scenario(_session_factory(collateralos_sql_engine), seq="0003")
    scope_key = fence_scope_key(obligation_id=obligation_id)

    results: dict[str, object] = {}
    results_lock = threading.Lock()
    start_barrier = threading.Barrier(2)

    def run_reservation() -> None:
        try:
            start_barrier.wait(timeout=10)
            store.reserve_plan(
                plan_id, reservation_id="SIM-RES-FENCE-0003", instruction_id="SIM-INSTR-FENCE-0003",
                actor_id=OPS_ID, now=AS_OF,
            )
        except Exception as exc:  # noqa: BLE001
            with results_lock:
                results["reservation"] = exc
        else:
            with results_lock:
                results["reservation"] = "success"

    def run_revocation() -> None:
        session = Session(collateralos_sql_engine)
        try:
            start_barrier.wait(timeout=10)
            advance_revocation(session, scope_key=scope_key, new_state=RevocationState.REVOCATION_EFFECTIVE, now=AS_OF)
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

    t1 = threading.Thread(target=run_reservation)
    t2 = threading.Thread(target=run_revocation)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    assert not t1.is_alive() and not t2.is_alive(), "a fence-locking thread hung -- the row lock did not release"

    # Invariant 1: revocation itself always succeeds (nothing about the
    # concurrent reservation attempt can block a legitimate revocation
    # forever -- it either goes first, or waits behind the reservation's
    # transaction and then proceeds).
    assert results["revocation"] == "success", results

    # Invariant 2: whichever ordering won, the DB-visible state is
    # consistent with it -- never a reservation that both "succeeded" and
    # is invisible, never a silently-corrupted fence.
    with Session(collateralos_sql_engine) as verify_session:
        reservation_row = verify_session.get(ReservationRow, "SIM-RES-FENCE-0003")
        from cassandre_security.revocation import SecurityFenceRow

        fence_row = verify_session.get(SecurityFenceRow, scope_key)
        assert fence_row.revocation_state == RevocationState.REVOCATION_EFFECTIVE.value

        if results["reservation"] == "success":
            # The reservation committed before revocation's lock landed --
            # legitimate ordering #1 (mutation precedes effective
            # revocation, per JPMC-FINAL-01 point 3).
            assert reservation_row is not None
        else:
            # The reservation lost the race -- it must have failed
            # *because of* the fence (closed, not some unrelated error),
            # and left no partial reservation behind.
            from cassandre_security.revocation import EpochMismatchError, RevokedScopeError

            assert isinstance(results["reservation"], (RevokedScopeError, EpochMismatchError)), results["reservation"]
            assert reservation_row is None

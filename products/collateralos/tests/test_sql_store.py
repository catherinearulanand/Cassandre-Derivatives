"""`SqlAlchemyCollateralOSStore` tests (work item COL-014's persistence
half) -- everything here runs only against a real Postgres, via the
`collateralos_sql_engine` fixture (`tests/conftest.py`), which skips cleanly
when `CASSANDRE_TEST_DATABASE_URL` is unset or unreachable (ADR-002).

Covers:

* a full obligation -> policy -> snapshot -> plan -> reserve -> maker/checker
  approve -> preflight -> export round trip through `SqlAlchemyCollateralOSStore`
  (mirrors `tests/fixtures/store_scenario.py`'s in-memory end-to-end
  scenario), asserting the exported envelope verifies with
  `cassandre_contracts.verify_envelope` and `verify_audit_chain()` reports
  OK, reading results back from Postgres directly (not the in-process
  objects the calls returned) to prove it actually round-tripped;
* the same negative/edge cases already proven for the in-memory store in
  `test_preflight_export.py` / `test_approval.py` (idempotent export
  replay, a same-key-different-body idempotency conflict, self-approval
  rejection, a stale approval binding invalidated by a plan change),
  re-run against the SQL store for parity;
* the genuine two-connection Postgres row-locking concurrency proof that
  retires the deferred-scope gap
  `collateralos.application.inventory_repository.InMemoryInventoryRepository`'s
  own docstring names explicitly ("NOT a substitute for a real
  multi-connection Postgres concurrency test").

Every test uses its own `SIM-*`-prefixed, per-test-unique identifiers and
the `collateralos_sql_engine` fixture drops every table after each test, so
this file is safe to run twice in a row against the same live Postgres
without resetting it.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from audit_protocol.keys import generate_keypair
from cassandre_contracts import ProducerProduct, TrustBundle, verify_envelope
from money_types import Money

from collateralos.adapters.models import (
    AuditEventRow,
    InventoryBalanceRow,
    InventorySnapshotRow,
    PlanRow,
    ReservationRow,
    SettlementInstructionRow,
)
from collateralos.adapters.sql_reservation import reserve_positions_for_update
from collateralos.adapters.sql_store import SqlAlchemyCollateralOSStore
from collateralos.application.idempotency import IdempotencyConflictError
from collateralos.domain.approval import SelfApprovalError
from collateralos.domain.inventory import InventorySnapshot
from collateralos.domain.obligation import Obligation
from collateralos.domain.reservation import ReservationConflictError
from collateralos.domain.status import ObligationDirection, ObligationStatus, SolverStatus

from fixtures.inventory_corpus import AS_OF, COUNTERPARTY_ID, build_twenty_position_corpus

MAKER_ID = "SIM-USER-MAKER-0001"
CHECKER_ID = "SIM-USER-CHECKER-0001"
OPS_ID = "SIM-USER-OPS-0001"
DEFAULT_ACTOR = "SIM-USER-TEST-0001"


@dataclass
class SqlScenario:
    store: SqlAlchemyCollateralOSStore
    obligation_id: str
    policy_id: str
    snapshot_id: str
    plan_id: str
    reservation_id: str
    instruction_id: str


def _build_reserved_plan_scenario(session_factory: sessionmaker[Session], *, seq: str) -> SqlScenario:
    """SQL-store equivalent of `tests/fixtures/store_scenario.py`'s
    `build_reserved_plan_scenario`: obligation ingested, policy validated
    and activated, one inventory snapshot ingested, a plan created (expected
    FEASIBLE_*), and that plan reserved -- all through
    `SqlAlchemyCollateralOSStore` against real Postgres."""
    store = SqlAlchemyCollateralOSStore(session_factory, keypair=generate_keypair(f"collateralos-sql-test-{seq}"))

    obligation_id = f"SIM-OBL-SQL-{seq}"
    snapshot_id = f"SIM-SNAP-SQL-{seq}"
    plan_id = f"SIM-PLAN-SQL-{seq}"
    reservation_id = f"SIM-RES-SQL-{seq}"
    instruction_id = f"SIM-INSTR-SQL-{seq}"
    now = AS_OF

    obligation = Obligation(
        obligation_id=obligation_id,
        counterparty_id=COUNTERPARTY_ID,
        entity_id="SIM-ENTITY-CASSANDRE-BANK-NA",
        agreement_id=f"SIM-AGREEMENT-SQL-{seq}",
        direction=ObligationDirection.BANK_DELIVERS,
        amount=Money(amount="100000.00", currency="USD", rounding_context="obligation"),
        due_at=now + dt.timedelta(days=1),
        source="test",
        version="v1",
        status=ObligationStatus.VALIDATED,
    )
    store.add_obligation(obligation, actor_id=DEFAULT_ACTOR, now=now)

    draft_policy = store.add_draft_policy()
    store.validate_policy(draft_policy.policy_id, actor_id=DEFAULT_ACTOR, now=now)
    store.activate_policy(draft_policy.policy_id, actor_id=DEFAULT_ACTOR, now=now)

    positions = build_twenty_position_corpus()
    snapshot = InventorySnapshot(snapshot_id=snapshot_id, version="v1", as_of=now, positions=positions)
    store.add_snapshot(snapshot, actor_id=DEFAULT_ACTOR, now=now)

    plan = store.create_plan(
        plan_id=plan_id,
        obligation_id=obligation_id,
        policy_id=draft_policy.policy_id,
        snapshot_id=snapshot_id,
        as_of=now,
        actor_id=DEFAULT_ACTOR,
        now=now,
    )
    assert plan.evidence.verified and plan.lines, (
        "test scenario obligation must be solvable against the twenty-position corpus",
        plan.status,
        plan.evidence.verifier_notes,
    )

    store.reserve_plan(
        plan_id, reservation_id=reservation_id, instruction_id=instruction_id, actor_id=DEFAULT_ACTOR, now=now,
    )

    return SqlScenario(
        store=store,
        obligation_id=obligation_id,
        policy_id=draft_policy.policy_id,
        snapshot_id=snapshot_id,
        plan_id=plan_id,
        reservation_id=reservation_id,
        instruction_id=instruction_id,
    )


def _session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


# --- full end-to-end workflow, read back from Postgres --------------------


def test_full_workflow_round_trips_through_real_postgres(collateralos_sql_engine):
    scenario = _build_reserved_plan_scenario(_session_factory(collateralos_sql_engine), seq="0001")
    store = scenario.store

    store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)
    store.checker_approve(scenario.reservation_id, checker_id=CHECKER_ID, actor_id=CHECKER_ID, now=AS_OF)

    preflight = store.run_preflight(scenario.instruction_id, actor_id=OPS_ID, now=AS_OF)
    assert preflight.passed, preflight.failure_reasons

    envelope = store.export_internal(
        scenario.instruction_id,
        idempotency_key="SIM-IDEMP-SQL-0001",
        request_fingerprint="fingerprint-a",
        actor_id=OPS_ID,
        now=AS_OF,
    )

    assert envelope.producer_product is ProducerProduct.COLLATERALOS
    assert envelope.payload["transmitted"] is False  # ADR-012: never transmitted anywhere
    assert envelope.payload["instruction_id"] == scenario.instruction_id

    trust_bundle = TrustBundle(keys_by_id={store.keypair.key_id: store.keypair.public_key_b64})
    result = verify_envelope(envelope, trust_bundle=trust_bundle, expected_audience="cassandre")
    assert result.ok, result.failures

    chain_result = store.verify_audit_chain()
    assert chain_result.ok, chain_result.reason

    # Read every assertion below back from Postgres directly, not from the
    # in-process objects the calls above returned, to prove this actually
    # round-tripped through the real tables rather than an in-memory cache.
    with Session(collateralos_sql_engine) as session:
        instruction_row = session.get(SettlementInstructionRow, scenario.instruction_id)
        assert instruction_row is not None
        assert instruction_row.idempotency_key == "SIM-IDEMP-SQL-0001"
        assert instruction_row.exported_envelope_json is not None

        reservation_row = session.get(ReservationRow, scenario.reservation_id)
        assert reservation_row.status == "ACTIVE"
        assert reservation_row.approval_json is not None

        plan_row = session.get(PlanRow, scenario.plan_id)
        assert plan_row.status in {
            SolverStatus.FEASIBLE_OPTIMAL.value,
            SolverStatus.FEASIBLE_BOUNDED_GAP.value,
            SolverStatus.FEASIBLE_HEURISTIC_VERIFIED.value,
        }

        audit_rows = session.execute(select(AuditEventRow).order_by(AuditEventRow.sequence.asc())).scalars().all()
        actions = [row.action for row in audit_rows]
        for expected_action in (
            "OBLIGATION_INGESTED",
            "INVENTORY_SNAPSHOT_INGESTED",
            "PLAN_CREATED",
            "RESERVATION_COMMITTED",
            "PLAN_MAKER_APPROVED",
            "PLAN_CHECKER_APPROVED",
            "PREFLIGHT_RUN",
            "INSTRUCTION_EXPORTED",
        ):
            assert expected_action in actions, (expected_action, actions)

        balance_row = session.get(
            InventoryBalanceRow,
            next(iter(scenario.store.get_plan(scenario.plan_id).lines)).position_id,
        )
        assert balance_row.reserved > Decimal("0")  # the reservation actually decremented reservable_now


# --- negative/edge-case parity with the in-memory store's own tests -------


def test_export_is_idempotent_for_same_key_and_same_body(collateralos_sql_engine):
    scenario = _build_reserved_plan_scenario(_session_factory(collateralos_sql_engine), seq="0002")
    store = scenario.store
    store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)
    store.checker_approve(scenario.reservation_id, checker_id=CHECKER_ID, actor_id=CHECKER_ID, now=AS_OF)

    envelope_first = store.export_internal(
        scenario.instruction_id, idempotency_key="SIM-IDEMP-SQL-0002", request_fingerprint="fingerprint-a",
        actor_id=OPS_ID, now=AS_OF,
    )
    envelope_second = store.export_internal(
        scenario.instruction_id, idempotency_key="SIM-IDEMP-SQL-0002", request_fingerprint="fingerprint-a",
        actor_id=OPS_ID, now=AS_OF,
    )

    assert envelope_second.content_hash == envelope_first.content_hash
    assert envelope_second.signature == envelope_first.signature

    with Session(collateralos_sql_engine) as session:
        export_events = session.execute(
            select(AuditEventRow).where(
                AuditEventRow.aggregate_id == scenario.instruction_id, AuditEventRow.action == "INSTRUCTION_EXPORTED"
            )
        ).scalars().all()
        # Exactly one export was actually performed -- the replay did not
        # re-execute the export or append a second audit event.
        assert len(export_events) == 1


def test_export_refuses_same_key_with_different_body(collateralos_sql_engine):
    scenario = _build_reserved_plan_scenario(_session_factory(collateralos_sql_engine), seq="0003")
    store = scenario.store
    store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)
    store.checker_approve(scenario.reservation_id, checker_id=CHECKER_ID, actor_id=CHECKER_ID, now=AS_OF)

    store.export_internal(
        scenario.instruction_id, idempotency_key="SIM-IDEMP-SQL-0003", request_fingerprint="fingerprint-a",
        actor_id=OPS_ID, now=AS_OF,
    )

    with pytest.raises(IdempotencyConflictError):
        store.export_internal(
            scenario.instruction_id, idempotency_key="SIM-IDEMP-SQL-0003", request_fingerprint="fingerprint-b",
            actor_id=OPS_ID, now=AS_OF,
        )

    with Session(collateralos_sql_engine) as session:
        export_events = session.execute(
            select(AuditEventRow).where(
                AuditEventRow.aggregate_id == scenario.instruction_id, AuditEventRow.action == "INSTRUCTION_EXPORTED"
            )
        ).scalars().all()
        assert len(export_events) == 1  # the conflicting attempt did not overwrite or duplicate the original export


def test_store_checker_approve_rejects_self_approval(collateralos_sql_engine):
    scenario = _build_reserved_plan_scenario(_session_factory(collateralos_sql_engine), seq="0004")
    store = scenario.store

    store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)
    with pytest.raises(SelfApprovalError):
        store.checker_approve(scenario.reservation_id, checker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)

    with Session(collateralos_sql_engine) as session:
        reservation_row = session.get(ReservationRow, scenario.reservation_id)
        assert '"status": "PENDING_CHECKER"' in reservation_row.approval_json


def test_store_checker_approve_fails_when_plan_changes_after_maker_approval(collateralos_sql_engine):
    scenario = _build_reserved_plan_scenario(_session_factory(collateralos_sql_engine), seq="0005")
    store = scenario.store

    store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)

    plan = store.get_plan(scenario.plan_id)
    new_status = (
        SolverStatus.FEASIBLE_BOUNDED_GAP
        if plan.status is not SolverStatus.FEASIBLE_BOUNDED_GAP
        else SolverStatus.FEASIBLE_OPTIMAL
    )
    # Simulate a re-plan changing the stored Plan's status underneath the
    # pending approval -- directly via the row, exactly the kind of
    # concurrent mutation only a real, separately-committed transaction can
    # produce (mirrors `test_approval.py`'s in-memory equivalent, which
    # mutates `store.plans[plan_id]` directly for the same reason).
    with Session(collateralos_sql_engine) as session:
        plan_row = session.get(PlanRow, scenario.plan_id)
        plan_row.status = new_status.value
        session.commit()

    with pytest.raises(ValueError, match="APPROVAL_INVALIDATED"):
        store.checker_approve(scenario.reservation_id, checker_id=CHECKER_ID, actor_id=CHECKER_ID, now=AS_OF)

    with Session(collateralos_sql_engine) as session:
        plan_row = session.get(PlanRow, scenario.plan_id)
        assert plan_row.status == SolverStatus.APPROVAL_INVALIDATED.value
        reservation_row = session.get(ReservationRow, scenario.reservation_id)
        assert '"checker_id": null' in reservation_row.approval_json
        audit_rows = session.execute(
            select(AuditEventRow).where(
                AuditEventRow.aggregate_id == scenario.reservation_id,
                AuditEventRow.action == "PLAN_APPROVAL_INVALIDATED",
            )
        ).scalars().all()
        assert len(audit_rows) == 1  # the invalidation is itself audited, not silently swallowed


# --- the genuine multi-connection Postgres concurrency proof ---------------


def test_two_separate_connections_race_to_reserve_the_same_position_under_real_postgres_locking(collateralos_sql_engine):
    """Retires the deferred-scope gap
    `collateralos.application.inventory_repository.InMemoryInventoryRepository`'s
    own docstring names: "This is explicitly NOT a substitute for a real
    multi-connection Postgres concurrency test" (also called out in the
    product README's "Known simplifications and deferred scope").

    Two genuinely separate `sqlalchemy.orm.Session` objects -- each its own
    connection, never sharing a session or transaction with the other --
    race to reserve overlapping quantities (60 + 60 against 100 available)
    of the same `inventory_balance` row via
    `collateralos.adapters.sql_reservation.reserve_positions_for_update`'s
    real `SELECT ... FOR UPDATE`. Only one can win: whichever transaction's
    lock acquisition wins commits its reservation; the loser's `SELECT ...
    FOR UPDATE` genuinely *blocks* on Postgres until the winner commits and
    releases the row lock, only then re-evaluates the pure
    `attempt_reserve` domain function against the *post-commit* balance,
    and correctly raises `ReservationConflictError` because the winner
    already consumed the capacity it needed. This is Postgres itself
    serializing two real connections -- no in-process lock, thread, or
    coordination primitive decides the outcome.
    """
    position_id = "SIM-POS-SQLCONC-0001"
    snapshot_id = "SIM-SNAP-SQLCONC-0001"
    available = Decimal("100")
    request_each = Decimal("60")  # 60 + 60 > 100: at most one request can be satisfied

    with Session(collateralos_sql_engine) as setup_session:
        setup_session.add(
            InventorySnapshotRow(snapshot_id=snapshot_id, version="v1", as_of=AS_OF, positions_json="[]")
        )
        setup_session.flush()  # snapshot row must exist before inventory_balance's FK insert below
        setup_session.add(
            InventoryBalanceRow(
                position_id=position_id,
                snapshot_id=snapshot_id,
                gross_settled=available,
                encumbered=Decimal("0"),
                pending_outbound=Decimal("0"),
                blocked_or_recalled=Decimal("0"),
                reserved=Decimal("0"),
                version=1,
            )
        )
        setup_session.commit()

    results: dict[str, object] = {}
    results_lock = threading.Lock()
    start_barrier = threading.Barrier(2)

    def attempt(name: str) -> None:
        # Each thread opens its own Session against the shared engine --
        # its own real connection and transaction, not a session or
        # transaction shared with the other thread.
        session = Session(collateralos_sql_engine)
        try:
            start_barrier.wait(timeout=10)  # launch both threads as close to simultaneously as possible
            reserve_positions_for_update(session, {position_id: request_each})
        except ReservationConflictError as exc:
            session.rollback()
            with results_lock:
                results[name] = exc
        else:
            session.commit()
            with results_lock:
                results[name] = "success"
        finally:
            session.close()

    thread_a = threading.Thread(target=attempt, args=("a",))
    thread_b = threading.Thread(target=attempt, args=("b",))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=30)
    thread_b.join(timeout=30)
    assert not thread_a.is_alive() and not thread_b.is_alive(), (
        "a reservation thread hung -- the Postgres FOR UPDATE lock did not release"
    )

    outcomes = list(results.values())
    successes = [o for o in outcomes if o == "success"]
    conflicts = [o for o in outcomes if isinstance(o, ReservationConflictError)]

    # Invariant 1: exactly one of the two genuinely raced attempts won --
    # never both (over-reservation) and never neither (a lost update).
    assert len(successes) == 1, f"expected exactly one winner, got {outcomes}"
    assert len(conflicts) == 1, f"expected exactly one conflict, got {outcomes}"

    # Invariant 2: the loser saw the *winner's post-commit* balance (a real
    # shortfall of 40, not the original 100) -- proof it was genuinely
    # blocked behind the winner's row lock and re-read fresh state, not
    # racing against stale data.
    shortfall = conflicts[0].shortfalls[position_id]
    assert shortfall == available - request_each
    assert shortfall < request_each

    # Invariant 3: the final persisted balance in Postgres reflects only the
    # winning reservation -- no lost update, no double-reservation.
    with Session(collateralos_sql_engine) as verify_session:
        row = verify_session.get(InventoryBalanceRow, position_id)
        assert row.reserved == request_each
        assert row.version == 2  # exactly one successful mutation was applied

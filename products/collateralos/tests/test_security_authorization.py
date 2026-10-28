"""SEC-04 deny-by-default authorization gate, wired into both
`CollateralOSStore` (in-memory) and `SqlAlchemyCollateralOSStore` (real
Postgres) via `collateralos.application.security` (see that module and
`README.md`'s "Security integration" section).

Proves the gate actually blocks a mutation -- raises
`collateralos.application.security.AuthorizationDeniedError`, leaves store
state unchanged -- rather than merely logging or annotating a decision that
the caller could ignore. CollateralOS has no identity-provider integration
of its own yet (a recorded residual, see `packages/cassandre_security/
THREAT_REGISTER.md`), so "unauthorized" here means an actor the store's own
`InMemoryRevocationRegistry` has been told is revoked -- the concrete,
already-wired path this product actually has for a DENY today.
"""

from __future__ import annotations

import datetime as dt

import pytest

from audit_protocol.keys import generate_keypair
from money_types import Money

from collateralos.application.security import AuthorizationDeniedError
from collateralos.application.store import CollateralOSStore
from collateralos.domain.obligation import Obligation
from collateralos.domain.status import ObligationDirection, ObligationStatus

from fixtures.inventory_corpus import AS_OF, COUNTERPARTY_ID, build_twenty_position_corpus
from fixtures.store_scenario import build_reserved_plan_scenario

MAKER_ID = "SIM-USER-MAKER-0001"
CHECKER_ID = "SIM-USER-CHECKER-0001"
REVOKED_ACTOR = "SIM-USER-REVOKED-0001"


def _obligation(obligation_id: str, *, now: dt.datetime) -> Obligation:
    return Obligation(
        obligation_id=obligation_id,
        counterparty_id=COUNTERPARTY_ID,
        entity_id="SIM-ENTITY-CASSANDRE-BANK-NA",
        agreement_id=f"SIM-AGREEMENT-{obligation_id}",
        direction=ObligationDirection.BANK_DELIVERS,
        amount=Money(amount="100000.00", currency="USD", rounding_context="obligation"),
        due_at=now + dt.timedelta(days=1),
        source="test",
        version="v1",
        status=ObligationStatus.VALIDATED,
    )


# --- in-memory store -------------------------------------------------------


def test_revoked_actor_is_denied_adding_an_obligation_in_memory():
    store = CollateralOSStore(keypair=generate_keypair("collateralos-authz-test-0001"))
    store.authz_engine.revocation_checker.revoke(REVOKED_ACTOR)

    obligation = _obligation("SIM-OBL-AUTHZ-0001", now=AS_OF)
    with pytest.raises(AuthorizationDeniedError):
        store.add_obligation(obligation, actor_id=REVOKED_ACTOR, now=AS_OF)

    # DENY actually blocked the mutation -- nothing was ingested.
    assert "SIM-OBL-AUTHZ-0001" not in store.obligations
    assert store.audit_events == []


def test_non_revoked_actor_still_succeeds_in_memory():
    store = CollateralOSStore(keypair=generate_keypair("collateralos-authz-test-0002"))
    obligation = _obligation("SIM-OBL-AUTHZ-0002", now=AS_OF)
    store.add_obligation(obligation, actor_id="SIM-USER-OPS-0001", now=AS_OF)
    assert "SIM-OBL-AUTHZ-0002" in store.obligations


def test_revoked_actor_is_denied_exporting_a_reservation_in_memory():
    """Exercises the gate on the product's most sensitive mutation
    (export) at the store level (COL-013 / SEC-04), not just ingest."""
    scenario = build_reserved_plan_scenario(seq="AUTHZ0003")
    store = scenario.store
    store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)
    store.checker_approve(scenario.reservation_id, checker_id=CHECKER_ID, actor_id=CHECKER_ID, now=AS_OF)

    store.authz_engine.revocation_checker.revoke(REVOKED_ACTOR)
    with pytest.raises(AuthorizationDeniedError):
        store.export_internal(
            scenario.instruction_id, idempotency_key="SIM-IDEMP-AUTHZ-0003",
            request_fingerprint="fp-a", actor_id=REVOKED_ACTOR, now=AS_OF,
        )

    # No export record was created by the denied attempt.
    assert "SIM-IDEMP-AUTHZ-0003" not in store.export_records


def test_revoked_actor_is_denied_across_every_gated_action():
    """One test per action would be repetitive -- this loops the eight
    gated store methods with a revoked actor and confirms every one denies,
    proving no method was missed when the gate was wired in."""
    scenario = build_reserved_plan_scenario(seq="AUTHZ0004")
    store = scenario.store
    store.authz_engine.revocation_checker.revoke(REVOKED_ACTOR)

    with pytest.raises(AuthorizationDeniedError):
        store.add_obligation(_obligation("SIM-OBL-AUTHZ-0004X", now=AS_OF), actor_id=REVOKED_ACTOR, now=AS_OF)

    # scenario.policy_id is already ACTIVE (build_reserved_plan_scenario
    # activates it), so re-activating it directly would hit the domain's
    # own "not VALIDATED" ValueError before authorization is even
    # consulted. `build_policy_bundle_sim_001` always returns the same
    # fixed "SIM-POLICY-001" id (there is only one policy bundle this
    # slice implements, DEC-COL-01), so drive it back to VALIDATED first,
    # exercise the gate, then restore ACTIVE (with a non-revoked actor) so
    # the create_plan/reserve_plan checks below still see an ACTIVE policy.
    validated_again = store.add_draft_policy()
    store.validate_policy(validated_again.policy_id, actor_id="SIM-USER-OPS-0001", now=AS_OF)
    with pytest.raises(AuthorizationDeniedError):
        store.activate_policy(validated_again.policy_id, actor_id=REVOKED_ACTOR, now=AS_OF)
    store.activate_policy(validated_again.policy_id, actor_id="SIM-USER-OPS-0001", now=AS_OF)

    positions = build_twenty_position_corpus()
    from collateralos.domain.inventory import InventorySnapshot

    with pytest.raises(AuthorizationDeniedError):
        store.add_snapshot(
            InventorySnapshot(snapshot_id="SIM-SNAP-AUTHZ-0004X", version="v1", as_of=AS_OF, positions=positions),
            actor_id=REVOKED_ACTOR, now=AS_OF,
        )

    with pytest.raises(AuthorizationDeniedError):
        store.create_plan(
            plan_id="SIM-PLAN-AUTHZ-0004X", obligation_id=scenario.obligation_id, policy_id=scenario.policy_id,
            snapshot_id=scenario.snapshot_id, as_of=AS_OF, actor_id=REVOKED_ACTOR, now=AS_OF,
        )

    with pytest.raises(AuthorizationDeniedError):
        store.reserve_plan(
            scenario.plan_id, reservation_id="SIM-RES-AUTHZ-0004X", instruction_id="SIM-INSTR-AUTHZ-0004X",
            actor_id=REVOKED_ACTOR, now=AS_OF,
        )

    with pytest.raises(AuthorizationDeniedError):
        store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=REVOKED_ACTOR, now=AS_OF)

    # Let a non-revoked maker actually approve so checker_approve has
    # something to authorize against.
    store.authz_engine.revocation_checker.unrevoke(REVOKED_ACTOR)
    store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)
    store.authz_engine.revocation_checker.revoke(REVOKED_ACTOR)

    with pytest.raises(AuthorizationDeniedError):
        store.checker_approve(scenario.reservation_id, checker_id=CHECKER_ID, actor_id=REVOKED_ACTOR, now=AS_OF)

    with pytest.raises(AuthorizationDeniedError):
        store.export_internal(
            scenario.instruction_id, idempotency_key="SIM-IDEMP-AUTHZ-0004X",
            request_fingerprint="fp-a", actor_id=REVOKED_ACTOR, now=AS_OF,
        )


def test_authorization_denied_error_carries_the_revoked_reason_code():
    from cassandre_security.authorization import ReasonCode

    store = CollateralOSStore(keypair=generate_keypair("collateralos-authz-test-0005"))
    store.authz_engine.revocation_checker.revoke(REVOKED_ACTOR)
    obligation = _obligation("SIM-OBL-AUTHZ-0005", now=AS_OF)
    with pytest.raises(AuthorizationDeniedError) as excinfo:
        store.add_obligation(obligation, actor_id=REVOKED_ACTOR, now=AS_OF)
    assert excinfo.value.reason_code is ReasonCode.REVOKED


# --- SQL store (real Postgres; skips cleanly without CASSANDRE_TEST_DATABASE_URL) --


def test_revoked_actor_is_denied_reserving_a_plan_against_real_postgres(collateralos_sql_engine):
    from sqlalchemy.orm import Session, sessionmaker

    from collateralos.adapters.models import ReservationRow
    from collateralos.adapters.sql_store import SqlAlchemyCollateralOSStore

    session_factory = sessionmaker(bind=collateralos_sql_engine, expire_on_commit=False)
    store = SqlAlchemyCollateralOSStore(session_factory, keypair=generate_keypair("collateralos-authz-sql-0001"))

    obligation_id = "SIM-OBL-AUTHZ-SQL-0001"
    snapshot_id = "SIM-SNAP-AUTHZ-SQL-0001"
    plan_id = "SIM-PLAN-AUTHZ-SQL-0001"

    store.add_obligation(_obligation(obligation_id, now=AS_OF), actor_id="SIM-USER-OPS-0001", now=AS_OF)
    draft_policy = store.add_draft_policy()
    store.validate_policy(draft_policy.policy_id, actor_id="SIM-USER-OPS-0001", now=AS_OF)
    store.activate_policy(draft_policy.policy_id, actor_id="SIM-USER-OPS-0001", now=AS_OF)

    from collateralos.domain.inventory import InventorySnapshot

    positions = build_twenty_position_corpus()
    store.add_snapshot(
        InventorySnapshot(snapshot_id=snapshot_id, version="v1", as_of=AS_OF, positions=positions),
        actor_id="SIM-USER-OPS-0001", now=AS_OF,
    )
    store.create_plan(
        plan_id=plan_id, obligation_id=obligation_id, policy_id=draft_policy.policy_id, snapshot_id=snapshot_id,
        as_of=AS_OF, actor_id="SIM-USER-OPS-0001", now=AS_OF,
    )

    store.authz_engine.revocation_checker.revoke(REVOKED_ACTOR)
    with pytest.raises(AuthorizationDeniedError):
        store.reserve_plan(
            plan_id, reservation_id="SIM-RES-AUTHZ-SQL-0001", instruction_id="SIM-INSTR-AUTHZ-SQL-0001",
            actor_id=REVOKED_ACTOR, now=AS_OF,
        )

    with Session(collateralos_sql_engine) as session:
        assert session.get(ReservationRow, "SIM-RES-AUTHZ-SQL-0001") is None

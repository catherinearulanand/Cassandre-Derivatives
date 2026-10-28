"""Shared store-wiring helper for CollateralOS application-layer tests
(maker/checker approval, preflight/export idempotency, signed envelope
export, and the API layer's TestClient tests).

Builds one `CollateralOSStore` all the way through obligation -> active
policy -> inventory snapshot -> plan -> reservation, so each test only has
to exercise the one step it actually cares about instead of re-deriving this
wiring itself.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from audit_protocol.keys import generate_keypair
from money_types import Money

from collateralos.application.store import CollateralOSStore
from collateralos.domain.inventory import InventorySnapshot
from collateralos.domain.obligation import Obligation
from collateralos.domain.status import ObligationDirection, ObligationStatus

from fixtures.inventory_corpus import AS_OF, COUNTERPARTY_ID, build_twenty_position_corpus

DEFAULT_ACTOR = "SIM-USER-TEST-0001"


@dataclass
class WiredScenario:
    store: CollateralOSStore
    obligation_id: str
    policy_id: str
    snapshot_id: str
    plan_id: str
    reservation_id: str
    instruction_id: str


def build_reserved_plan_scenario(
    *,
    seq: str = "0001",
    obligation_amount: str = "100000.00",
    now: dt.datetime | None = None,
) -> WiredScenario:
    """Obligation ingested, policy validated+activated, one inventory
    snapshot ingested, a plan created against it (expected FEASIBLE_*), and
    that plan reserved -- i.e. everything a maker/checker approval,
    preflight, or export test needs already in place."""
    now = now or AS_OF
    store = CollateralOSStore(keypair=generate_keypair(f"collateralos-test-key-{seq}"))

    obligation_id = f"SIM-OBL-{seq}"
    snapshot_id = f"SIM-SNAP-{seq}"
    plan_id = f"SIM-PLAN-{seq}"
    reservation_id = f"SIM-RES-{seq}"
    instruction_id = f"SIM-INSTR-{seq}"

    obligation = Obligation(
        obligation_id=obligation_id,
        counterparty_id=COUNTERPARTY_ID,
        entity_id="SIM-ENTITY-CASSANDRE-BANK-NA",
        agreement_id=f"SIM-AGREEMENT-{seq}",
        direction=ObligationDirection.BANK_DELIVERS,
        amount=Money(amount=obligation_amount, currency="USD", rounding_context="obligation"),
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
        plan_id,
        reservation_id=reservation_id,
        instruction_id=instruction_id,
        actor_id=DEFAULT_ACTOR,
        now=now,
    )

    return WiredScenario(
        store=store,
        obligation_id=obligation_id,
        policy_id=draft_policy.policy_id,
        snapshot_id=snapshot_id,
        plan_id=plan_id,
        reservation_id=reservation_id,
        instruction_id=instruction_id,
    )

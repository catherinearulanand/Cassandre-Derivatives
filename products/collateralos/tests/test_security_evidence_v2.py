"""SEC-10 canonical evidence v2 (`cassandre_security.canonical_evidence`),
wired additively into `export_internal` on both stores
(`application/security.py::build_export_evidence_v2`).

This is explicitly ADDITIVE: the existing `cassandre_contracts`-signed
`Envelope` export (already proven by `test_signed_export_envelope.py`) is
untouched -- these tests only cover the new v2 container alongside it.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from cassandre_security.canonical_evidence import EvidenceVerificationError, verify_evidence

from fixtures.inventory_corpus import AS_OF
from fixtures.store_scenario import build_reserved_plan_scenario

MAKER_ID = "SIM-USER-MAKER-0001"
CHECKER_ID = "SIM-USER-CHECKER-0001"
OPS_ID = "SIM-USER-OPS-0001"


def _reserved_and_approved_scenario(seq: str):
    scenario = build_reserved_plan_scenario(seq=seq)
    store = scenario.store
    store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)
    store.checker_approve(scenario.reservation_id, checker_id=CHECKER_ID, actor_id=CHECKER_ID, now=AS_OF)
    return scenario


def test_export_produces_an_additive_evidence_v2_container_alongside_the_envelope():
    scenario = _reserved_and_approved_scenario("EV0001")
    store = scenario.store

    envelope = store.export_internal(
        scenario.instruction_id, idempotency_key="SIM-IDEMP-EV-0001", request_fingerprint="fp-a",
        actor_id=OPS_ID, now=AS_OF,
    )

    # The existing envelope export is completely unaffected.
    assert envelope.payload["instruction_id"] == scenario.instruction_id

    evidence = store.evidence_v2_records["SIM-IDEMP-EV-0001"]
    assert evidence.body.schema_version == "cassandre.evidence.v2"
    assert evidence.body.object_id == scenario.instruction_id
    assert set(evidence.signatures) == {"ed25519"}

    verify_evidence(
        evidence,
        trusted_ed25519_public_key_b64=store.keypair.public_key_b64,
        now=AS_OF,
    )  # raises on any failure -- reaching here is the pass condition


def test_evidence_v2_is_not_recreated_on_an_idempotent_replay():
    scenario = _reserved_and_approved_scenario("EV0002")
    store = scenario.store

    store.export_internal(
        scenario.instruction_id, idempotency_key="SIM-IDEMP-EV-0002", request_fingerprint="fp-a",
        actor_id=OPS_ID, now=AS_OF,
    )
    first = store.evidence_v2_records["SIM-IDEMP-EV-0002"]

    store.export_internal(
        scenario.instruction_id, idempotency_key="SIM-IDEMP-EV-0002", request_fingerprint="fp-a",
        actor_id=OPS_ID, now=AS_OF,
    )
    second = store.evidence_v2_records["SIM-IDEMP-EV-0002"]
    assert first is second  # replay did not reseal a new container


def test_evidence_v2_tamper_is_caught_by_verify_evidence():
    scenario = _reserved_and_approved_scenario("EV0003")
    store = scenario.store

    store.export_internal(
        scenario.instruction_id, idempotency_key="SIM-IDEMP-EV-0003", request_fingerprint="fp-a",
        actor_id=OPS_ID, now=AS_OF,
    )
    evidence = store.evidence_v2_records["SIM-IDEMP-EV-0003"]

    tampered_body = replace(evidence.body, content_digest_hex="00" * 48)
    tampered = replace(evidence, body=tampered_body)

    with pytest.raises(EvidenceVerificationError):
        verify_evidence(tampered, trusted_ed25519_public_key_b64=store.keypair.public_key_b64, now=AS_OF)


# --- SQL store parity (real Postgres; skips cleanly without the DB env var) -


def test_sql_store_export_produces_evidence_v2_too(collateralos_sql_engine):
    import datetime as dt

    from sqlalchemy.orm import Session, sessionmaker

    from audit_protocol.keys import generate_keypair
    from money_types import Money

    from collateralos.adapters.sql_store import SqlAlchemyCollateralOSStore
    from collateralos.domain.inventory import InventorySnapshot
    from collateralos.domain.obligation import Obligation
    from collateralos.domain.status import ObligationDirection, ObligationStatus

    from fixtures.inventory_corpus import COUNTERPARTY_ID, build_twenty_position_corpus

    session_factory = sessionmaker(bind=collateralos_sql_engine, expire_on_commit=False)
    store = SqlAlchemyCollateralOSStore(session_factory, keypair=generate_keypair("collateralos-evidence-sql-0001"))

    obligation_id = "SIM-OBL-EV-SQL-0001"
    snapshot_id = "SIM-SNAP-EV-SQL-0001"
    plan_id = "SIM-PLAN-EV-SQL-0001"
    reservation_id = "SIM-RES-EV-SQL-0001"
    instruction_id = "SIM-INSTR-EV-SQL-0001"

    store.add_obligation(
        Obligation(
            obligation_id=obligation_id, counterparty_id=COUNTERPARTY_ID, entity_id="SIM-ENTITY-CASSANDRE-BANK-NA",
            agreement_id="SIM-AGREEMENT-EV-SQL-0001", direction=ObligationDirection.BANK_DELIVERS,
            amount=Money(amount="100000.00", currency="USD", rounding_context="obligation"),
            due_at=AS_OF + dt.timedelta(days=1), source="test", version="v1", status=ObligationStatus.VALIDATED,
        ),
        actor_id=OPS_ID, now=AS_OF,
    )
    draft_policy = store.add_draft_policy()
    store.validate_policy(draft_policy.policy_id, actor_id=OPS_ID, now=AS_OF)
    store.activate_policy(draft_policy.policy_id, actor_id=OPS_ID, now=AS_OF)
    store.add_snapshot(
        InventorySnapshot(snapshot_id=snapshot_id, version="v1", as_of=AS_OF, positions=build_twenty_position_corpus()),
        actor_id=OPS_ID, now=AS_OF,
    )
    store.create_plan(
        plan_id=plan_id, obligation_id=obligation_id, policy_id=draft_policy.policy_id, snapshot_id=snapshot_id,
        as_of=AS_OF, actor_id=OPS_ID, now=AS_OF,
    )
    store.reserve_plan(plan_id, reservation_id=reservation_id, instruction_id=instruction_id, actor_id=OPS_ID, now=AS_OF)
    store.maker_approve(reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)
    store.checker_approve(reservation_id, checker_id=CHECKER_ID, actor_id=CHECKER_ID, now=AS_OF)

    store.export_internal(
        instruction_id, idempotency_key="SIM-IDEMP-EV-SQL-0001", request_fingerprint="fp-a", actor_id=OPS_ID, now=AS_OF,
    )

    evidence = store.get_export_evidence_v2("SIM-IDEMP-EV-SQL-0001")
    assert evidence is not None
    verify_evidence(evidence, trusted_ed25519_public_key_b64=store.keypair.public_key_b64, now=AS_OF)

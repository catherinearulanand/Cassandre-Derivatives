"""Preflight + idempotent export tests (plan Section 12.4 steps 11-12, 12.8;
work items COL-012/COL-013).

Covers: a fresh preflight run immediately before export must pass for a
fully maker+checker-approved, still-active reservation; exporting twice with
the same idempotency key and the same request body returns the exact same
result (no re-export, no duplicate audit event); the same idempotency key
reused with a *different* request body is refused rather than silently
re-executed (the 409-equivalent `IdempotencyConflictError`); and export
itself fails closed with `PreflightFailedError` when preflight would fail
(e.g. the reservation was released).
"""

from __future__ import annotations

import pytest

from collateralos.application.idempotency import IdempotencyConflictError, PreflightFailedError

from fixtures.inventory_corpus import AS_OF
from fixtures.store_scenario import build_reserved_plan_scenario

MAKER_ID = "SIM-USER-MAKER-0001"
CHECKER_ID = "SIM-USER-CHECKER-0001"
OPS_ID = "SIM-USER-OPS-0001"


def _approved_scenario(seq: str):
    scenario = build_reserved_plan_scenario(seq=seq)
    store = scenario.store
    store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)
    store.checker_approve(scenario.reservation_id, checker_id=CHECKER_ID, actor_id=CHECKER_ID, now=AS_OF)
    return scenario


def test_fresh_preflight_passes_for_approved_active_reservation():
    scenario = _approved_scenario("0501")
    store = scenario.store

    preflight = store.run_preflight(scenario.instruction_id, actor_id=OPS_ID, now=AS_OF)

    assert preflight.passed, preflight.failure_reasons
    assert preflight.failure_reasons == ()


def test_export_is_idempotent_for_same_key_and_same_body():
    scenario = _approved_scenario("0502")
    store = scenario.store

    # Fresh preflight immediately before export, as the API layer will do.
    assert store.run_preflight(scenario.instruction_id, actor_id=OPS_ID, now=AS_OF).passed

    envelope_first = store.export_internal(
        scenario.instruction_id,
        idempotency_key="SIM-IDEMP-0502",
        request_fingerprint="fingerprint-a",
        actor_id=OPS_ID,
        now=AS_OF,
    )

    envelope_second = store.export_internal(
        scenario.instruction_id,
        idempotency_key="SIM-IDEMP-0502",
        request_fingerprint="fingerprint-a",
        actor_id=OPS_ID,
        now=AS_OF,
    )

    assert envelope_second == envelope_first
    assert envelope_second.content_hash == envelope_first.content_hash
    assert envelope_second.signature == envelope_first.signature

    # Exactly one export was actually performed -- the replay did not
    # re-execute the export or append a second audit event.
    export_events = [e for e in store.audit_events if e.action == "INSTRUCTION_EXPORTED"]
    assert len(export_events) == 1


def test_export_refuses_same_key_with_different_body():
    scenario = _approved_scenario("0503")
    store = scenario.store
    assert store.run_preflight(scenario.instruction_id, actor_id=OPS_ID, now=AS_OF).passed

    store.export_internal(
        scenario.instruction_id,
        idempotency_key="SIM-IDEMP-0503",
        request_fingerprint="fingerprint-a",
        actor_id=OPS_ID,
        now=AS_OF,
    )

    with pytest.raises(IdempotencyConflictError):
        store.export_internal(
            scenario.instruction_id,
            idempotency_key="SIM-IDEMP-0503",
            request_fingerprint="fingerprint-b",  # different body, same key -- must be refused (409-equivalent)
            actor_id=OPS_ID,
            now=AS_OF,
        )

    # The conflicting attempt did not overwrite or duplicate the original export.
    export_events = [e for e in store.audit_events if e.action == "INSTRUCTION_EXPORTED"]
    assert len(export_events) == 1


def test_export_fails_closed_when_preflight_would_fail():
    scenario = _approved_scenario("0504")
    store = scenario.store

    # Release the reservation after approval but before export -- preflight
    # (re-run internally by export_internal) must now fail.
    store.release_reservation(scenario.reservation_id, actor_id=OPS_ID, now=AS_OF)

    with pytest.raises(PreflightFailedError) as excinfo:
        store.export_internal(
            scenario.instruction_id,
            idempotency_key="SIM-IDEMP-0504",
            request_fingerprint="fingerprint-a",
            actor_id=OPS_ID,
            now=AS_OF,
        )
    assert any("RESERVATION_NOT_ACTIVE" in reason for reason in excinfo.value.reasons)

    # No export record was created for the failed attempt.
    export_events = [e for e in store.audit_events if e.action == "INSTRUCTION_EXPORTED"]
    assert len(export_events) == 0

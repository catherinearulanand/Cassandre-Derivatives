"""API-level tests for the CollateralOS FastAPI app (work item COL-014).

Each test builds a fresh app (`create_app()`), so every test gets its own
in-memory `CollateralOSStore` -- no Postgres/Docker dependency and no
cross-test state leakage. These exercise the wire-format request/response
contract end to end; the underlying business rules already have dedicated
unit/integration coverage under `products/collateralos/tests/`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi.testclient import TestClient

from collateralos_api.main import create_app

AS_OF = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.isoformat()


def _money(amount: str, currency: str = "USD") -> dict:
    return {"amount": amount, "currency": currency}


def _quantity(amount: str, unit: str) -> dict:
    return {"amount": amount, "unit": unit}


def _cash_position_wire(position_id: str, quantity: str) -> dict:
    return {
        "position_id": position_id,
        "owner_entity_id": "SIM-ENTITY-CASSANDRE-BANK-NA",
        "account_id": "SIM-ACCT-COLLAT-0001",
        "location": "SIM-LOC-CUSTODY-0001",
        "asset_class": "CASH_USD",
        "issuer_group_id": None,
        "currency": "USD",
        "market_value": _money(quantity),
        "quantity": _quantity(quantity, "CASH_USD"),
        "price_as_of": _iso(AS_OF - dt.timedelta(days=1)),
        "stale_after": _iso(AS_OF + dt.timedelta(days=1)),
        "is_encumbered": False,
        "is_recalled": False,
        "is_self_issued": False,
        "is_affiliate_issued": False,
    }


def _client() -> TestClient:
    return TestClient(create_app())


def _ingest_obligation(client: TestClient, *, obligation_id: str, amount: str, counterparty_id: str = "SIM-CPTY-0001") -> None:
    resp = client.post(
        "/v1/obligations",
        json={
            "obligation_id": obligation_id,
            "counterparty_id": counterparty_id,
            "entity_id": "SIM-ENTITY-CASSANDRE-BANK-NA",
            "agreement_id": "SIM-AGREEMENT-0001",
            "direction": "BANK_DELIVERS",
            "amount": _money(amount),
            "due_at": _iso(AS_OF + dt.timedelta(days=1)),
            "source": "test",
            "version": "v1",
            "status": "VALIDATED",
        },
    )
    assert resp.status_code == 201, resp.text


def _activate_policy(client: TestClient) -> str:
    resp = client.post("/v1/policy-bundles:validate", json={})
    assert resp.status_code == 201, resp.text
    policy_id = resp.json()["policy_id"]
    resp = client.post(f"/v1/policy-bundles/{policy_id}:activate", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ACTIVE"
    return policy_id


def _ingest_snapshot(client: TestClient, *, snapshot_id: str, positions: list[dict]) -> None:
    resp = client.post(
        "/v1/inventory-snapshots",
        json={"snapshot_id": snapshot_id, "version": "v1", "as_of": _iso(AS_OF), "positions": positions},
    )
    assert resp.status_code == 201, resp.text


def test_full_happy_path_workflow_through_http():
    client = _client()

    _ingest_obligation(client, obligation_id="SIM-OBL-API-0001", amount="100000.00")
    policy_id = _activate_policy(client)
    _ingest_snapshot(
        client,
        snapshot_id="SIM-SNAP-API-0001",
        positions=[_cash_position_wire("SIM-POS-API-0001", "500000")],
    )

    resp = client.post(
        "/v1/plans",
        json={
            "plan_id": "SIM-PLAN-API-0001",
            "obligation_id": "SIM-OBL-API-0001",
            "policy_id": policy_id,
            "snapshot_id": "SIM-SNAP-API-0001",
            "as_of": _iso(AS_OF),
        },
    )
    assert resp.status_code == 201, resp.text
    plan_body = resp.json()
    assert plan_body["status"] in {"FEASIBLE_OPTIMAL", "FEASIBLE_BOUNDED_GAP", "FEASIBLE_HEURISTIC_VERIFIED"}
    assert plan_body["evidence"]["verified"] is True
    assert len(plan_body["lines"]) >= 1

    resp = client.get("/v1/plans/SIM-PLAN-API-0001")
    assert resp.status_code == 200
    assert resp.json()["plan_id"] == "SIM-PLAN-API-0001"

    resp = client.get("/v1/plans/SIM-PLAN-API-0001/evidence")
    assert resp.status_code == 200
    assert resp.json()["method"] in {"MIP", "GREEDY_HEURISTIC_VERIFIED"}

    resp = client.post(
        "/v1/plans/SIM-PLAN-API-0001:reserve",
        json={"reservation_id": "SIM-RES-API-0001", "instruction_id": "SIM-INSTR-API-0001"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "ACTIVE"

    resp = client.post(
        "/v1/reservations/SIM-RES-API-0001:maker-approve", json={"maker_id": "SIM-USER-MAKER-0001"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "PENDING_CHECKER"

    resp = client.post(
        "/v1/reservations/SIM-RES-API-0001:checker-approve", json={"checker_id": "SIM-USER-CHECKER-0001"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "APPROVED"

    resp = client.post("/v1/instructions/SIM-INSTR-API-0001:preflight", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["passed"] is True

    export_body = {"idempotency_key": "SIM-IDEMP-API-0001"}
    resp = client.post("/v1/instructions/SIM-INSTR-API-0001:export-internal", json=export_body)
    assert resp.status_code == 200, resp.text
    envelope_first = resp.json()
    assert envelope_first["producer_product"] == "collateralos"
    assert envelope_first["signature"] is not None
    assert envelope_first["payload"]["transmitted"] is False

    # Same idempotency key, same body -> exact same result (no re-export).
    resp = client.post("/v1/instructions/SIM-INSTR-API-0001:export-internal", json=export_body)
    assert resp.status_code == 200, resp.text
    envelope_second = resp.json()
    assert envelope_second["content_hash"] == envelope_first["content_hash"]
    assert envelope_second["signature"] == envelope_first["signature"]

    # Same idempotency key, different body -> refused (409 conflict).
    resp = client.post(
        "/v1/instructions/SIM-INSTR-API-0001:export-internal",
        json={"idempotency_key": "SIM-IDEMP-API-0001", "actor_id": "SIM-USER-DIFFERENT-0002"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_maker_equals_checker_rejected_over_http():
    client = _client()
    _ingest_obligation(client, obligation_id="SIM-OBL-API-0002", amount="50000.00")
    policy_id = _activate_policy(client)
    _ingest_snapshot(
        client, snapshot_id="SIM-SNAP-API-0002", positions=[_cash_position_wire("SIM-POS-API-0002", "500000")]
    )
    client.post(
        "/v1/plans",
        json={
            "plan_id": "SIM-PLAN-API-0002",
            "obligation_id": "SIM-OBL-API-0002",
            "policy_id": policy_id,
            "snapshot_id": "SIM-SNAP-API-0002",
            "as_of": _iso(AS_OF),
        },
    )
    client.post(
        "/v1/plans/SIM-PLAN-API-0002:reserve",
        json={"reservation_id": "SIM-RES-API-0002", "instruction_id": "SIM-INSTR-API-0002"},
    )
    client.post("/v1/reservations/SIM-RES-API-0002:maker-approve", json={"maker_id": "SIM-USER-SAME-0001"})

    resp = client.post(
        "/v1/reservations/SIM-RES-API-0002:checker-approve", json={"checker_id": "SIM-USER-SAME-0001"}
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "SELF_APPROVAL_REJECTED"


def test_checker_approve_invalidated_when_plan_binding_drifts_over_http():
    client = _client()
    app_store = None  # populated below via the client's underlying app

    _ingest_obligation(client, obligation_id="SIM-OBL-API-0003", amount="50000.00")
    policy_id = _activate_policy(client)
    _ingest_snapshot(
        client, snapshot_id="SIM-SNAP-API-0003", positions=[_cash_position_wire("SIM-POS-API-0003", "500000")]
    )
    client.post(
        "/v1/plans",
        json={
            "plan_id": "SIM-PLAN-API-0003",
            "obligation_id": "SIM-OBL-API-0003",
            "policy_id": policy_id,
            "snapshot_id": "SIM-SNAP-API-0003",
            "as_of": _iso(AS_OF),
        },
    )
    client.post(
        "/v1/plans/SIM-PLAN-API-0003:reserve",
        json={"reservation_id": "SIM-RES-API-0003", "instruction_id": "SIM-INSTR-API-0003"},
    )
    client.post("/v1/reservations/SIM-RES-API-0003:maker-approve", json={"maker_id": "SIM-USER-MAKER-0001"})

    # Reach into the app's store to simulate the plan changing underneath the
    # approval between maker and checker approval (same technique as the
    # store-level test in products/collateralos/tests/test_approval.py).
    from dataclasses import replace as dc_replace

    from collateralos.domain.status import SolverStatus

    store = client.app.state.store
    plan = store.get_plan("SIM-PLAN-API-0003")
    new_status = (
        SolverStatus.FEASIBLE_BOUNDED_GAP if plan.status is not SolverStatus.FEASIBLE_BOUNDED_GAP else SolverStatus.FEASIBLE_OPTIMAL
    )
    store.plans["SIM-PLAN-API-0003"] = dc_replace(plan, status=new_status)

    resp = client.post(
        "/v1/reservations/SIM-RES-API-0003:checker-approve", json={"checker_id": "SIM-USER-CHECKER-0001"}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "APPROVAL_INVALIDATED"


def test_reservation_conflict_returns_409_over_http():
    client = _client()
    _ingest_obligation(client, obligation_id="SIM-OBL-API-0004", amount="100000.00")
    policy_id = _activate_policy(client)
    # Exactly one eligible position, sized so the plan must use (very
    # nearly) all of it -- a second reservation attempt against the same
    # plan then has nothing left and must conflict.
    _ingest_snapshot(
        client, snapshot_id="SIM-SNAP-API-0004", positions=[_cash_position_wire("SIM-POS-API-0004", "100000")]
    )
    client.post(
        "/v1/plans",
        json={
            "plan_id": "SIM-PLAN-API-0004",
            "obligation_id": "SIM-OBL-API-0004",
            "policy_id": policy_id,
            "snapshot_id": "SIM-SNAP-API-0004",
            "as_of": _iso(AS_OF),
        },
    )

    resp = client.post(
        "/v1/plans/SIM-PLAN-API-0004:reserve",
        json={"reservation_id": "SIM-RES-API-0004-A", "instruction_id": "SIM-INSTR-API-0004-A"},
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        "/v1/plans/SIM-PLAN-API-0004:reserve",
        json={"reservation_id": "SIM-RES-API-0004-B", "instruction_id": "SIM-INSTR-API-0004-B"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "RESERVATION_CONFLICT"


def test_get_unknown_plan_returns_404():
    client = _client()
    resp = client.get("/v1/plans/SIM-PLAN-DOES-NOT-EXIST")
    assert resp.status_code == 404
    assert resp.json()["error"] == "NOT_FOUND"


def test_export_before_approval_fails_preflight_over_http():
    client = _client()
    _ingest_obligation(client, obligation_id="SIM-OBL-API-0005", amount="50000.00")
    policy_id = _activate_policy(client)
    _ingest_snapshot(
        client, snapshot_id="SIM-SNAP-API-0005", positions=[_cash_position_wire("SIM-POS-API-0005", "500000")]
    )
    client.post(
        "/v1/plans",
        json={
            "plan_id": "SIM-PLAN-API-0005",
            "obligation_id": "SIM-OBL-API-0005",
            "policy_id": policy_id,
            "snapshot_id": "SIM-SNAP-API-0005",
            "as_of": _iso(AS_OF),
        },
    )
    client.post(
        "/v1/plans/SIM-PLAN-API-0005:reserve",
        json={"reservation_id": "SIM-RES-API-0005", "instruction_id": "SIM-INSTR-API-0005"},
    )
    # No maker/checker approval performed.
    resp = client.post("/v1/instructions/SIM-INSTR-API-0005:preflight", json={})
    assert resp.status_code == 200
    assert resp.json()["passed"] is False
    assert any("APPROVAL" in reason for reason in resp.json()["failure_reasons"])

    resp = client.post(
        "/v1/instructions/SIM-INSTR-API-0005:export-internal", json={"idempotency_key": "SIM-IDEMP-API-0005"}
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "PREFLIGHT_FAILED"

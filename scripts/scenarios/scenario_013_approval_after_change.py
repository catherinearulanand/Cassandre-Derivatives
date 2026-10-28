"""SIM-SCENARIO-013 "Approval-after-change" (CollateralOS).

Catalogue expected_control_behavior: "Approval invalidated."

Genuine external-HTTP-only trigger (no reaching into process internals --
unlike `apps/collateralos_api/tests/test_api.py`'s equivalent in-process
test, which pokes `client.app.state.store` directly; this script is a
separate OS process talking only over HTTP, so that technique isn't
available here, and a different real external action is used instead):

Get a plan maker-approved, then -- before checker-approval -- re-ingest
the SAME `snapshot_id` again with a bumped `version`. CollateralOS's
approval binding resolves `inventory_snapshot_version` by looking the
snapshot up by ID at approval time (`_binding_inputs`), not by a value
frozen when the plan was created, so this is a real drift an external
caller can legitimately cause -- e.g. a routine re-ingest of the same
day's snapshot after a correction, racing an in-flight approval. The
checker-approve call should then fail closed with `APPROVAL_INVALIDATED`.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import (  # noqa: E402
    activate_fresh_policy,
    banner,
    check_service_up,
    col_money,
    collateralos_client,
    conclude,
    find_inventory_position,
    now,
    position_wire_from_corpus,
    run_suffix,
    step,
)

SCENARIO_ID = "SIM-SCENARIO-013"
SCENARIO_NAME = "Approval-after-change"
EXPECTED = "Approval invalidated"

POSITION_ID = "SIM-INV-POS-00001"


def main() -> None:
    banner(f"{SCENARIO_ID} {SCENARIO_NAME!r} -- expected: {EXPECTED}")

    if not check_service_up("http://127.0.0.1:8003", "CollateralOS"):
        conclude(SCENARIO_ID, SCENARIO_NAME, False, "CollateralOS (port 8003) is not reachable")

    position = find_inventory_position(POSITION_ID)
    suffix = run_suffix()
    client = collateralos_client()
    as_of = now()

    obligation_id = f"SIM-OBL-{suffix}"
    snapshot_id = f"SIM-SNAP-{suffix}"
    plan_id = f"SIM-PLAN-{suffix}"
    reservation_id = f"SIM-RES-{suffix}"
    instruction_id = f"SIM-INSTR-{suffix}"

    step("Ingest obligation, policy, snapshot v1, plan, reservation")
    resp = client.post(
        "/v1/obligations",
        {
            "obligation_id": obligation_id,
            "counterparty_id": "SIM-CPTY-0003",
            "entity_id": "SIM-ENTITY-CASSANDRE-BANK-NA",
            "agreement_id": f"SIM-AGREEMENT-{suffix}",
            "direction": "BANK_DELIVERS",
            "amount": col_money("50000.00"),
            "due_at": (as_of + dt.timedelta(days=1)).isoformat(),
            "source": f"scenario-{SCENARIO_ID}",
            "version": "v1",
            "status": "VALIDATED",
        },
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"obligation ingest failed: {resp.text}")

    policy_id = activate_fresh_policy(client)

    position_wire = position_wire_from_corpus(position, fresh_as_of=as_of)
    resp = client.post(
        "/v1/inventory-snapshots",
        {"snapshot_id": snapshot_id, "version": "v1", "as_of": as_of.isoformat(), "positions": [position_wire]},
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"snapshot v1 ingest failed: {resp.text}")

    resp = client.post(
        "/v1/plans",
        {"plan_id": plan_id, "obligation_id": obligation_id, "policy_id": policy_id, "snapshot_id": snapshot_id, "as_of": as_of.isoformat()},
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"plan creation failed: {resp.text}")

    resp = client.post("/v1/plans/" + plan_id + ":reserve", {"reservation_id": reservation_id, "instruction_id": instruction_id})
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"reservation failed: {resp.text}")

    step(f"Maker-approve reservation {reservation_id}")
    resp = client.post(f"/v1/reservations/{reservation_id}:maker-approve", {"maker_id": "SIM-USER-MAKER-SCENARIO013"})
    if resp.status_code != 200:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"maker-approve failed: {resp.text}")

    step(f"BEFORE checker-approval: re-ingest the SAME snapshot_id ({snapshot_id}) with a bumped version")
    resp = client.post(
        "/v1/inventory-snapshots",
        {"snapshot_id": snapshot_id, "version": "v2-corrected", "as_of": as_of.isoformat(), "positions": [position_wire]},
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"snapshot v2 re-ingest failed: {resp.text}")

    step(f"Checker-approve reservation {reservation_id} -- binding now stale, expect refusal")
    resp = client.post(f"/v1/reservations/{reservation_id}:checker-approve", {"checker_id": "SIM-USER-CHECKER-SCENARIO013"})

    checker_rejected = resp.status_code == 409
    error_is_approval_invalidated = False
    try:
        error_is_approval_invalidated = resp.json().get("error") == "APPROVAL_INVALIDATED"
    except Exception:
        pass

    passed = checker_rejected and error_is_approval_invalidated
    reason = "ok" if passed else f"checker-approve returned status={resp.status_code} body={resp.text}"
    conclude(SCENARIO_ID, SCENARIO_NAME, passed, reason)


if __name__ == "__main__":
    main()

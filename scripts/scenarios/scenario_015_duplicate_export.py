"""SIM-SCENARIO-015 "Duplicate export" (CollateralOS).

Catalogue expected_control_behavior: "Idempotency prevents second export."

Full happy-path pipeline through to export, then calls `:export-internal`
TWICE with the identical idempotency key and request body. The second
call must return the exact same signed envelope (same content_hash, same
signature) rather than re-executing or double-submitting -- proving
replay, not re-export. A third call with the SAME key but a DIFFERENT
body must be refused outright (409), proving the idempotency key is
bound to the exact request, not just blindly short-circuiting.
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

SCENARIO_ID = "SIM-SCENARIO-015"
SCENARIO_NAME = "Duplicate export"
EXPECTED = "Idempotency prevents second export"

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
    idempotency_key = f"SIM-IDEMP-{suffix}"

    step("Full happy-path pipeline: obligation -> policy -> snapshot -> plan -> reserve -> maker -> checker -> preflight")
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
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"snapshot ingest failed: {resp.text}")

    resp = client.post(
        "/v1/plans",
        {"plan_id": plan_id, "obligation_id": obligation_id, "policy_id": policy_id, "snapshot_id": snapshot_id, "as_of": as_of.isoformat()},
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"plan creation failed: {resp.text}")

    resp = client.post("/v1/plans/" + plan_id + ":reserve", {"reservation_id": reservation_id, "instruction_id": instruction_id})
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"reservation failed: {resp.text}")

    resp = client.post(f"/v1/reservations/{reservation_id}:maker-approve", {"maker_id": "SIM-USER-MAKER-SCENARIO015"})
    if resp.status_code != 200:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"maker-approve failed: {resp.text}")

    resp = client.post(f"/v1/reservations/{reservation_id}:checker-approve", {"checker_id": "SIM-USER-CHECKER-SCENARIO015"})
    if resp.status_code != 200:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"checker-approve failed: {resp.text}")

    resp = client.post(f"/v1/instructions/{instruction_id}:preflight", {})
    if resp.status_code != 200 or resp.json().get("passed") is not True:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"preflight did not pass: {resp.text}")

    step(f"First export with idempotency_key={idempotency_key!r}")
    export_body = {"idempotency_key": idempotency_key}
    resp = client.post(f"/v1/instructions/{instruction_id}:export-internal", export_body)
    if resp.status_code != 200:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"first export failed: {resp.text}")
    envelope_first = resp.json()

    step("Second export: IDENTICAL idempotency_key and body -- expect exact replay, not re-execution")
    resp = client.post(f"/v1/instructions/{instruction_id}:export-internal", export_body)
    if resp.status_code != 200:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"second (replay) export failed: {resp.text}")
    envelope_second = resp.json()

    replay_matches_exactly = (
        envelope_second.get("content_hash") == envelope_first.get("content_hash")
        and envelope_second.get("signature") == envelope_first.get("signature")
    )
    print(f"\nfirst content_hash={envelope_first.get('content_hash')}")
    print(f"second content_hash={envelope_second.get('content_hash')} (must match)")

    step("Third export: SAME idempotency_key, DIFFERENT body -- expect 409 conflict, not silent reuse")
    conflicting_body = {"idempotency_key": idempotency_key, "actor_id": "SIM-USER-DIFFERENT-ACTOR"}
    resp = client.post(f"/v1/instructions/{instruction_id}:export-internal", conflicting_body)
    conflict_detected = resp.status_code == 409

    passed = replay_matches_exactly and conflict_detected
    reason = (
        "ok" if passed else
        f"replay_matches_exactly={replay_matches_exactly} conflict_detected={conflict_detected} (status={resp.status_code})"
    )
    conclude(SCENARIO_ID, SCENARIO_NAME, passed, reason)


if __name__ == "__main__":
    main()

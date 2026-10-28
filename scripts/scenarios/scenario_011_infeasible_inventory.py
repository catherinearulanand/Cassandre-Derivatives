"""SIM-SCENARIO-011 "Infeasible inventory" (CollateralOS).

Catalogue expected_control_behavior: "Solver evidence and operational
diagnosis; no fabricated plan."

Construct an obligation whose amount vastly exceeds the value of the only
eligible position in the snapshot, so the planner cannot possibly satisfy
it. Show the plan comes back with an explicit infeasibility status
(`INFEASIBLE_SOLVER_CONFIRMED`), carrying diagnostic evidence, and with NO
allocation lines -- never a partial/fabricated plan presented as valid.
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

SCENARIO_ID = "SIM-SCENARIO-011"
SCENARIO_NAME = "Infeasible inventory"
EXPECTED = "Solver evidence and operational diagnosis; no fabricated plan"

# A real corpus CASH_USD position -- small, genuinely eligible, but nowhere
# near enough to cover the obligation amount requested below.
SMALL_POSITION_ID = "SIM-INV-POS-00001"


def main() -> None:
    banner(f"{SCENARIO_ID} {SCENARIO_NAME!r} -- expected: {EXPECTED}")

    if not check_service_up("http://127.0.0.1:8003", "CollateralOS"):
        conclude(SCENARIO_ID, SCENARIO_NAME, False, "CollateralOS (port 8003) is not reachable")

    small_position = find_inventory_position(SMALL_POSITION_ID)
    print(f"Real corpus position used: {small_position['position_id']} market_value={small_position['market_value']}")

    suffix = run_suffix()
    client = collateralos_client()
    as_of = now()

    obligation_id = f"SIM-OBL-{suffix}"
    snapshot_id = f"SIM-SNAP-{suffix}"
    plan_id = f"SIM-PLAN-{suffix}"

    step("Ingest an obligation far larger than any available inventory can cover")
    resp = client.post(
        "/v1/obligations",
        {
            "obligation_id": obligation_id,
            "counterparty_id": "SIM-CPTY-0003",
            "entity_id": "SIM-ENTITY-CASSANDRE-BANK-NA",
            "agreement_id": f"SIM-AGREEMENT-{suffix}",
            "direction": "BANK_DELIVERS",
            "amount": col_money("500000000.00"),  # 500 million -- deliberately unsatisfiable
            "due_at": (as_of + dt.timedelta(days=1)).isoformat(),
            "source": f"scenario-{SCENARIO_ID}",
            "version": "v1",
            "status": "VALIDATED",
        },
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"obligation ingest failed: {resp.text}")

    step("Validate + activate policy")
    policy_id = activate_fresh_policy(client)

    step("Ingest a snapshot with only one small, real eligible position")
    position_wire = position_wire_from_corpus(small_position, fresh_as_of=as_of)
    resp = client.post(
        "/v1/inventory-snapshots",
        {"snapshot_id": snapshot_id, "version": "v1", "as_of": as_of.isoformat(), "positions": [position_wire]},
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"inventory snapshot ingest failed: {resp.text}")

    step("Create the plan -- expect explicit infeasibility, not a fabricated partial allocation")
    resp = client.post(
        "/v1/plans",
        {
            "plan_id": plan_id,
            "obligation_id": obligation_id,
            "policy_id": policy_id,
            "snapshot_id": snapshot_id,
            "as_of": as_of.isoformat(),
        },
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"plan creation failed unexpectedly: {resp.text}")

    plan_body = resp.json()
    status = plan_body.get("status")
    lines = plan_body.get("lines", [])
    evidence = plan_body.get("evidence", {})
    print(f"\nplan status={status!r} lines={lines} evidence={evidence}")

    is_explicit_infeasible = status == "INFEASIBLE_SOLVER_CONFIRMED"
    has_no_fabricated_lines = len(lines) == 0
    has_diagnostic_evidence = bool(evidence)

    passed = is_explicit_infeasible and has_no_fabricated_lines and has_diagnostic_evidence
    reason = (
        "ok" if passed else
        f"status={status!r} (expected INFEASIBLE_SOLVER_CONFIRMED) lines_count={len(lines)} "
        f"has_evidence={has_diagnostic_evidence}"
    )
    conclude(SCENARIO_ID, SCENARIO_NAME, passed, reason)


if __name__ == "__main__":
    main()

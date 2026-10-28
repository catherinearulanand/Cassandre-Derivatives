"""SIM-SCENARIO-008 "Stale market/inventory" (CollateralOS).

Catalogue expected_control_behavior: "Calculation or plan non-actionable."

Uses CollateralOS (chosen over RiskOS per the catalogue's "your choice" --
CollateralOS's real corpus inventory fixtures already carry a genuinely
stale-priced, otherwise-eligible real position, which is the cleanest real
fixture support for this scenario). Real corpus position `SIM-INV-POS-00003`
(`schemas/fixtures/inventory.json`, `asset_class="CASH_USD"`,
`trap_types=["STALE_PRICE"]`, `stale_after="2026-05-30T00:00:00Z"`) is
ingested with its REAL (unmodified) `stale_after` -- unlike other scenario
scripts in this suite, this one deliberately does NOT re-base the
timestamps to the real wall clock, because staleness relative to the real
clock is exactly what this scenario needs to demonstrate, and the corpus's
own `stale_after` is already (realistically) in the past.

`collateralos.core.model_inputs.build_eligible_model_inputs` excludes any
position the eligibility compiler marks ineligible -- including
`STALE_PRICE` -- from the solver's decision space entirely (never merely
penalized). With the obligation's only offered position stale, both the MIP
solver and the greedy fallback find nothing, so
`collateralos.core.planner.plan_obligation` returns
`SolverStatus.INFEASIBLE_SOLVER_CONFIRMED` with zero allocation lines --
outside `ACTIONABLE_STATUSES`, i.e. the plan is non-actionable, and
`POST /v1/plans/{plan_id}:reserve` on it must itself fail.
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

SCENARIO_ID = "SIM-SCENARIO-008"
SCENARIO_NAME = "Stale market/inventory"
EXPECTED = "Calculation or plan non-actionable"

STALE_POSITION_ID = "SIM-INV-POS-00003"


def main() -> None:
    banner(f"{SCENARIO_ID} {SCENARIO_NAME!r} -- expected: {EXPECTED}")

    if not check_service_up("http://127.0.0.1:8003", "CollateralOS"):
        conclude(SCENARIO_ID, SCENARIO_NAME, False, "CollateralOS (port 8003) is not reachable")

    stale_position = find_inventory_position(STALE_POSITION_ID)
    print(f"Real corpus stale position: position_id={stale_position['position_id']} "
          f"asset_class={stale_position['asset_class']} trap_types={stale_position['trap_types']} "
          f"stale_after={stale_position['stale_after']} market_value={stale_position['market_value']}")

    suffix = run_suffix()
    client = collateralos_client()

    obligation_id = f"SIM-OBL-{suffix}"
    snapshot_id = f"SIM-SNAP-{suffix}"
    plan_id = f"SIM-PLAN-{suffix}"
    as_of = now()

    step("Ingest the obligation")
    resp = client.post(
        "/v1/obligations",
        {
            "obligation_id": obligation_id,
            "counterparty_id": "SIM-CPTY-0001",
            "entity_id": "SIM-ENTITY-CASSANDRE-BANK-NA",
            "agreement_id": f"SIM-AGREEMENT-{suffix}",
            "direction": "BANK_DELIVERS",
            "amount": col_money("100000.00"),
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

    step("Ingest an inventory snapshot containing ONLY the real stale position, at the real wall clock")
    # fresh_as_of is deliberately omitted: this is the one scenario script
    # that wants the corpus's own (already real-clock-past) stale_after, not
    # a re-based fresh one.
    position = position_wire_from_corpus(stale_position)
    resp = client.post(
        "/v1/inventory-snapshots",
        {"snapshot_id": snapshot_id, "version": "v1", "as_of": as_of.isoformat(), "positions": [position]},
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"inventory snapshot ingest failed: {resp.text}")

    step("Create the plan")
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
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"plan creation call itself failed: {resp.text}")
    plan_body = resp.json()
    print(f"\nplan status={plan_body['status']!r} verified={plan_body['evidence']['verified']!r} "
          f"lines={len(plan_body['lines'])} verifier_notes={plan_body['evidence']['verifier_notes']}")

    actionable_statuses = {"FEASIBLE_OPTIMAL", "FEASIBLE_BOUNDED_GAP", "FEASIBLE_HEURISTIC_VERIFIED"}
    plan_is_non_actionable = plan_body["status"] not in actionable_statuses and len(plan_body["lines"]) == 0

    step("Confirm a non-actionable plan cannot be reserved either")
    reservation_id = f"SIM-RES-{suffix}"
    instruction_id = f"SIM-INSTR-{suffix}"
    resp = client.post(
        f"/v1/plans/{plan_id}:reserve",
        {"reservation_id": reservation_id, "instruction_id": instruction_id},
    )
    reserve_blocked = resp.status_code == 409
    print(f"\nreserve attempt on non-actionable plan: HTTP {resp.status_code}")

    passed = plan_is_non_actionable and reserve_blocked
    reason = (
        "ok" if passed else
        f"plan_status={plan_body['status']!r} lines={len(plan_body['lines'])} reserve_http={resp.status_code}"
    )
    conclude(SCENARIO_ID, SCENARIO_NAME, passed, reason)


if __name__ == "__main__":
    main()

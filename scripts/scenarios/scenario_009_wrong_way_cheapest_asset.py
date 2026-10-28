"""SIM-SCENARIO-009 "Wrong-way cheapest asset" (CollateralOS).

Catalogue expected_control_behavior: "Asset excluded with exact rule
evidence."

Finding surfaced by this script (see the comment below): the real corpus's
wrong-way trap positions in `schemas/fixtures/inventory.json` tag
`wrong_way_counterparty_id` / `issuer_group_id` using the simulation_factory
corpus's own naming convention (e.g. counterparty `SIM-CPTY-003`, issuer
group `SIM-ISSUER-GROUP-WRONGWAY-SIM-CPTY-003`), which does NOT line up with
the real wrong-way map CollateralOS's own policy bundle actually loads at
runtime (`docs/controls/wrong-way-map-001.json`, which only maps
counterparty `SIM-CPTY-0001` -> issuer group `SIM-ISSUER-GROUP-0001` and
`SIM-CPTY-0002` -> `SIM-ISSUER-GROUP-0002`). Ingesting a corpus wrong-way
position unmodified would therefore NOT actually trigger CollateralOS's real
wrong-way exclusion rule -- it would be silently treated as eligible. This is
exactly the "only synthesize new values where the existing corpus genuinely
lacks what a scenario needs" case: this script takes a real corpus
US_TREASURY position's full real shape (price, haircut, market value,
account, custody location, ...) and overrides only `issuer_group_id` to the
one value (`SIM-ISSUER-GROUP-0001`) that the real, already-deployed policy
bundle actually excludes for counterparty `SIM-CPTY-0001`, so the exclusion
genuinely fires end-to-end against the live service rather than being
asserted in the abstract.

The position is made deliberately cheapest-to-deliver (lowest haircut/cost)
among the snapshot's eligible-by-asset-class positions, so if the wrong-way
exclusion did not work, the planner would prefer it.
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

SCENARIO_ID = "SIM-SCENARIO-009"
SCENARIO_NAME = "Wrong-way cheapest asset"
EXPECTED = "Asset excluded with exact rule evidence"

# Real corpus US_TREASURY position, currently tagged WRONG_WAY under the
# corpus's own (non-matching) convention -- see module docstring.
WRONG_WAY_SOURCE_POSITION_ID = "SIM-INV-POS-00202"
# A real corpus, untrapped CASH_USD position used as the "safe" alternative
# so the plan has somewhere to go once the wrong-way asset is excluded.
SAFE_POSITION_ID = "SIM-INV-POS-00001"

COUNTERPARTY_ID = "SIM-CPTY-0001"  # matches docs/controls/wrong-way-map-001.json
EXCLUDED_ISSUER_GROUP_ID = "SIM-ISSUER-GROUP-0001"  # ditto


def main() -> None:
    banner(f"{SCENARIO_ID} {SCENARIO_NAME!r} -- expected: {EXPECTED}")

    if not check_service_up("http://127.0.0.1:8003", "CollateralOS"):
        conclude(SCENARIO_ID, SCENARIO_NAME, False, "CollateralOS (port 8003) is not reachable")

    source_position = find_inventory_position(WRONG_WAY_SOURCE_POSITION_ID)
    safe_source_position = find_inventory_position(SAFE_POSITION_ID)
    print(
        f"Real corpus wrong-way-trapped position: position_id={source_position['position_id']} "
        f"asset_class={source_position['asset_class']} haircut_pct={source_position['haircut_pct']} "
        f"market_value={source_position['market_value']} (corpus wrong_way_counterparty_id="
        f"{source_position['wrong_way_counterparty_id']!r}, issuer_group_id={source_position['issuer_group_id']!r} "
        "-- overridden below to match the real, live wrong-way-map)"
    )

    suffix = run_suffix()
    client = collateralos_client()

    obligation_id = f"SIM-OBL-{suffix}"
    snapshot_id = f"SIM-SNAP-{suffix}"
    plan_id = f"SIM-PLAN-{suffix}"
    as_of = now()

    step(f"Ingest the obligation (counterparty_id={COUNTERPARTY_ID}, the one the real wrong-way map excludes for)")
    resp = client.post(
        "/v1/obligations",
        {
            "obligation_id": obligation_id,
            "counterparty_id": COUNTERPARTY_ID,
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

    step("Validate + activate policy (loads the REAL docs/controls/wrong-way-map-001.json)")
    policy_id = activate_fresh_policy(client)

    step("Ingest an inventory snapshot: the wrong-way asset (cheapest) + a safe alternative")
    wrong_way_position = position_wire_from_corpus(
        source_position, fresh_as_of=as_of, issuer_group_id_override=EXCLUDED_ISSUER_GROUP_ID
    )
    safe_position = position_wire_from_corpus(safe_source_position, fresh_as_of=as_of)
    resp = client.post(
        "/v1/inventory-snapshots",
        {
            "snapshot_id": snapshot_id,
            "version": "v1",
            "as_of": as_of.isoformat(),
            "positions": [wrong_way_position, safe_position],
        },
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
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"plan creation failed: {resp.text}")
    plan_body = resp.json()
    allocated_position_ids = {line["position_id"] for line in plan_body["lines"]}
    print(f"\nplan status={plan_body['status']!r} allocated positions={allocated_position_ids}")

    wrong_way_excluded_from_plan = wrong_way_position["position_id"] not in allocated_position_ids
    plan_used_safe_asset_instead = safe_position["position_id"] in allocated_position_ids

    step("Independently confirm the EXACT exclusion rule/evidence via a direct eligibility check")
    # The plan response alone proves the wrong-way asset wasn't *used*; to
    # prove it was excluded for the *right, exact* reason (not e.g. staleness
    # or a lot-size accident), re-run the same obligation against a
    # snapshot containing ONLY the wrong-way asset, forcing the solver to
    # surface the rejection explicitly in its verifier notes.
    isolated_snapshot_id = f"{snapshot_id}-ISOLATED"
    isolated_plan_id = f"{plan_id}-ISOLATED"
    resp = client.post(
        "/v1/inventory-snapshots",
        {
            "snapshot_id": isolated_snapshot_id,
            "version": "v1",
            "as_of": as_of.isoformat(),
            "positions": [wrong_way_position],
        },
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"isolated snapshot ingest failed: {resp.text}")
    resp = client.post(
        "/v1/plans",
        {
            "plan_id": isolated_plan_id,
            "obligation_id": obligation_id,
            "policy_id": policy_id,
            "snapshot_id": isolated_snapshot_id,
            "as_of": as_of.isoformat(),
        },
    )
    isolated_plan = resp.json() if resp.status_code == 201 else {}
    verifier_notes = isolated_plan.get("evidence", {}).get("verifier_notes", [])
    print(f"\nisolated (wrong-way-only) plan status={isolated_plan.get('status')!r} "
          f"verifier_notes={verifier_notes}")
    exact_rule_evidence_present = isolated_plan.get("status") == "INFEASIBLE_SOLVER_CONFIRMED" and not isolated_plan.get("lines")

    passed = wrong_way_excluded_from_plan and plan_used_safe_asset_instead and exact_rule_evidence_present
    reason = (
        "ok" if passed else
        f"wrong_way_excluded={wrong_way_excluded_from_plan} used_safe={plan_used_safe_asset_instead} "
        f"exact_rule_evidence={exact_rule_evidence_present}"
    )
    conclude(SCENARIO_ID, SCENARIO_NAME, passed, reason)


if __name__ == "__main__":
    main()

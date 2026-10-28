"""SIM-SCENARIO-002 "Delivery by bank" (RiskOS + CollateralOS).

Catalogue expected_control_behavior: "Verified allocation, reservation,
approval, preflight, export."

RiskOS: a portfolio with a large NEGATIVE net exposure (the simulation bank,
as calculation entity, is over-exposed the other way) and zero collateral
held, so METHOD-PACK-001 resolves to CallStatus.RETURN with a concrete call
amount -- per `riskos.core.method_pack.resolve_transfer_parties`, RETURN
means the calculation party (the bank) delivers to the counterparty. That is
exactly "delivery by bank", and it is the only direction CollateralOS's S1
slice implements (`ObligationDirection.BANK_DELIVERS`;
`COUNTERPARTY_DELIVERS` is an explicit, documented deferred-scope gap in
`collateralos.domain.obligation.Obligation.__post_init__`). The call amount
becomes a CollateralOS BANK_DELIVERS obligation, which then runs the full
pipeline: activate policy -> ingest inventory snapshot -> plan -> reserve ->
maker-approve -> checker-approve -> preflight -> export-internal.
"""

from __future__ import annotations

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
    empty_collateral_package,
    find_inventory_position,
    money,
    now,
    now_iso,
    pack_money,
    position_wire_from_corpus,
    register_riskos_entities,
    riskos_client,
    run_suffix,
    step,
)

SCENARIO_ID = "SIM-SCENARIO-002"
SCENARIO_NAME = "Delivery by bank"
EXPECTED = "Verified allocation, reservation, approval, preflight, export"


def main() -> None:
    banner(f"{SCENARIO_ID} {SCENARIO_NAME!r} -- expected: {EXPECTED}")

    for base, label in (("http://127.0.0.1:8002", "RiskOS"), ("http://127.0.0.1:8003", "CollateralOS")):
        if not check_service_up(base, label):
            conclude(SCENARIO_ID, SCENARIO_NAME, False, f"{label} ({base}) is not reachable")

    suffix = run_suffix()

    # --- Part 1: RiskOS produces a DELIVER call amount ---------------------
    calc_entity_id = f"SIM-ENTITY-CALC-{suffix}"
    cpty_entity_id = f"SIM-ENTITY-CPTY-{suffix}"
    netting_set_id = f"SIM-NETSET-{suffix}"
    agreement_id = f"SIM-AGREEMENT-{suffix}"
    portfolio_id = f"SIM-PORTFOLIO-{suffix}"
    collateral_id = f"SIM-COLLATERAL-{suffix}"
    run_id = f"SIM-RUN-{suffix}"
    manifest_id = f"SIM-MANIFEST-{suffix}"

    rclient = riskos_client()

    step("[RiskOS] Register legal entities, netting set, approved agreement terms")
    register_riskos_entities(
        rclient,
        calc_entity_id=calc_entity_id,
        cpty_entity_id=cpty_entity_id,
        netting_set_id=netting_set_id,
        agreement_id=agreement_id,
    )

    step("[RiskOS] Ingest a portfolio snapshot with a large negative net MTM (bank owes)")
    resp = rclient.post(
        "/v1/portfolio-snapshots",
        {
            "snapshot_id": portfolio_id,
            "as_of": now_iso(),
            "trades": [
                {
                    "trade_id": f"SIM-TRADE-{suffix}-0001",
                    "calculation_entity_id": calc_entity_id,
                    "counterparty_entity_id": cpty_entity_id,
                    "netting_set_id": netting_set_id,
                    "product_family": "IRS",
                    "lifecycle_state": "ACTIVE",
                    "disposition": "ACCEPTED",
                    "mtm": money("-250000.00"),
                },
            ],
        },
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"portfolio snapshot ingest failed: {resp.text}")

    step("[RiskOS] Ingest an empty collateral state package")
    empty_collateral_package(
        rclient,
        package_id=collateral_id,
        calc_entity_id=calc_entity_id,
        netting_set_id=netting_set_id,
        as_of_iso=now_iso(),
    )

    step("[RiskOS] Run the MARGIN_CALL calculation")
    resp = rclient.post(
        "/v1/calculation-runs",
        {
            "run_id": run_id,
            "purpose": "MARGIN_CALL",
            "calculation_entity_id": calc_entity_id,
            "counterparty_entity_id": cpty_entity_id,
            "agreement_id": agreement_id,
            "portfolio_snapshot_id": portfolio_id,
            "collateral_package_id": collateral_id,
            "manifest": {
                "manifest_id": manifest_id,
                "expected_accepted_count": 1,
                "expected_accepted_mtm_total": money("-250000.00"),
            },
            "counterparty_call": {
                "party_label": "COUNTERPARTY",
                "call_status": "RETURN",
                "call_amount": pack_money("250000"),
                "responded": True,
            },
        },
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"calculation run failed: {resp.text}")
    run_payload = resp.json()
    waterfall = run_payload.get("waterfall") or {}
    call_status = waterfall.get("call_status")
    call_amount = waterfall.get("call_amount", {}).get("amount")
    transferor = waterfall.get("transferor_entity_id")

    print(f"\nRiskOS call_status={call_status!r} call_amount={call_amount!r} transferor={transferor!r}")

    if call_status != "RETURN" or run_payload.get("calculation_status") != "COMPLETE":
        conclude(
            SCENARIO_ID, SCENARIO_NAME, False,
            f"expected a COMPLETE/RETURN run, got calculation_status={run_payload.get('calculation_status')!r} "
            f"call_status={call_status!r}",
        )
    if transferor != calc_entity_id:
        conclude(
            SCENARIO_ID, SCENARIO_NAME, False,
            f"expected the calculation entity (the bank) to be the transferor, got {transferor!r}",
        )

    # --- Part 2: CollateralOS full pipeline for that call amount -----------
    cclient = collateralos_client()

    obligation_id = f"SIM-OBL-{suffix}"
    snapshot_id = f"SIM-SNAP-{suffix}"
    plan_id = f"SIM-PLAN-{suffix}"
    reservation_id = f"SIM-RES-{suffix}"
    instruction_id = f"SIM-INSTR-{suffix}"
    idempotency_key = f"SIM-IDEMP-{suffix}"

    as_of = now()

    step("[CollateralOS] Ingest the obligation (the bank's RETURN call becomes a BANK_DELIVERS obligation)")
    resp = cclient.post(
        "/v1/obligations",
        {
            "obligation_id": obligation_id,
            "counterparty_id": cpty_entity_id,
            "entity_id": calc_entity_id,
            "agreement_id": agreement_id,
            "direction": "BANK_DELIVERS",
            "amount": col_money(call_amount),
            "due_at": as_of.isoformat(),
            "source": f"riskos-run:{run_id}",
            "version": "v1",
            "status": "VALIDATED",
        },
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"obligation ingest failed: {resp.text}")

    step("[CollateralOS] Validate + activate the one provisional policy bundle")
    policy_id = activate_fresh_policy(cclient)

    step("[CollateralOS] Ingest an inventory snapshot built from real corpus eligible positions")
    # Real CASH_USD / US_TREASURY positions from schemas/fixtures/inventory.json
    # that carry no trap at all (trap slot 0 == "NONE"), so they are cleanly
    # eligible and fresh -- the actual allocation target of this plan.
    position_ids = ["SIM-INV-POS-00001", "SIM-INV-POS-00002"]
    positions = [
        position_wire_from_corpus(find_inventory_position(pid), fresh_as_of=as_of) for pid in position_ids
    ]
    for p in positions:
        print(f"  using real corpus position {p['position_id']} asset_class={p['asset_class']} value={p['market_value']}")
    resp = cclient.post(
        "/v1/inventory-snapshots",
        {"snapshot_id": snapshot_id, "version": "v1", "as_of": as_of.isoformat(), "positions": positions},
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"inventory snapshot ingest failed: {resp.text}")

    step("[CollateralOS] Create the allocation plan")
    resp = cclient.post(
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
    print(f"\nplan status={plan_body['status']!r} verified={plan_body['evidence']['verified']!r} lines={len(plan_body['lines'])}")

    if plan_body["status"] not in {"FEASIBLE_OPTIMAL", "FEASIBLE_BOUNDED_GAP", "FEASIBLE_HEURISTIC_VERIFIED"}:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"plan not actionable: status={plan_body['status']!r}")

    step("[CollateralOS] Reserve the plan")
    resp = cclient.post(
        f"/v1/plans/{plan_id}:reserve",
        {"reservation_id": reservation_id, "instruction_id": instruction_id},
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"reservation failed: {resp.text}")

    step("[CollateralOS] Maker-approve")
    resp = cclient.post(f"/v1/reservations/{reservation_id}:maker-approve", {"maker_id": "SIM-USER-MAKER-0001"})
    if resp.status_code != 200 or resp.json().get("status") != "PENDING_CHECKER":
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"maker-approve failed: {resp.text}")

    step("[CollateralOS] Checker-approve (distinct actor)")
    resp = cclient.post(f"/v1/reservations/{reservation_id}:checker-approve", {"checker_id": "SIM-USER-CHECKER-0001"})
    if resp.status_code != 200 or resp.json().get("status") != "APPROVED":
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"checker-approve failed: {resp.text}")

    step("[CollateralOS] Settlement preflight")
    resp = cclient.post(f"/v1/instructions/{instruction_id}:preflight", {})
    preflight_passed = resp.status_code == 200 and resp.json().get("passed") is True
    if not preflight_passed:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"preflight did not pass: {resp.text}")

    step("[CollateralOS] Export (signed internal envelope)")
    resp = cclient.post(f"/v1/instructions/{instruction_id}:export-internal", {"idempotency_key": idempotency_key})
    export_ok = (
        resp.status_code == 200
        and resp.json().get("producer_product") == "collateralos"
        and resp.json().get("signature") is not None
    )

    passed = export_ok
    reason = "ok" if passed else f"export failed or malformed: {resp.text}"
    conclude(SCENARIO_ID, SCENARIO_NAME, passed, reason)


if __name__ == "__main__":
    main()

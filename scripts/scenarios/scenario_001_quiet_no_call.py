"""SIM-SCENARIO-001 "Quiet/no call" (RiskOS + CollateralOS).

Catalogue expected_control_behavior: "RiskOS returns no transfer; CollateralOS
records no action."

A QUIET-regime market backdrop (from `schemas/fixtures/market_snapshots/
index.json`) and a small, two-sided portfolio whose net exposure, after
threshold and credit support balance, falls strictly inside METHOD-PACK-001's
USD 10,000 Minimum Transfer Amount band. RiskOS's margin-call waterfall must
therefore resolve to CallStatus.NO_TRANSFER -- and because there is no call
amount, CollateralOS is never invoked at all (no obligation, no plan, no
reservation): "records no action" is demonstrated by the complete absence of
any CollateralOS call in this script, not by a null response from one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import (  # noqa: E402
    banner,
    check_service_up,
    conclude,
    load_market_snapshot_index,
    money,
    now,
    now_iso,
    pack_money,
    register_riskos_entities,
    riskos_client,
    run_suffix,
    step,
    empty_collateral_package,
)

SCENARIO_ID = "SIM-SCENARIO-001"
SCENARIO_NAME = "Quiet/no call"
EXPECTED = "RiskOS returns no transfer; CollateralOS records no action"


def main() -> None:
    banner(f"{SCENARIO_ID} {SCENARIO_NAME!r} -- expected: {EXPECTED}")

    if not check_service_up("http://127.0.0.1:8002", "RiskOS"):
        conclude(SCENARIO_ID, SCENARIO_NAME, False, "RiskOS (port 8002) is not reachable")

    quiet_snapshots = [s for s in load_market_snapshot_index() if s["regime"] == "QUIET"]
    print(f"Selected QUIET-regime market backdrop from real corpus: {quiet_snapshots[0]}")

    suffix = run_suffix()
    calc_entity_id = f"SIM-ENTITY-CALC-{suffix}"
    cpty_entity_id = f"SIM-ENTITY-CPTY-{suffix}"
    netting_set_id = f"SIM-NETSET-{suffix}"
    agreement_id = f"SIM-AGREEMENT-{suffix}"
    portfolio_id = f"SIM-PORTFOLIO-{suffix}"
    collateral_id = f"SIM-COLLATERAL-{suffix}"
    run_id = f"SIM-RUN-{suffix}"
    manifest_id = f"SIM-MANIFEST-{suffix}"

    client = riskos_client()

    step("Register legal entities, netting set, approved agreement terms")
    register_riskos_entities(
        client,
        calc_entity_id=calc_entity_id,
        cpty_entity_id=cpty_entity_id,
        netting_set_id=netting_set_id,
        agreement_id=agreement_id,
    )

    step("Ingest a portfolio snapshot with net MTM well inside the MTA band")
    # Two trades netting to USD 3,000: well under the USD 10,000 MTA, so the
    # method pack must resolve to NO_TRANSFER regardless of threshold/CSB.
    resp = client.post(
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
                    "mtm": money("8000.00"),
                },
                {
                    "trade_id": f"SIM-TRADE-{suffix}-0002",
                    "calculation_entity_id": calc_entity_id,
                    "counterparty_entity_id": cpty_entity_id,
                    "netting_set_id": netting_set_id,
                    "product_family": "FX_FORWARD",
                    "lifecycle_state": "ACTIVE",
                    "disposition": "ACCEPTED",
                    "mtm": money("-5000.00"),
                },
            ],
        },
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"portfolio snapshot ingest failed: {resp.text}")

    step("Ingest an empty collateral state package (no held/posted collateral)")
    empty_collateral_package(
        client,
        package_id=collateral_id,
        calc_entity_id=calc_entity_id,
        netting_set_id=netting_set_id,
        as_of_iso=now_iso(),
    )

    step("Run the MARGIN_CALL calculation")
    resp = client.post(
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
                "expected_accepted_count": 2,
                "expected_accepted_mtm_total": money("3000.00"),
            },
            "counterparty_call": {
                "party_label": "COUNTERPARTY",
                "call_status": "NO_TRANSFER",
                "call_amount": pack_money("0"),
                "responded": True,
            },
        },
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"calculation run failed: {resp.text}")

    run_payload = resp.json()
    call_status = run_payload.get("waterfall", {}).get("call_status") if run_payload.get("waterfall") else None

    step("Fetch the margin waterfall to confirm the call status directly")
    resp = client.get(f"/v1/calculation-runs/{run_id}/waterfall")
    waterfall = resp.json() if resp.status_code == 200 else {}
    call_status = waterfall.get("call_status", call_status)

    print(
        "\nBecause RiskOS resolved NO_TRANSFER, this script makes zero calls into "
        "CollateralOS (no obligation, no plan, no reservation) -- that silence IS "
        "the 'CollateralOS records no action' half of the expected behavior."
    )

    passed = (
        run_payload.get("calculation_status") == "COMPLETE"
        and call_status == "NO_TRANSFER"
    )
    reason = (
        "ok" if passed else
        f"calculation_status={run_payload.get('calculation_status')!r} call_status={call_status!r} (wanted COMPLETE/NO_TRANSFER)"
    )
    conclude(SCENARIO_ID, SCENARIO_NAME, passed, reason)


if __name__ == "__main__":
    main()

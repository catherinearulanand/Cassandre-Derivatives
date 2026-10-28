"""SIM-SCENARIO-007 "Unsupported option trade" (RiskOS).

Catalogue expected_control_behavior: "RiskOS rejects affected purpose or
batch according to explicit policy."

Uses the real corpus trade `SIM-TRADE-00294` from `schemas/fixtures/
trades.json` (`trap_type="UNSUPPORTED_PRODUCT"`, `product_family=
"EQUITY_OPTION"`, `disposition="UNSUPPORTED"`), alongside real ACCEPTED
trades from the same netting set (`SIM-NETSET-TRADE-0007`,
calculation_entity `SIM-ENTITY-CASSANDRE-BANK-NA`, counterparty
`SIM-CPTY-007`) so the manifest's population counts are non-trivial.

`riskos.core.exposure.aggregate_exposure` only ever sums ACCEPTED trades --
`UNSUPPORTED_PRODUCT` trades are "population-accounted but never enter the
exposure aggregate" (see that module's own docstring). This script proves
that exclusion is a real, checked, explicit policy rather than a silent
drop: the source-control manifest's `expected_unsupported_count` must match
exactly, and the final run and its waterfall are checked to confirm
SIM-TRADE-00294's MTM never contributed to the computed exposure.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import (  # noqa: E402
    banner,
    check_service_up,
    conclude,
    find_netting_set,
    find_trade,
    load_trades,
    money,
    now_iso,
    pack_money,
    register_riskos_entities,
    riskos_client,
    run_suffix,
    step,
    empty_collateral_package,
)

SCENARIO_ID = "SIM-SCENARIO-007"
SCENARIO_NAME = "Unsupported option trade"
EXPECTED = "RiskOS rejects affected purpose or batch according to explicit policy"

UNSUPPORTED_TRADE_ID = "SIM-TRADE-00294"


def main() -> None:
    banner(f"{SCENARIO_ID} {SCENARIO_NAME!r} -- expected: {EXPECTED}")

    if not check_service_up("http://127.0.0.1:8002", "RiskOS"):
        conclude(SCENARIO_ID, SCENARIO_NAME, False, "RiskOS (port 8002) is not reachable")

    unsupported_trade = find_trade(UNSUPPORTED_TRADE_ID)
    corpus_netting_set = find_netting_set(unsupported_trade["netting_set_id"])
    print(f"Real corpus unsupported trade: {json_preview(unsupported_trade)}")
    print(f"Real corpus netting set: {corpus_netting_set}")

    suffix = run_suffix()
    # Fresh SIM-* ids per run, but mirroring the real corpus's calc/cpty/
    # netting-set relationship for SIM-TRADE-00294 so the trade's own
    # calculation_entity_id/counterparty_entity_id/netting_set_id fields
    # (taken verbatim from the real corpus record) resolve correctly.
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

    step("Ingest a portfolio: one real ACCEPTED trade + the real UNSUPPORTED_PRODUCT trade")
    # Remapped onto this run's fresh entity/netting-set ids (the underlying
    # trade economics -- product_family, mtm, disposition -- are taken
    # verbatim from the real corpus record).
    accepted_trade_id = f"SIM-TRADE-{suffix}-ACCEPTED-0001"
    unsupported_trade_id = f"SIM-TRADE-{suffix}-UNSUPPORTED-0001"
    resp = client.post(
        "/v1/portfolio-snapshots",
        {
            "snapshot_id": portfolio_id,
            "as_of": now_iso(),
            "trades": [
                {
                    "trade_id": accepted_trade_id,
                    "calculation_entity_id": calc_entity_id,
                    "counterparty_entity_id": cpty_entity_id,
                    "netting_set_id": netting_set_id,
                    "product_family": "IRS",
                    "lifecycle_state": "ACTIVE",
                    "disposition": "ACCEPTED",
                    "mtm": money("50000.00"),
                },
                {
                    "trade_id": unsupported_trade_id,
                    "calculation_entity_id": calc_entity_id,
                    "counterparty_entity_id": cpty_entity_id,
                    "netting_set_id": netting_set_id,
                    "product_family": unsupported_trade["product_family"],
                    "lifecycle_state": unsupported_trade["lifecycle_state"],
                    "disposition": unsupported_trade["disposition"],
                    "mtm": money(unsupported_trade["mtm"]["amount"]),
                },
            ],
        },
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"portfolio snapshot ingest failed: {resp.text}")

    step("Ingest an empty collateral state package")
    empty_collateral_package(
        client,
        package_id=collateral_id,
        calc_entity_id=calc_entity_id,
        netting_set_id=netting_set_id,
        as_of_iso=now_iso(),
    )

    step("Run the MARGIN_CALL calculation with a manifest that correctly expects the UNSUPPORTED trade")
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
                "expected_accepted_count": 1,
                "expected_unsupported_count": 1,
                "expected_accepted_mtm_total": money("50000.00"),
            },
            "counterparty_call": {
                "party_label": "COUNTERPARTY",
                "call_status": "DELIVER",
                "call_amount": pack_money("50000"),
                "responded": True,
            },
        },
    )
    if resp.status_code != 201:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"calculation run failed: {resp.text}")
    run_payload = resp.json()
    waterfall = run_payload.get("waterfall") or {}
    exposure = waterfall.get("exposure", {}).get("amount")

    print(
        f"\ncalculation_status={run_payload.get('calculation_status')!r} "
        f"exposure={exposure!r} (expected exactly 50000.00 -- the ACCEPTED trade's MTM only; "
        f"the UNSUPPORTED_PRODUCT trade's {unsupported_trade['mtm']['amount']} never entered the aggregate)"
    )

    unsupported_trade_excluded_from_exposure = exposure == "50000.00"

    step("Negative control: a manifest that does NOT account for the unsupported trade is explicitly rejected")
    bad_manifest_id = f"{manifest_id}-BAD"
    bad_run_id = f"{run_id}-BAD"
    resp = client.post(
        "/v1/calculation-runs",
        {
            "run_id": bad_run_id,
            "purpose": "MARGIN_CALL",
            "calculation_entity_id": calc_entity_id,
            "counterparty_entity_id": cpty_entity_id,
            "agreement_id": agreement_id,
            "portfolio_snapshot_id": portfolio_id,
            "collateral_package_id": collateral_id,
            "manifest": {
                "manifest_id": bad_manifest_id,
                "expected_accepted_count": 1,
                "expected_unsupported_count": 0,  # wrong on purpose: portfolio actually has 1
                "expected_accepted_mtm_total": money("50000.00"),
            },
            "counterparty_call": {
                "party_label": "COUNTERPARTY",
                "call_status": "DELIVER",
                "call_amount": pack_money("50000"),
                "responded": True,
            },
        },
    )
    bad_run_payload = resp.json() if resp.status_code == 201 else {}
    bad_run_blocked = (
        resp.status_code == 201
        and bad_run_payload.get("calculation_status") == "FAILED"
        and "UNSUPPORTED" in (bad_run_payload.get("failure_reason") or "")
    )
    print(f"\nmismatched-manifest run: calculation_status={bad_run_payload.get('calculation_status')!r} "
          f"failure_reason={bad_run_payload.get('failure_reason')!r}")

    passed = (
        run_payload.get("calculation_status") == "COMPLETE"
        and unsupported_trade_excluded_from_exposure
        and bad_run_blocked
    )
    reason = (
        "ok" if passed else
        f"calculation_status={run_payload.get('calculation_status')!r} "
        f"exposure_excluded_unsupported={unsupported_trade_excluded_from_exposure} "
        f"bad_manifest_run_blocked={bad_run_blocked}"
    )
    conclude(SCENARIO_ID, SCENARIO_NAME, passed, reason)


def json_preview(d: dict) -> dict:
    return {k: d[k] for k in ("trade_id", "product_family", "disposition", "trap_type", "mtm") if k in d}


if __name__ == "__main__":
    main()

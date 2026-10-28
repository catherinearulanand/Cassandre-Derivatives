"""Shared helpers for the `scripts/scenarios/*` runnable simulation scenarios.

These scripts are pure external HTTP clients against the three live FastAPI
services (ClauseOS :8001, RiskOS :8002, CollateralOS :8003) -- no mocking, no
in-process TestClient. Every script:

  * reads real fixture data from `schemas/fixtures/` (trades, inventory,
    market snapshots, agreement bundles) rather than hand-typing values,
  * uses fresh `SIM-*` identifiers per run (a run-scoped suffix derived from
    the current time) so scripts are safely re-runnable without ID
    collisions against the real Postgres-backed services,
  * prints each request/response so a human can follow along, and
  * ends with an explicit `RESULT: PASS` or `RESULT: FAIL -- <why>` line,
    exiting 0 or 1 respectively.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_ROOT = REPO_ROOT / "schemas" / "fixtures"

CLAUSEOS_BASE = "http://127.0.0.1:8001"
RISKOS_BASE = "http://127.0.0.1:8002"
COLLATERALOS_BASE = "http://127.0.0.1:8003"

DEFAULT_TIMEOUT = 30.0


def run_suffix() -> str:
    """A short, time-ordered, collision-resistant suffix for SIM-* ids
    minted fresh on every run (plan constraint: never reuse ids across
    runs)."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%H%M%S")
    return f"{stamp}{uuid.uuid4().hex[:6].upper()}"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------
# Fixture loading (schemas/fixtures/*) -- real corpus data, never hand-typed
# ---------------------------------------------------------------------------


def load_json(relative_path: str) -> Any:
    path = FIXTURES_ROOT / relative_path
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_trades() -> dict:
    return load_json("trades.json")


def load_inventory() -> list:
    return load_json("inventory.json")


def load_market_snapshot_index() -> list:
    return load_json("market_snapshots/index.json")


def load_market_snapshot(snapshot_id: str) -> dict:
    return load_json(f"market_snapshots/{snapshot_id.lower()}.json")


def load_agreements_index() -> list:
    return load_json("agreements/index.json")


def load_agreement_bundle_ground_truth(bundle_id: str) -> dict:
    return load_json(f"agreements/{bundle_id.lower()}/ground_truth.json")


def load_agreement_bundle_document(bundle_id: str, document_name: str) -> str:
    path = FIXTURES_ROOT / "agreements" / bundle_id.lower() / document_name
    return path.read_text(encoding="utf-8")


def find_trade(trade_id: str) -> dict:
    trades = load_trades()["trades"]
    for t in trades:
        if t["trade_id"] == trade_id:
            return t
    raise KeyError(f"no trade {trade_id!r} in trades.json")


def find_netting_set(netting_set_id: str) -> dict:
    for ns in load_trades()["netting_sets"]:
        if ns["netting_set_id"] == netting_set_id:
            return ns
    raise KeyError(f"no netting set {netting_set_id!r} in trades.json")


def find_inventory_position(position_id: str) -> dict:
    for p in load_inventory():
        if p["position_id"] == position_id:
            return p
    raise KeyError(f"no inventory position {position_id!r} in inventory.json")


# ---------------------------------------------------------------------------
# Printing / assertion helpers
# ---------------------------------------------------------------------------


def banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def step(title: str) -> None:
    print(f"\n--- {title} ---")


def show_request(method: str, url: str, body: Any = None) -> None:
    print(f">> {method} {url}")
    if body is not None:
        print(json.dumps(body, indent=2, default=str))


def show_response(resp: httpx.Response) -> None:
    print(f"<< HTTP {resp.status_code}")
    try:
        print(json.dumps(resp.json(), indent=2, default=str))
    except Exception:
        print(resp.text[:2000])


class ScenarioClient:
    """Thin wrapper over `httpx.Client` that prints every call it makes, so
    each scenario script's output is a readable transcript of what was
    submitted and what came back."""

    def __init__(self, base_url: str):
        self._client = httpx.Client(base_url=base_url, timeout=DEFAULT_TIMEOUT)

    def post(self, path: str, json_body: dict | None = None) -> httpx.Response:
        show_request("POST", f"{self._client.base_url}{path}", json_body)
        resp = self._client.post(path, json=json_body or {})
        show_response(resp)
        return resp

    def get(self, path: str, params: dict | None = None) -> httpx.Response:
        show_request("GET", f"{self._client.base_url}{path}" + (f"?{params}" if params else ""))
        resp = self._client.get(path, params=params)
        show_response(resp)
        return resp

    def close(self) -> None:
        self._client.close()


def clauseos_client() -> ScenarioClient:
    return ScenarioClient(CLAUSEOS_BASE)


def riskos_client() -> ScenarioClient:
    return ScenarioClient(RISKOS_BASE)


def collateralos_client() -> ScenarioClient:
    return ScenarioClient(COLLATERALOS_BASE)


def check_service_up(base_url: str, name: str) -> bool:
    try:
        resp = httpx.get(f"{base_url}/docs", timeout=5.0)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


# ---------------------------------------------------------------------------
# RiskOS wire-format helpers
# ---------------------------------------------------------------------------


def money(amount: str, currency: str = "USD", rounding_context: str = "riskos.trade_mtm.source_incumbent.raw") -> dict:
    return {"amount": amount, "currency": currency, "rounding_context": rounding_context}


METHOD_PACK_ROUNDING_CONTEXT = "riskos.method_pack.SIM-CSA-SYMMETRIC-USD-001"
METHOD_PACK_ID = "SIM-CSA-SYMMETRIC-USD-001"


def pack_money(amount: str, currency: str = "USD") -> dict:
    return money(amount, currency, rounding_context=METHOD_PACK_ROUNDING_CONTEXT)


def register_riskos_entities(
    client: ScenarioClient,
    *,
    calc_entity_id: str,
    cpty_entity_id: str,
    netting_set_id: str,
    agreement_id: str,
) -> None:
    """Register a fresh calculation entity, counterparty entity, netting
    set, and SUPPORTED/approved agreement-terms package, all under the one
    implemented METHOD-PACK-001 (`SIM-CSA-SYMMETRIC-USD-001`)."""

    resp = client.post(
        "/v1/legal-entity-versions",
        {
            "entity_id": calc_entity_id,
            "display_name": "SIM Calculation Bank (scenario run)",
            "lei_shaped_id": "SIM-LEI-CALC-RUN",
            "home_currency": "USD",
            "is_active_calculation_entity": True,
            "version": "v1",
            "approved": True,
        },
    )
    assert resp.status_code == 201, f"legal entity (calc) registration failed: {resp.text}"

    resp = client.post(
        "/v1/legal-entity-versions",
        {
            "entity_id": cpty_entity_id,
            "display_name": "SIM Counterparty Bank (scenario run)",
            "lei_shaped_id": "SIM-LEI-CPTY-RUN",
            "home_currency": "USD",
            "is_active_calculation_entity": False,
            "version": "v1",
            "approved": True,
        },
    )
    assert resp.status_code == 201, f"legal entity (cpty) registration failed: {resp.text}"

    resp = client.post(
        "/v1/netting-set-versions",
        {
            "netting_set_id": netting_set_id,
            "calculation_entity_id": calc_entity_id,
            "counterparty_entity_id": cpty_entity_id,
            "governing_agreement_id": agreement_id,
            "version": "v1",
            "effective_from": "2026-01-01",
            "approved": True,
        },
    )
    assert resp.status_code == 201, f"netting set registration failed: {resp.text}"

    resp = client.post(
        "/v1/agreement-terms:approve",
        {
            "agreement_id": agreement_id,
            "netting_set_id": netting_set_id,
            "method_pack_id": METHOD_PACK_ID,
            "call_currency": "USD",
            "is_supported": True,
        },
    )
    assert resp.status_code == 201, f"agreement terms approval failed: {resp.text}"


def empty_collateral_package(
    client: ScenarioClient,
    *,
    package_id: str,
    calc_entity_id: str,
    netting_set_id: str,
    as_of_iso: str,
) -> None:
    resp = client.post(
        "/v1/collateral-state-packages",
        {
            "package_id": package_id,
            "calculation_entity_id": calc_entity_id,
            "netting_set_id": netting_set_id,
            "as_of": as_of_iso,
            "source_watermark": "SIM-WATERMARK-SCENARIO-RUN",
            "collateral_held": [],
            "collateral_posted": [],
            "disputed_amount": pack_money("0"),
            "valuation_source": "SIM-VAL-SRC-SCENARIO-RUN",
            "fx_source": "SIM-FX-SRC-SCENARIO-RUN",
            "custodian_reconciliation_status": "MATCHED",
            "sign_convention": "positive_amount_held_by_calculation_party",
        },
    )
    assert resp.status_code == 201, f"collateral state package ingest failed: {resp.text}"


# ---------------------------------------------------------------------------
# CollateralOS wire-format helpers
# ---------------------------------------------------------------------------


def col_money(amount: str, currency: str = "USD") -> dict:
    return {"amount": amount, "currency": currency}


def col_quantity(amount: str, unit: str) -> dict:
    return {"amount": amount, "unit": unit}


def activate_fresh_policy(client: ScenarioClient) -> str:
    """CollateralOS's one provisional policy bundle, `SIM-POLICY-001`
    (DEC-COL-01), validated then activated. The store builds this bundle
    fresh from `docs/controls/wrong-way-map-001.json` on every
    `:validate` call -- it is not scenario-specific, so every scenario
    script calls this the same way."""
    resp = client.post("/v1/policy-bundles:validate", {})
    assert resp.status_code == 201, f"policy validate failed: {resp.text}"
    policy_id = resp.json()["policy_id"]
    resp = client.post(f"/v1/policy-bundles/{policy_id}:activate", {})
    assert resp.status_code == 200, f"policy activate failed: {resp.text}"
    assert resp.json()["status"] == "ACTIVE"
    return policy_id


def position_wire_from_corpus(
    position: dict,
    *,
    quantity_field: str = "settled_quantity",
    fresh_as_of: dt.datetime | None = None,
    issuer_group_id_override: str | None = "__UNSET__",
) -> dict:
    """Translate one `schemas/fixtures/inventory.json` position record into
    CollateralOS's `PositionIn` wire format. Corpus field names mostly line
    up 1:1; a few need renaming/flattening (nested `price.as_of` ->
    `price_as_of`, `ENCUMBERED` trap -> `is_encumbered`).

    The corpus was generated around a simulated `2026-06-01` clock, so every
    position's `stale_after` is already in the past relative to this
    machine's real wall clock (these live services use real `datetime.now()`,
    not an injectable clock). For scenarios that need a position to be
    genuinely FRESH under the real clock, pass `fresh_as_of` (typically
    "now") and this function re-bases `price_as_of`/`stale_after` relative to
    it, synthesizing only the two timestamp fields the corpus cannot supply
    for a real-clock run -- every other field (market_value, asset_class,
    issuer_group_id, traps-derived booleans, ...) stays exactly as generated.
    For a scenario that WANTS real staleness (SIM-SCENARIO-008), omit
    `fresh_as_of` and the corpus's own (already-past) `stale_after` is used
    unmodified.
    """
    trap_types = position.get("trap_types", [])
    if fresh_as_of is not None:
        price_as_of = (fresh_as_of - dt.timedelta(days=1)).isoformat()
        stale_after = (fresh_as_of + dt.timedelta(days=30)).isoformat()
    else:
        price_as_of = position["price"]["as_of"]
        stale_after = position["stale_after"]
    issuer_group_id = (
        position.get("issuer_group_id") if issuer_group_id_override == "__UNSET__" else issuer_group_id_override
    )
    return {
        "position_id": position["position_id"],
        "owner_entity_id": position["owner_entity_id"],
        "account_id": position["account_id"],
        "location": position["location"],
        "asset_class": position["asset_class"],
        "issuer_group_id": issuer_group_id,
        "currency": position["currency"],
        "market_value": col_money(position["market_value"]["amount"], position["market_value"]["currency"]),
        "quantity": col_quantity(position[quantity_field]["amount"], position[quantity_field]["unit"]),
        "price_as_of": price_as_of,
        "stale_after": stale_after,
        "is_encumbered": "ENCUMBERED" in trap_types or Decimal(position["encumbered_quantity"]["amount"]) > 0,
        "is_recalled": bool(position.get("is_recalled", False)),
        "is_self_issued": bool(position.get("is_self_issued", False)),
        "is_affiliate_issued": bool(position.get("is_affiliate_issued", False)),
    }


# ---------------------------------------------------------------------------
# Scenario result plumbing (for run_all.py's summary table)
# ---------------------------------------------------------------------------


class ScenarioResult:
    def __init__(self, scenario_id: str, name: str, passed: bool, reason: str):
        self.scenario_id = scenario_id
        self.name = name
        self.passed = passed
        self.reason = reason


def conclude(scenario_id: str, name: str, passed: bool, reason: str) -> None:
    banner(f"RESULT: {'PASS' if passed else 'FAIL -- ' + reason}")
    print(f"scenario_id={scenario_id} name={name!r}")
    sys.exit(0 if passed else 1)

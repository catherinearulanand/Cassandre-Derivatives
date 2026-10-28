"""API-level tests for the RiskOS FastAPI app (`apps/riskos_api`).

`apps/riskos_api/src/riskos_api/dependencies.py` wires one process-wide
in-memory repository/service singleton (there is no `create_app()` factory
in this app, unlike CollateralOS/ClauseOS's api apps), so every test below
uses its own unique `SIM-*` ids to avoid cross-test collisions in the shared
store, rather than relying on a fresh app per test.

These exercise the wire-format request/response contract end to end
(including the RSK-X01 native valuation challenger's new endpoints and the
`apps/riskos_web` static UI mount); the underlying business rules already
have dedicated unit/integration coverage under `products/riskos/tests/`.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from riskos_api.main import app

client = TestClient(app)

# `apps/riskos_api/src/riskos_api/main.py` stamps every ingested aggregate's
# audit event with the real wall-clock time (`_now()`), and
# `riskos.core.gates.run_freshness_gate` compares `portfolio.as_of` /
# `collateral.as_of` against that same real "now" within a 24h window
# (`FRESHNESS_WINDOW_HOURS`, DEC-OPS-03) -- unlike the pure-function
# `products/riskos/tests` suite, which injects a fixed clock throughout, the
# HTTP layer has no injectable clock. So every "as_of" in this file uses the
# actual current time, not a fixed historical date.
NOW = dt.datetime.now(dt.timezone.utc)


def _money(amount: str, currency: str = "USD", rounding_context: str = "test.rounding") -> dict:
    return {"amount": amount, "currency": currency, "rounding_context": rounding_context}


def _iso(value: dt.datetime) -> str:
    return value.isoformat()


# ---------------------------------------------------------------------------
# Static UI mount (apps/riskos_web, plan Section 11.9)
# ---------------------------------------------------------------------------


def test_static_ui_is_mounted_and_serves_index():
    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "RiskOS" in resp.text
    assert "Challenger comparison" in resp.text  # tab label, proves the challenger page shipped


def test_static_ui_serves_its_own_js_and_css():
    js_resp = client.get("/ui/app.js")
    assert js_resp.status_code == 200
    css_resp = client.get("/ui/style.css")
    assert css_resp.status_code == 200


def test_ui_fetch_targets_match_real_endpoint_paths():
    """The brief asks to verify the UI's fetch targets match real endpoint
    paths. Cross-check every `/v1/...` literal referenced in app.js against
    the app's actual registered routes."""

    import re

    js_text = client.get("/ui/app.js").text
    referenced_paths = set(re.findall(r'"(/v1/[a-zA-Z0-9_\-:/${}]*)"', js_text))
    assert referenced_paths, "expected at least one /v1/... path literal in app.js"

    registered = {route.path for route in app.routes}
    for path in referenced_paths:
        # Template-substituted ids (`${...}`) map onto a path-parameter route.
        templated = re.sub(r"\$\{[^}]*\}", "{x}", path)
        matches_any = any(
            re.fullmatch(re.sub(r"\{[^}]*\}", "{x}", r), templated) for r in registered
        )
        assert matches_any, f"app.js references {path!r} which has no matching registered route"


# ---------------------------------------------------------------------------
# MARGIN_CALL workflow through the HTTP API, plus the new read endpoints
# ---------------------------------------------------------------------------


def _ingest_golden_scenario(suffix: str) -> dict:
    calc_entity_id = f"SIM-ENTITY-CALC-API-{suffix}"
    cpty_entity_id = f"SIM-ENTITY-CPTY-API-{suffix}"
    netting_set_id = f"SIM-NETSET-API-{suffix}"
    agreement_id = f"SIM-AGREEMENT-API-{suffix}"
    portfolio_id = f"SIM-PORTFOLIO-API-{suffix}"
    collateral_id = f"SIM-COLLATERAL-API-{suffix}"

    resp = client.post(
        "/v1/legal-entity-versions",
        json={
            "entity_id": calc_entity_id,
            "display_name": "SIM Calc Bank",
            "lei_shaped_id": "SIM-LEI-CALC",
            "home_currency": "USD",
            "is_active_calculation_entity": True,
            "version": "v1",
            "approved": True,
        },
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        "/v1/legal-entity-versions",
        json={
            "entity_id": cpty_entity_id,
            "display_name": "SIM Cpty Bank",
            "lei_shaped_id": "SIM-LEI-CPTY",
            "home_currency": "USD",
            "is_active_calculation_entity": False,
            "version": "v1",
            "approved": True,
        },
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        "/v1/netting-set-versions",
        json={
            "netting_set_id": netting_set_id,
            "calculation_entity_id": calc_entity_id,
            "counterparty_entity_id": cpty_entity_id,
            "governing_agreement_id": agreement_id,
            "version": "v1",
            "effective_from": "2026-01-01",
            "approved": True,
        },
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        "/v1/agreement-terms:approve",
        json={
            "agreement_id": agreement_id,
            "netting_set_id": netting_set_id,
            "method_pack_id": "SIM-CSA-SYMMETRIC-USD-001",
            "call_currency": "USD",
            "is_supported": True,
        },
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        "/v1/portfolio-snapshots",
        json={
            "snapshot_id": portfolio_id,
            "as_of": _iso(NOW),
            "trades": [
                {
                    "trade_id": f"SIM-TRADE-API-{suffix}-0001",
                    "calculation_entity_id": calc_entity_id,
                    "counterparty_entity_id": cpty_entity_id,
                    "netting_set_id": netting_set_id,
                    "product_family": "IRS",
                    "lifecycle_state": "ACTIVE",
                    "disposition": "ACCEPTED",
                    "mtm": _money("100000.00", rounding_context="riskos.trade_mtm.source_incumbent.raw"),
                }
            ],
        },
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        "/v1/collateral-state-packages",
        json={
            "package_id": collateral_id,
            "calculation_entity_id": calc_entity_id,
            "netting_set_id": netting_set_id,
            "as_of": _iso(NOW),
            "source_watermark": "SIM-WATERMARK",
            "collateral_held": [],
            "collateral_posted": [],
            "disputed_amount": _money("0"),
            "valuation_source": "SIM-VAL-SRC",
            "fx_source": "SIM-FX-SRC",
            "custodian_reconciliation_status": "MATCHED",
            "sign_convention": "positive_amount_held_by_calculation_party",
        },
    )
    assert resp.status_code == 201, resp.text

    return {
        "calc_entity_id": calc_entity_id,
        "cpty_entity_id": cpty_entity_id,
        "netting_set_id": netting_set_id,
        "agreement_id": agreement_id,
        "portfolio_id": portfolio_id,
        "collateral_id": collateral_id,
    }


def test_full_margin_call_workflow_via_http_and_new_read_endpoints():
    ids = _ingest_golden_scenario("HTTP0001")
    run_id = "SIM-RUN-API-HTTP0001"

    resp = client.post(
        "/v1/calculation-runs",
        json={
            "run_id": run_id,
            "purpose": "MARGIN_CALL",
            "calculation_entity_id": ids["calc_entity_id"],
            "counterparty_entity_id": ids["cpty_entity_id"],
            "agreement_id": ids["agreement_id"],
            "portfolio_snapshot_id": ids["portfolio_id"],
            "collateral_package_id": ids["collateral_id"],
            "manifest": {
                "manifest_id": "SIM-MANIFEST-HTTP0001",
                "expected_accepted_count": 1,
                "expected_accepted_mtm_total": _money(
                    "100000.00", rounding_context="riskos.trade_mtm.source_incumbent.raw"
                ),
            },
            "counterparty_call": {
                "party_label": "COUNTERPARTY",
                "call_status": "DELIVER",
                "call_amount": _money("100000.00", rounding_context="riskos.method_pack.SIM-CSA-SYMMETRIC-USD-001"),
                "exposure": _money("100000.00", rounding_context="riskos.method_pack.SIM-CSA-SYMMETRIC-USD-001"),
                "credit_support_balance": _money("0", rounding_context="riskos.method_pack.SIM-CSA-SYMMETRIC-USD-001"),
                "trade_population_count": 1,
                "as_of": _iso(NOW),
                "responded": True,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    run_payload = resp.json()
    assert run_payload["calculation_status"] == "COMPLETE"
    assert run_payload["reconciliation_status"] == "MATCHED"
    # All five status dimensions are present on the wire (plan 11.7).
    for key in (
        "calculation_status",
        "data_quality_status",
        "reconciliation_status",
        "validation_status",
        "actionability",
    ):
        assert key in run_payload

    # GET run + waterfall
    resp = client.get(f"/v1/calculation-runs/{run_id}")
    assert resp.status_code == 200
    resp = client.get(f"/v1/calculation-runs/{run_id}/waterfall")
    assert resp.status_code == 200
    assert resp.json()["call_status"] == "DELIVER"

    # New read endpoints this session added
    resp = client.get(f"/v1/legal-entity-versions/{ids['calc_entity_id']}")
    assert resp.status_code == 200
    assert resp.json()["entity_id"] == ids["calc_entity_id"]

    resp = client.get("/v1/legal-entity-versions")
    assert resp.status_code == 200
    assert any(e["entity_id"] == ids["calc_entity_id"] for e in resp.json()["legal_entities"])

    resp = client.get("/v1/netting-set-versions")
    assert resp.status_code == 200
    assert any(n["netting_set_id"] == ids["netting_set_id"] for n in resp.json()["netting_sets"])

    resp = client.get(f"/v1/agreement-terms/{ids['agreement_id']}")
    assert resp.status_code == 200
    assert resp.json()["agreement_id"] == ids["agreement_id"]

    resp = client.get(f"/v1/portfolio-snapshots/{ids['portfolio_id']}")
    assert resp.status_code == 200
    assert len(resp.json()["trades"]) == 1

    resp = client.get(f"/v1/collateral-state-packages/{ids['collateral_id']}")
    assert resp.status_code == 200

    resp = client.get(f"/v1/reconciliation-cases/{run_payload['reconciliation_case_id']}")
    assert resp.status_code == 200
    assert resp.json()["reconciliation_status"] == "MATCHED"


def test_challenger_purpose_is_still_rejected_by_the_margin_call_endpoint():
    """Unchanged existing behavior: `POST /v1/calculation-runs` with
    purpose=CHALLENGER is rejected by `execute_margin_call`'s own purpose
    gate -- this session's challenger work added a *separate*
    `/v1/challenger-runs` endpoint rather than changing this one."""

    ids = _ingest_golden_scenario("HTTP0002")
    run_id = "SIM-RUN-API-HTTP0002"
    resp = client.post(
        "/v1/calculation-runs",
        json={
            "run_id": run_id,
            "purpose": "CHALLENGER",
            "calculation_entity_id": ids["calc_entity_id"],
            "counterparty_entity_id": ids["cpty_entity_id"],
            "agreement_id": ids["agreement_id"],
            "portfolio_snapshot_id": ids["portfolio_id"],
            "collateral_package_id": ids["collateral_id"],
            "manifest": {
                "manifest_id": "SIM-MANIFEST-HTTP0002",
                "expected_accepted_count": 1,
                "expected_accepted_mtm_total": _money(
                    "100000.00", rounding_context="riskos.trade_mtm.source_incumbent.raw"
                ),
            },
            "counterparty_call": {
                "party_label": "COUNTERPARTY",
                "call_status": "DELIVER",
                "call_amount": _money("100000.00"),
                "responded": True,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["calculation_status"] == "FAILED"
    assert "unsupported calculation purpose" in resp.json()["failure_reason"]


# ---------------------------------------------------------------------------
# RSK-X01 native valuation challenger over HTTP (market snapshot + run)
# ---------------------------------------------------------------------------


def test_challenger_workflow_via_http_reproduces_the_irs_golden_case():
    resp = client.post(
        "/v1/market-snapshots",
        json={
            "snapshot_id": "SIM-MKT-API-0001",
            "as_of": "2026-01-01",
            "calendars": ["SIM.NONE"],
            "discount_curves": [{"currency": "USD", "points": [{"tenor_years": "1", "zero_rate": "0.03"}]}],
            "fx_spot_rates": [],
        },
    )
    assert resp.status_code == 201, resp.text

    resp = client.get("/v1/market-snapshots/SIM-MKT-API-0001")
    assert resp.status_code == 200
    assert resp.json()["discount_curves"][0]["currency"] == "USD"

    resp = client.post(
        "/v1/challenger-runs",
        json={
            "run_id": "SIM-CHALLENGER-RUN-API-0001",
            "netting_set_id": "SIM-NETSET-CHALLENGER-API-0001",
            "market_snapshot_id": "SIM-MKT-API-0001",
            "as_of": "2026-01-01",
            "irs_trades": [
                {
                    "trade_id": "SIM-CHALLENGER-TRADE-API-0001",
                    "calculation_entity_id": "SIM-ENTITY-CALC-API-0001",
                    "counterparty_entity_id": "SIM-ENTITY-CPTY-API-0001",
                    "netting_set_id": "SIM-NETSET-CHALLENGER-API-0001",
                    "currency": "USD",
                    "notional": "1000000",
                    "fixed_rate": "0.04",
                    "calculation_party_pays_fixed": False,
                    "effective_date": "2026-01-01",
                    "maturity_date": "2028-01-01",
                    "payment_frequency_months": 12,
                }
            ],
            "fx_forward_trades": [],
        },
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["calculation_status"] == "COMPLETE"
    assert payload["actionability"] == "INDICATIVE_ONLY"
    comparison = payload["comparisons"][0]
    assert comparison["challenger_pv"]["amount"] == "19967.03"
    assert comparison["challenger_pv"]["currency"] == "USD"

    resp = client.get("/v1/challenger-runs/SIM-CHALLENGER-RUN-API-0001")
    assert resp.status_code == 200
    assert resp.json()["comparisons"][0]["challenger_pv"]["amount"] == "19967.03"


def test_challenger_run_does_not_affect_an_existing_margin_call_run_via_http():
    ids = _ingest_golden_scenario("HTTP0003")
    run_id = "SIM-RUN-API-HTTP0003"
    resp = client.post(
        "/v1/calculation-runs",
        json={
            "run_id": run_id,
            "purpose": "MARGIN_CALL",
            "calculation_entity_id": ids["calc_entity_id"],
            "counterparty_entity_id": ids["cpty_entity_id"],
            "agreement_id": ids["agreement_id"],
            "portfolio_snapshot_id": ids["portfolio_id"],
            "collateral_package_id": ids["collateral_id"],
            "manifest": {
                "manifest_id": "SIM-MANIFEST-HTTP0003",
                "expected_accepted_count": 1,
                "expected_accepted_mtm_total": _money(
                    "100000.00", rounding_context="riskos.trade_mtm.source_incumbent.raw"
                ),
            },
            "counterparty_call": {
                "party_label": "COUNTERPARTY",
                "call_status": "DELIVER",
                "call_amount": _money("100000.00", rounding_context="riskos.method_pack.SIM-CSA-SYMMETRIC-USD-001"),
                "responded": True,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    before = client.get(f"/v1/calculation-runs/{run_id}").json()

    resp = client.post(
        "/v1/market-snapshots",
        json={
            "snapshot_id": "SIM-MKT-API-0003",
            "as_of": "2026-01-01",
            "discount_curves": [{"currency": "USD", "points": [{"tenor_years": "1", "zero_rate": "0.03"}]}],
        },
    )
    assert resp.status_code == 201, resp.text
    resp = client.post(
        "/v1/challenger-runs",
        json={
            "run_id": "SIM-CHALLENGER-RUN-API-0003",
            "netting_set_id": ids["netting_set_id"],
            "market_snapshot_id": "SIM-MKT-API-0003",
            "as_of": "2026-01-01",
            "portfolio_snapshot_id": ids["portfolio_id"],
            "irs_trades": [],
            "fx_forward_trades": [],
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["calculation_status"] == "FAILED"  # no supported trades supplied; still its own status

    after = client.get(f"/v1/calculation-runs/{run_id}").json()
    assert after == before

    # A CHALLENGER run_id is not fetchable as a MARGIN_CALL CalculationRun.
    resp = client.get("/v1/calculation-runs/SIM-CHALLENGER-RUN-API-0003")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# SEC-04 authorization: a DENY from `cassandre_security.authorization`
# genuinely maps to HTTP 403, not a silently-allowed mutation. The
# process-wide `get_service` singleton (see `riskos_api/dependencies.py`)
# always uses a policy engine where the fixed default actor is pre-granted
# every gated role (see `riskos.application.security`), so this test
# swaps in a `RiskOSService` built with an empty grant resolver via
# FastAPI's `dependency_overrides` -- proving the route-level exception
# mapping actually works, not just the service-level `.decide(...)` call.
# ---------------------------------------------------------------------------


def test_register_legal_entity_denied_by_authorization_maps_to_http_403():
    from audit_protocol.keys import generate_keypair

    from riskos.adapters.in_memory import InMemoryRiskOSRepository
    from riskos.application.security import StaticGrantResolver, StaticRevocationChecker, build_default_policy_engine
    from riskos.application.service import RiskOSService
    from riskos_api.dependencies import get_service

    no_grants_engine = build_default_policy_engine(
        grant_resolver=StaticGrantResolver(), revocation_checker=StaticRevocationChecker()
    )
    restricted_service = RiskOSService(
        InMemoryRiskOSRepository(),
        keypair=generate_keypair("riskos-api-403-test-key"),
        policy_engine=no_grants_engine,
    )

    app.dependency_overrides[get_service] = lambda: restricted_service
    try:
        resp = client.post(
            "/v1/legal-entity-versions",
            json={
                "entity_id": "SIM-ENTITY-DENY-0001",
                "display_name": "SIM Denied Bank",
                "lei_shaped_id": "SIM-LEI-DENY",
                "home_currency": "USD",
                "is_active_calculation_entity": True,
                "version": "v1",
                "approved": True,
            },
        )
        assert resp.status_code == 403, resp.text
    finally:
        del app.dependency_overrides[get_service]

    # Confirm the deny genuinely blocked persistence: the default (allowing)
    # service singleton never saw this entity either.
    resp = client.get("/v1/legal-entity-versions/SIM-ENTITY-DENY-0001")
    assert resp.status_code == 404

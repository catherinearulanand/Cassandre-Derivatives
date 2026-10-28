import json

import pytest
from fastapi.testclient import TestClient

from cassandre_contracts import ProducerProduct

from cassandre_control_api.main import app, get_correlation_store, get_trust_registry
from cassandre_control_api.store import CorrelationStore, TrustRegistry


@pytest.fixture
def client():
    fresh_registry = TrustRegistry()
    fresh_store = CorrelationStore()
    app.dependency_overrides[get_trust_registry] = lambda: fresh_registry
    app.dependency_overrides[get_correlation_store] = lambda: fresh_store
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _envelope_json(envelope) -> dict:
    return json.loads(envelope.model_dump_json())


def test_submit_unknown_key_rejected(client, signed_envelope_factory):
    env, _kp = signed_envelope_factory(ProducerProduct.CLAUSEOS, "SIM-SCEN-API-1", "obj-1", "k1")
    resp = client.post("/v1/envelopes:submit", json=_envelope_json(env))
    assert resp.status_code == 422
    assert "UNKNOWN_OR_REVOKED_KEY_ID" in resp.json()["detail"]["failures"]


def test_register_key_then_submit_succeeds(client, signed_envelope_factory):
    env, kp = signed_envelope_factory(ProducerProduct.CLAUSEOS, "SIM-SCEN-API-2", "obj-2", "k2")
    reg = client.post("/v1/trust-keys", json={"key_id": kp.key_id, "public_key_b64": kp.public_key_b64})
    assert reg.status_code == 201

    resp = client.post("/v1/envelopes:submit", json=_envelope_json(env))
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    assert body["correlation_key"] == "SIM-SCEN-API-2"


def test_revoked_key_rejected(client, signed_envelope_factory):
    env, kp = signed_envelope_factory(ProducerProduct.CLAUSEOS, "SIM-SCEN-API-3", "obj-3", "k3")
    client.post("/v1/trust-keys", json={"key_id": kp.key_id, "public_key_b64": kp.public_key_b64})
    client.post(f"/v1/trust-keys/{kp.key_id}:revoke")

    resp = client.post("/v1/envelopes:submit", json=_envelope_json(env))
    assert resp.status_code == 422
    assert "UNKNOWN_OR_REVOKED_KEY_ID" in resp.json()["detail"]["failures"]


def test_correlated_case_view_and_federated_export(client, signed_envelope_factory):
    scenario = "SIM-SCEN-API-4"
    for product, key_id in [
        (ProducerProduct.CLAUSEOS, "k4a"),
        (ProducerProduct.RISKOS, "k4b"),
        (ProducerProduct.COLLATERALOS, "k4c"),
    ]:
        env, kp = signed_envelope_factory(product, scenario, f"obj-{product.value}", key_id)
        client.post("/v1/trust-keys", json={"key_id": kp.key_id, "public_key_b64": kp.public_key_b64})
        submit = client.post("/v1/envelopes:submit", json=_envelope_json(env))
        assert submit.status_code == 200

    case_resp = client.get(f"/v1/correlated-cases/{scenario}")
    assert case_resp.status_code == 200
    case_body = case_resp.json()
    assert case_body["is_cross_product_complete"] is True
    assert set(case_body["products_present"]) == {"clauseos", "riskos", "collateralos"}

    export_resp = client.get(f"/v1/correlated-cases/{scenario}/federated-audit-export")
    assert export_resp.status_code == 200
    export_body = export_resp.json()
    assert len(export_body["receipts"]) == 3


def test_unknown_correlation_key_404(client):
    resp = client.get("/v1/correlated-cases/does-not-exist")
    assert resp.status_code == 404

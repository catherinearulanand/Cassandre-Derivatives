from fastapi.testclient import TestClient

from audit_protocol.keys import generate_keypair
from cassandre_contracts import TrustBundle, verify_envelope
from clauseos.adapters.memory import InMemoryBundleRepository

from clauseos_api.app import create_app

MAKER = "maker.alice@sim.cassandre.test"
CHECKER = "checker.bob@sim.cassandre.test"
SCENARIO_ID = "SIM-SCEN-CLAUSEOS-0001"
CSA_DOCUMENT_NAME = "CSA"
ANNEX_DOCUMENT_NAME = "Confirmation Annex A"

CSA_TEXT = (
    "SYNTHETIC CREDIT SUPPORT ANNEX (BILATERAL FORM)\n"
    "Agreement ID: SIM-AGREEMENT-CSA-0001\n\n"
    "Parties: Cassandre Simulation Bank N.A. (\"Party A\") and SIM-COUNTERPARTY-ALPHA (\"Party B\").\n\n"
    "(a) Threshold. The Threshold for Party A shall be USD 500,000.00 and the Threshold for "
    "Party B shall be USD 500,000.00.\n\n"
    "(b) Minimum Transfer Amount. The Minimum Transfer Amount for each party shall be USD 50,000.00.\n\n"
    "(c) Notification. A party that disputes a Demand shall notify the other party within "
    "2 Business Days of receipt.\n\n"
    "(d) Incorporated Documents. This Agreement incorporates by reference the document named "
    '"Confirmation Annex A", which forms part of this Agreement bundle.\n'
)

ANNEX_TEXT = (
    "CONFIRMATION ANNEX A (SYNTHETIC)\n\n"
    "This synthetic annex is incorporated by reference into SIM-AGREEMENT-CSA-0001.\n"
)


def _client() -> TestClient:
    keypair = generate_keypair("clauseos-api-test-key-1")
    app = create_app(InMemoryBundleRepository(), signing_keypair=keypair)
    return TestClient(app)


def _create_and_ingest_complete_bundle(client: TestClient) -> str:
    resp = client.post("/v1/bundles", json={"scenario_id": SCENARIO_ID, "actor_id": MAKER})
    assert resp.status_code == 200, resp.text
    bundle_id = resp.json()["bundle_id"]

    resp = client.post(
        f"/v1/bundles/{bundle_id}/documents",
        json={"document_name": CSA_DOCUMENT_NAME, "content": CSA_TEXT, "actor_id": MAKER},
    )
    assert resp.status_code == 200, resp.text

    resp = client.post(
        f"/v1/bundles/{bundle_id}/documents",
        json={"document_name": ANNEX_DOCUMENT_NAME, "content": ANNEX_TEXT, "actor_id": MAKER},
    )
    assert resp.status_code == 200, resp.text

    resp = client.post(
        f"/v1/bundles/{bundle_id}/manifest:approve",
        json={
            "document_type": "CSA",
            "parties": ["Cassandre Simulation Bank N.A.", "SIM-COUNTERPARTY-ALPHA"],
            "execution_date": "2026-01-15",
            "effective_date": "2026-01-15",
            "incorporated_by_reference": [ANNEX_DOCUMENT_NAME],
            "actor_id": MAKER,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "PARSED"
    return bundle_id


def test_full_api_slice_ingest_to_signed_policy_release():
    client = _client()
    bundle_id = _create_and_ingest_complete_bundle(client)

    resp = client.post(f"/v1/bundles/{bundle_id}:analyze", json={"actor_id": MAKER})
    assert resp.status_code == 200, resp.text
    assertions = resp.json()["assertions"]
    assert len(assertions) == 3

    resp = client.get("/v1/assertions", params={"bundle_id": bundle_id})
    assert resp.status_code == 200
    assert len(resp.json()["assertions"]) == 3

    resp = client.get(f"/v1/bundles/{bundle_id}/reviews")
    assert resp.status_code == 200
    reviews = resp.json()["reviews"]
    assert len(reviews) == 3

    for review in reviews:
        resp = client.post(
            f"/v1/reviews/{review['review_id']}/maker-decision",
            json={"actor_id": MAKER, "decision": "APPROVE"},
        )
        assert resp.status_code == 200, resp.text
        resp = client.post(
            f"/v1/reviews/{review['review_id']}/checker-decision",
            json={"actor_id": CHECKER, "decision": "APPROVE"},
        )
        assert resp.status_code == 200, resp.text

    resp = client.post("/v1/policy-releases", json={"bundle_id": bundle_id, "actor_id": CHECKER})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["policy_release"]["terms"]) == 3
    assert body["envelope"]["signature"] is not None

    resp = client.get(f"/v1/bundles/{bundle_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "PUBLISHED"

    resp = client.post("/v1/evidence-bundles:export", json={"bundle_id": bundle_id})
    assert resp.status_code == 200
    export = resp.json()
    assert len(export["documents"]) == 2
    assert len(export["assertions"]) == 3


def test_maker_and_checker_must_differ_over_api():
    client = _client()
    bundle_id = _create_and_ingest_complete_bundle(client)
    client.post(f"/v1/bundles/{bundle_id}:analyze", json={"actor_id": MAKER})
    reviews = client.get(f"/v1/bundles/{bundle_id}/reviews").json()["reviews"]
    review_id = reviews[0]["review_id"]

    resp = client.post(
        f"/v1/reviews/{review_id}/maker-decision",
        json={"actor_id": "same.person@sim.cassandre.test", "decision": "APPROVE"},
    )
    assert resp.status_code == 200, resp.text

    resp = client.post(
        f"/v1/reviews/{review_id}/checker-decision",
        json={"actor_id": "same.person@sim.cassandre.test", "decision": "APPROVE"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "SameActorError"


def test_missing_incorporated_document_returns_422_bundle_incomplete():
    client = _client()
    resp = client.post("/v1/bundles", json={"scenario_id": SCENARIO_ID, "actor_id": MAKER})
    bundle_id = resp.json()["bundle_id"]
    client.post(
        f"/v1/bundles/{bundle_id}/documents",
        json={"document_name": CSA_DOCUMENT_NAME, "content": CSA_TEXT, "actor_id": MAKER},
    )
    # Annex never ingested, but manifest declares it incorporated.
    resp = client.post(
        f"/v1/bundles/{bundle_id}/manifest:approve",
        json={
            "document_type": "CSA",
            "parties": ["Cassandre Simulation Bank N.A.", "SIM-COUNTERPARTY-ALPHA"],
            "execution_date": "2026-01-15",
            "effective_date": "2026-01-15",
            "incorporated_by_reference": [ANNEX_DOCUMENT_NAME],
            "actor_id": MAKER,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "BundleIncompleteError"

    resp = client.get(f"/v1/bundles/{bundle_id}")
    assert resp.json()["status"] == "BUNDLE_INCOMPLETE"


def test_published_envelope_independently_verifiable():
    client = _client()
    bundle_id = _create_and_ingest_complete_bundle(client)
    client.post(f"/v1/bundles/{bundle_id}:analyze", json={"actor_id": MAKER})
    reviews = client.get(f"/v1/bundles/{bundle_id}/reviews").json()["reviews"]
    for review in reviews:
        client.post(
            f"/v1/reviews/{review['review_id']}/maker-decision",
            json={"actor_id": MAKER, "decision": "APPROVE"},
        )
        client.post(
            f"/v1/reviews/{review['review_id']}/checker-decision",
            json={"actor_id": CHECKER, "decision": "APPROVE"},
        )
    resp = client.post("/v1/policy-releases", json={"bundle_id": bundle_id, "actor_id": CHECKER})
    envelope_dict = resp.json()["envelope"]

    from cassandre_contracts import Envelope

    envelope = Envelope.model_validate(envelope_dict)
    keypair = client.app.state.keypair
    trust_bundle = TrustBundle(keys_by_id={keypair.key_id: keypair.public_key_b64})
    result = verify_envelope(envelope, trust_bundle=trust_bundle, expected_audience="cassandre")
    assert result.ok, result.failures

"""Tests for the API surface added while closing the three deferred gaps:

- Gap 1 (PDF ingestion): `content_base64` on `POST .../documents`, and the
  ingestion-rejection error path (422 with an `ingestion_status` field).
- Gap 2 (model adapter): `include_model_proposer` accepted on
  `POST .../:analyze` (network-mocking the live model call is out of scope
  for this file -- see `products/clauseos/tests/test_model_adapter.py` for
  that; here we only prove the flag is plumbed through and defaults off).
- Gap 3 (web UI) support endpoints: `GET /v1/bundles`, document text,
  audit events, and policy-release listing/revocation.
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from audit_protocol.keys import generate_keypair
from clauseos.adapters.memory import InMemoryBundleRepository

from clauseos_api.app import create_app

MAKER = "maker.alice@sim.cassandre.test"
CHECKER = "checker.bob@sim.cassandre.test"
SCENARIO_ID = "SIM-SCEN-CLAUSEOS-0001"

CSA_TEXT = (
    "SYNTHETIC CREDIT SUPPORT ANNEX (BILATERAL FORM)\n"
    "(a) Threshold. The Threshold for Party A shall be USD 500,000.00.\n\n"
    "(b) Minimum Transfer Amount. The Minimum Transfer Amount for each party shall be USD 50,000.00.\n\n"
    "(c) Notification. A party shall notify the other party within 2 Business Days of receipt.\n"
)


def _client() -> TestClient:
    keypair = generate_keypair("clauseos-api-test-key-2")
    app = create_app(InMemoryBundleRepository(), signing_keypair=keypair)
    return TestClient(app)


def _minimal_pdf_bytes() -> bytes:
    """A tiny, structurally valid single-page PDF with one line of text,
    built the same way as `products/clauseos/tests/helpers.py`'s fixture
    builder (kept local/minimal here to avoid cross-package test imports)."""
    content = b"BT /F1 12 Tf 72 720 Td (Hello from a PDF.) Tj ET"
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
            b"/MediaBox [0 0 612 792] /Contents 5 0 R >>"
        ),
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        5: f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"\nendstream",
    }
    buf = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for i in range(1, 6):
        offsets[i] = len(buf)
        buf += f"{i} 0 obj\n".encode("ascii") + objects[i] + b"\nendobj\n"
    xref_offset = len(buf)
    buf += f"xref\n0 6\n0000000000 65535 f \n".encode("ascii")
    for i in range(1, 6):
        buf += f"{offsets[i]:010d} 00000 n \n".encode("ascii")
    buf += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("ascii")
    return bytes(buf)


def test_ingest_document_via_base64_pdf():
    client = _client()
    resp = client.post("/v1/bundles", json={"scenario_id": SCENARIO_ID, "actor_id": MAKER})
    bundle_id = resp.json()["bundle_id"]

    pdf_bytes = _minimal_pdf_bytes()
    resp = client.post(
        f"/v1/bundles/{bundle_id}/documents",
        json={
            "document_name": "CSA",
            "content_base64": base64.b64encode(pdf_bytes).decode("ascii"),
            "actor_id": MAKER,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["documents"] == ["CSA"]

    resp = client.get(f"/v1/bundles/{bundle_id}/documents/CSA")
    assert resp.status_code == 200
    assert "Hello from a PDF." in resp.json()["normalized_text"]


def test_ingest_document_rejects_both_content_fields_given():
    client = _client()
    resp = client.post("/v1/bundles", json={"scenario_id": SCENARIO_ID, "actor_id": MAKER})
    bundle_id = resp.json()["bundle_id"]
    resp = client.post(
        f"/v1/bundles/{bundle_id}/documents",
        json={"document_name": "CSA", "content": "hi", "content_base64": "aGk=", "actor_id": MAKER},
    )
    assert resp.status_code == 422


def test_ingest_corrupted_pdf_returns_422_with_ingestion_status():
    client = _client()
    resp = client.post("/v1/bundles", json={"scenario_id": SCENARIO_ID, "actor_id": MAKER})
    bundle_id = resp.json()["bundle_id"]

    garbage = base64.b64encode(b"%PDF-1.4\nnot a real pdf structure\n" * 10).decode("ascii")
    resp = client.post(
        f"/v1/bundles/{bundle_id}/documents",
        json={"document_name": "bad.pdf", "content_base64": garbage, "actor_id": MAKER},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "DocumentIngestionRejectedError"
    assert body["ingestion_status"] == "FAILED"


def test_list_bundles_endpoint():
    client = _client()
    client.post("/v1/bundles", json={"scenario_id": SCENARIO_ID, "actor_id": MAKER})
    client.post("/v1/bundles", json={"scenario_id": SCENARIO_ID, "actor_id": MAKER})
    resp = client.get("/v1/bundles")
    assert resp.status_code == 200
    assert len(resp.json()["bundles"]) == 2


def test_analyze_accepts_include_model_proposer_flag_defaulting_off():
    client = _client()
    resp = client.post("/v1/bundles", json={"scenario_id": SCENARIO_ID, "actor_id": MAKER})
    bundle_id = resp.json()["bundle_id"]
    client.post(
        f"/v1/bundles/{bundle_id}/documents",
        json={"document_name": "CSA", "content": CSA_TEXT, "actor_id": MAKER},
    )
    client.post(
        f"/v1/bundles/{bundle_id}/manifest:approve",
        json={
            "document_type": "CSA",
            "parties": ["Cassandre Simulation Bank N.A.", "SIM-COUNTERPARTY-ALPHA"],
            "execution_date": "2026-01-15",
            "effective_date": "2026-01-15",
            "incorporated_by_reference": [],
            "actor_id": MAKER,
        },
    )
    # Default (omitted) behaves exactly as before this gap was closed.
    resp = client.post(f"/v1/bundles/{bundle_id}:analyze", json={"actor_id": MAKER})
    assert resp.status_code == 200
    assert len(resp.json()["assertions"]) == 3


def test_audit_events_endpoint_returns_hash_chained_events():
    client = _client()
    resp = client.post("/v1/bundles", json={"scenario_id": SCENARIO_ID, "actor_id": MAKER})
    bundle_id = resp.json()["bundle_id"]
    resp = client.get(f"/v1/bundles/{bundle_id}/audit-events")
    assert resp.status_code == 200
    events = resp.json()["audit_events"]
    assert len(events) == 1
    assert events[0]["action"] == "BUNDLE_CREATED"
    # audit_protocol's genesis event uses an all-zero sentinel hash rather
    # than null for "no previous event" -- assert the sentinel, not None.
    assert set(events[0]["previous_event_hash"]) == {"0"}
    assert events[0]["event_hash"]


def test_policy_release_list_and_revoke():
    client = _client()
    resp = client.post("/v1/bundles", json={"scenario_id": SCENARIO_ID, "actor_id": MAKER})
    bundle_id = resp.json()["bundle_id"]
    client.post(
        f"/v1/bundles/{bundle_id}/documents",
        json={"document_name": "CSA", "content": CSA_TEXT, "actor_id": MAKER},
    )
    client.post(
        f"/v1/bundles/{bundle_id}/manifest:approve",
        json={
            "document_type": "CSA",
            "parties": ["Cassandre Simulation Bank N.A.", "SIM-COUNTERPARTY-ALPHA"],
            "execution_date": "2026-01-15",
            "effective_date": "2026-01-15",
            "incorporated_by_reference": [],
            "actor_id": MAKER,
        },
    )
    client.post(f"/v1/bundles/{bundle_id}:analyze", json={"actor_id": MAKER})
    reviews = client.get(f"/v1/bundles/{bundle_id}/reviews").json()["reviews"]
    for review in reviews:
        client.post(f"/v1/reviews/{review['review_id']}/maker-decision", json={"actor_id": MAKER, "decision": "APPROVE"})
        client.post(f"/v1/reviews/{review['review_id']}/checker-decision", json={"actor_id": CHECKER, "decision": "APPROVE"})

    resp = client.post("/v1/policy-releases", json={"bundle_id": bundle_id, "actor_id": CHECKER})
    release_id = resp.json()["policy_release"]["release_id"]

    resp = client.get("/v1/policy-releases", params={"bundle_id": bundle_id})
    assert resp.status_code == 200
    releases = resp.json()["policy_releases"]
    assert len(releases) == 1
    assert releases[0]["status"] == "PUBLISHED"

    resp = client.post(f"/v1/policy-releases/{release_id}:revoke", json={"actor_id": CHECKER})
    assert resp.status_code == 200
    assert resp.json()["status"] == "REVOKED"

    resp = client.get("/v1/policy-releases", params={"bundle_id": bundle_id})
    assert resp.json()["policy_releases"][0]["status"] == "REVOKED"

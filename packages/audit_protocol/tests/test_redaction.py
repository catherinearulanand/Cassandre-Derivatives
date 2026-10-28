from audit_protocol.redaction import REDACTED_MARKER, redact


def test_redacts_top_level_sensitive_key():
    out = redact({"document_text": "the actual clause text", "bundle_id": "b-1"})
    assert out["document_text"] == REDACTED_MARKER
    assert out["bundle_id"] == "b-1"


def test_redacts_nested_sensitive_key():
    out = redact({"payload": {"amount": "1000.00", "currency": "USD"}})
    assert out["payload"]["amount"] == REDACTED_MARKER
    assert out["payload"]["currency"] == "USD"


def test_redacts_within_lists():
    out = redact({"positions": [{"account_number": "12345"}]})
    assert out["positions"][0]["account_number"] == REDACTED_MARKER

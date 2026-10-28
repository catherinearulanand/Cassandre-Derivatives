import pytest

from audit_protocol.canonical import CanonicalizationError, canonicalize, content_hash


def test_canonical_is_order_independent():
    a = {"b": "2", "a": "1"}
    b = {"a": "1", "b": "2"}
    assert canonicalize(a) == canonicalize(b)


def test_content_hash_deterministic():
    obj = {"x": "1", "y": ["a", "b"]}
    assert content_hash(obj) == content_hash(dict(reversed(obj.items())))


def test_content_hash_changes_on_value_change():
    assert content_hash({"x": "1"}) != content_hash({"x": "2"})


def test_rejects_float():
    with pytest.raises(CanonicalizationError):
        canonicalize({"amount": 1.5})


def test_rejects_nested_float():
    with pytest.raises(CanonicalizationError):
        canonicalize({"payload": {"amount": 1.5}})

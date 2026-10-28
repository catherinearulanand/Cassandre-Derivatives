"""Deterministic canonicalization for content hashing and signing.

Plan Section 8.1 asks for "deterministic RFC 8785/JCS-compatible
serialization". This is an RFC-8785-*approximate* canonicalizer (sorted
object keys, no insignificant whitespace, UTF-8, no NaN/Infinity) — it does
not implement JCS's exact ECMA-262 number-to-string algorithm, since every
number that matters in this system is represented as a decimal string or an
RFC3339 timestamp string, never a JSON number. That gap is recorded here
rather than silently assumed away.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class CanonicalizationError(ValueError):
    pass


def _reject_non_finite(obj: Any) -> Any:
    if isinstance(obj, float):
        raise CanonicalizationError(
            "canonicalized payloads must not contain JSON numbers/floats; "
            "use decimal strings and RFC3339 timestamp strings"
        )
    if isinstance(obj, dict):
        return {k: _reject_non_finite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_reject_non_finite(v) for v in obj]
    return obj


def canonicalize(obj: dict[str, Any]) -> bytes:
    """Return the canonical UTF-8 byte serialization of a JSON-like object."""
    _reject_non_finite(obj)
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def content_hash(obj: dict[str, Any]) -> str:
    """SHA-256 hex digest of the canonical serialization."""
    return hashlib.sha256(canonicalize(obj)).hexdigest()

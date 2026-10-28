"""In-memory correlation store and trust-bundle registry for the Cassandre
control API slice. A Postgres-backed adapter is out of scope for this
session (see README) — everything here is process-local, which is why the
API is a single-process FastAPI app for this slice, not yet the durable
`aether_control`-equivalent (`cassandre_control`) database described in
plan Section 6.1 / docs/adr/ADR-002.
"""

from __future__ import annotations

import threading

from cassandre_contracts import TrustBundle

from cassandre_control_api.domain import CorrelatedCase, EnvelopeReceipt, correlation_key_for


class TrustRegistry:
    """A mutable, growable trust bundle. Real key lifecycle (offline root,
    rotation, revocation lists per plan Section 8.1) is out of scope for
    this slice; this only tracks key_id -> public_key_b64 plus a revoked
    set, sufficient to exercise verify_envelope end-to-end."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: dict[str, str] = {}
        self._revoked: set[str] = set()

    def register(self, key_id: str, public_key_b64: str) -> None:
        with self._lock:
            self._keys[key_id] = public_key_b64

    def revoke(self, key_id: str) -> None:
        with self._lock:
            self._revoked.add(key_id)

    def snapshot(self) -> TrustBundle:
        with self._lock:
            return TrustBundle(keys_by_id=dict(self._keys), revoked_key_ids=frozenset(self._revoked))


class CorrelationStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cases: dict[str, CorrelatedCase] = {}

    def add_receipt(self, receipt: EnvelopeReceipt) -> CorrelatedCase:
        key = correlation_key_for(receipt.envelope)
        with self._lock:
            case = self._cases.setdefault(key, CorrelatedCase(correlation_key=key))
            case.add(receipt)
            return case

    def get(self, correlation_key: str) -> CorrelatedCase | None:
        with self._lock:
            return self._cases.get(correlation_key)

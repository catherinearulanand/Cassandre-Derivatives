"""Hash-chained, tamper-evident audit events — pure logic (ADR-010).

This module has no database dependency: `append_event` computes the next
sequence/hash pair a caller should persist inside the same transaction as
the domain mutation and outbox row; `verify_chain` re-derives every hash
from a list of persisted events and reports the first point of tampering,
if any. Locking the `audit_chain_head` row (`SELECT ... FOR UPDATE`) so two
concurrent writers cannot allocate the same sequence number is the calling
product adapter's job (Postgres-specific), not this package's.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from audit_protocol.canonical import content_hash

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    sequence: int
    product: str
    aggregate_id: str
    actor_id: str
    actor_type: str
    action: str
    reason_code: str | None
    object_version: str
    object_content_hash: str
    event_timestamp: dt.datetime
    previous_event_hash: str
    simulation_provenance: dict[str, Any]
    trace_id: str
    event_hash: str

    def payload_for_hash(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "product": self.product,
            "aggregate_id": self.aggregate_id,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "action": self.action,
            "reason_code": self.reason_code,
            "object_version": self.object_version,
            "object_content_hash": self.object_content_hash,
            "event_timestamp": self.event_timestamp.astimezone(dt.timezone.utc).isoformat(),
            "previous_event_hash": self.previous_event_hash,
            "simulation_provenance": self.simulation_provenance,
            "trace_id": self.trace_id,
        }


def compute_event_hash(
    *,
    event_id: str,
    sequence: int,
    product: str,
    aggregate_id: str,
    actor_id: str,
    actor_type: str,
    action: str,
    reason_code: str | None,
    object_version: str,
    object_content_hash: str,
    event_timestamp: dt.datetime,
    previous_event_hash: str,
    simulation_provenance: dict[str, Any],
    trace_id: str,
) -> str:
    payload = {
        "event_id": event_id,
        "sequence": sequence,
        "product": product,
        "aggregate_id": aggregate_id,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "action": action,
        "reason_code": reason_code,
        "object_version": object_version,
        "object_content_hash": object_content_hash,
        "event_timestamp": event_timestamp.astimezone(dt.timezone.utc).isoformat(),
        "previous_event_hash": previous_event_hash,
        "simulation_provenance": simulation_provenance,
        "trace_id": trace_id,
    }
    return content_hash(payload)


def append_event(
    *,
    previous_event: AuditEvent | None,
    event_id: str,
    product: str,
    aggregate_id: str,
    actor_id: str,
    actor_type: str,
    action: str,
    reason_code: str | None,
    object_version: str,
    object_content_hash: str,
    event_timestamp: dt.datetime,
    simulation_provenance: dict[str, Any],
    trace_id: str,
) -> AuditEvent:
    sequence = 0 if previous_event is None else previous_event.sequence + 1
    previous_hash = GENESIS_HASH if previous_event is None else previous_event.event_hash
    event_hash = compute_event_hash(
        event_id=event_id,
        sequence=sequence,
        product=product,
        aggregate_id=aggregate_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        reason_code=reason_code,
        object_version=object_version,
        object_content_hash=object_content_hash,
        event_timestamp=event_timestamp,
        previous_event_hash=previous_hash,
        simulation_provenance=simulation_provenance,
        trace_id=trace_id,
    )
    return AuditEvent(
        event_id=event_id,
        sequence=sequence,
        product=product,
        aggregate_id=aggregate_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        reason_code=reason_code,
        object_version=object_version,
        object_content_hash=object_content_hash,
        event_timestamp=event_timestamp,
        previous_event_hash=previous_hash,
        simulation_provenance=simulation_provenance,
        trace_id=trace_id,
        event_hash=event_hash,
    )


@dataclass(frozen=True)
class ChainVerificationResult:
    ok: bool
    checked: int
    first_failure_sequence: int | None
    reason: str | None


def verify_chain(events: list[AuditEvent]) -> ChainVerificationResult:
    if not events:
        return ChainVerificationResult(ok=True, checked=0, first_failure_sequence=None, reason=None)

    ordered = sorted(events, key=lambda e: e.sequence)
    expected_previous = GENESIS_HASH
    for idx, event in enumerate(ordered):
        if event.sequence != idx:
            return ChainVerificationResult(
                ok=False, checked=idx, first_failure_sequence=event.sequence,
                reason=f"non-monotonic sequence: expected {idx}, got {event.sequence}",
            )
        if event.previous_event_hash != expected_previous:
            return ChainVerificationResult(
                ok=False, checked=idx, first_failure_sequence=event.sequence,
                reason="previous_event_hash does not match prior event's hash (fork or gap)",
            )
        recomputed = compute_event_hash(
            event_id=event.event_id,
            sequence=event.sequence,
            product=event.product,
            aggregate_id=event.aggregate_id,
            actor_id=event.actor_id,
            actor_type=event.actor_type,
            action=event.action,
            reason_code=event.reason_code,
            object_version=event.object_version,
            object_content_hash=event.object_content_hash,
            event_timestamp=event.event_timestamp,
            previous_event_hash=event.previous_event_hash,
            simulation_provenance=event.simulation_provenance,
            trace_id=event.trace_id,
        )
        if recomputed != event.event_hash:
            return ChainVerificationResult(
                ok=False, checked=idx, first_failure_sequence=event.sequence,
                reason="event_hash does not match recomputed hash (tampered event)",
            )
        expected_previous = event.event_hash

    return ChainVerificationResult(ok=True, checked=len(ordered), first_failure_sequence=None, reason=None)

"""Independent witness / external checkpoint store (SEC-15, witness half).

Spec section 12: "An independent witness, administered with credentials
unavailable to application and database administrators, retains
`{product_chain_id, sequence, head_hash, release_manifest_digest,
trust_bundle_version, receipt_time}` and rejects inconsistent extensions.
Sensitive publication/export blocks when authenticated witness receipts
exceed the approved maximum lag."

This module implements the store's logic and schema. It does NOT itself
solve "credentials unavailable to application and database
administrators" -- that is a deployment/ops decision (a separate database
role, separate host, or separate managed service) recorded in this
package's README; the code here is deployable under any of those
profiles.

S1 explicitly does not claim public notarization or legally trusted
timestamping (spec section 12) -- this is "an isolated evidence host plus
an offline end-of-day copy" in spirit, sized for a synthetic workstation.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


def _ensure_utc(value: dt.datetime) -> dt.datetime:
    """See the identical helper in `revocation.py`: SQLite does not
    preserve tzinfo on a `DateTime(timezone=True)` round-trip."""
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class WitnessRow(Base):
    __tablename__ = "cassandre_security_witness_checkpoint"

    product_chain_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    head_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    release_manifest_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    trust_bundle_version: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class WitnessRecord:
    product_chain_id: str
    sequence: int
    head_hash: str
    release_manifest_digest: str
    trust_bundle_version: str
    receipt_time: dt.datetime


class WitnessError(ValueError):
    pass


class WitnessForkError(WitnessError):
    """A different head_hash was submitted for an already-witnessed
    sequence number -- exactly the "rejects inconsistent extensions"
    requirement. This is a hard stop: the witness never overwrites a
    prior receipt."""


class WitnessGapError(WitnessError):
    """The submitted sequence is not exactly one greater than the last
    witnessed sequence for this chain -- catches both a skipped sequence
    (missing intermediate event) and a replayed/rolled-back sequence."""


class WitnessFreshnessError(WitnessError):
    pass


def submit_checkpoint(
    session: Session,
    *,
    product_chain_id: str,
    sequence: int,
    head_hash: str,
    release_manifest_digest: str,
    trust_bundle_version: str,
    now: dt.datetime,
) -> WitnessRecord:
    existing_same_sequence = session.get(WitnessRow, (product_chain_id, sequence))
    if existing_same_sequence is not None:
        if existing_same_sequence.head_hash != head_hash:
            raise WitnessForkError(
                f"chain {product_chain_id!r} sequence {sequence} already witnessed with a different head_hash "
                "(rejected as an inconsistent extension / fork attempt)"
            )
        # Exact replay of an already-witnessed receipt is idempotent, not an error.
        return _row_to_record(existing_same_sequence)

    stmt = (
        select(WitnessRow)
        .where(WitnessRow.product_chain_id == product_chain_id)
        .order_by(WitnessRow.sequence.desc())
        .limit(1)
    )
    last = session.execute(stmt).scalars().first()
    expected_next = 0 if last is None else last.sequence + 1
    if sequence != expected_next:
        raise WitnessGapError(
            f"chain {product_chain_id!r}: expected next sequence {expected_next}, got {sequence}"
        )

    row = WitnessRow(
        product_chain_id=product_chain_id,
        sequence=sequence,
        head_hash=head_hash,
        release_manifest_digest=release_manifest_digest,
        trust_bundle_version=trust_bundle_version,
        receipt_time=now,
    )
    session.add(row)
    session.flush()
    return _row_to_record(row)


def _row_to_record(row: WitnessRow) -> WitnessRecord:
    return WitnessRecord(
        product_chain_id=row.product_chain_id,
        sequence=row.sequence,
        head_hash=row.head_hash,
        release_manifest_digest=row.release_manifest_digest,
        trust_bundle_version=row.trust_bundle_version,
        receipt_time=row.receipt_time,
    )


def latest_checkpoint(session: Session, *, product_chain_id: str) -> WitnessRecord | None:
    stmt = (
        select(WitnessRow)
        .where(WitnessRow.product_chain_id == product_chain_id)
        .order_by(WitnessRow.sequence.desc())
        .limit(1)
    )
    row = session.execute(stmt).scalars().first()
    return _row_to_record(row) if row is not None else None


def check_freshness(
    session: Session, *, product_chain_id: str, now: dt.datetime, max_lag: dt.timedelta
) -> WitnessRecord:
    """Spec: "Sensitive publication/export blocks when authenticated
    witness receipts exceed the approved maximum lag." Raises
    `WitnessFreshnessError` (never returns a stale record silently) if
    there is no checkpoint yet, or the latest one is older than
    `max_lag`."""

    record = latest_checkpoint(session, product_chain_id=product_chain_id)
    if record is None:
        raise WitnessFreshnessError(f"no witness checkpoint exists yet for chain {product_chain_id!r}")
    if now - _ensure_utc(record.receipt_time) > max_lag:
        raise WitnessFreshnessError(
            f"chain {product_chain_id!r}: latest witness receipt is {now - _ensure_utc(record.receipt_time)} old, "
            f"exceeding max_lag={max_lag}; blocking sensitive publication/export"
        )
    return record

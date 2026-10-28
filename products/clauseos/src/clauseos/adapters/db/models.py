"""SQLAlchemy declarative tables for the ClauseOS Section 25.5 slice (plan
Section 25.5 item 9 / repository ownership map in `docs/INTERPRETATION.md`).

Nested value objects that the domain layer treats as a single immutable
unit (a manifest's parties/relationships, a candidate's proposed value, a
policy release's published terms) are stored as JSON columns on their
parent row rather than fully normalized into further child tables -- a
deliberate scope simplification for this slice, not a hidden one. Evidence
spans get their own table because the plan explicitly lists them separately
and multiple assertions may eventually reference overlapping spans.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_TZ_DATETIME = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class AgreementBundleRow(Base):
    __tablename__ = "agreement_bundle"

    bundle_id: Mapped[str] = mapped_column(primary_key=True)
    scenario_id: Mapped[str]
    status: Mapped[str]
    created_at: Mapped[dt.datetime] = mapped_column(_TZ_DATETIME)
    manifest_json: Mapped[dict | None] = mapped_column(JSON)
    manifest_approved: Mapped[bool] = mapped_column(default=False)
    incompleteness_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(default=0)


class DocumentNodeRow(Base):
    __tablename__ = "document_node"

    document_id: Mapped[str] = mapped_column(primary_key=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("agreement_bundle.bundle_id"))
    document_name: Mapped[str]
    raw_bytes_sha256: Mapped[str]
    normalized_text: Mapped[str] = mapped_column(Text)
    normalized_text_sha256: Mapped[str]


class EvidenceSpanRow(Base):
    __tablename__ = "evidence_span"

    span_id: Mapped[str] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("document_node.document_id"))
    start_offset: Mapped[int]
    end_offset: Mapped[int]
    snippet: Mapped[str] = mapped_column(Text)
    source_content_hash: Mapped[str]


class AssertionRow(Base):
    __tablename__ = "assertion"

    assertion_id: Mapped[str] = mapped_column(primary_key=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("agreement_bundle.bundle_id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("document_node.document_id"))
    span_id: Mapped[str] = mapped_column(ForeignKey("evidence_span.span_id"))
    concept: Mapped[str]
    status: Mapped[str]
    proposer: Mapped[str]
    candidate_id: Mapped[str]
    proposed_value_json: Mapped[dict] = mapped_column(JSON)
    evidence_content_hash: Mapped[str]
    conflict_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(default=0)


class ReviewCaseRow(Base):
    __tablename__ = "review_case"

    review_id: Mapped[str] = mapped_column(primary_key=True)
    assertion_id: Mapped[str] = mapped_column(ForeignKey("assertion.assertion_id"))
    bundle_id: Mapped[str] = mapped_column(ForeignKey("agreement_bundle.bundle_id"))
    bound_evidence_hash: Mapped[str]
    status: Mapped[str]
    maker_actor_id: Mapped[str | None]
    maker_decision: Mapped[str | None]
    maker_decided_at: Mapped[dt.datetime | None] = mapped_column(_TZ_DATETIME)
    checker_actor_id: Mapped[str | None]
    checker_decision: Mapped[str | None]
    checker_decided_at: Mapped[dt.datetime | None] = mapped_column(_TZ_DATETIME)
    version: Mapped[int] = mapped_column(default=0)


class PolicyReleaseRow(Base):
    __tablename__ = "policy_release"

    release_id: Mapped[str] = mapped_column(primary_key=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("agreement_bundle.bundle_id"))
    scenario_id: Mapped[str]
    created_at: Mapped[dt.datetime] = mapped_column(_TZ_DATETIME)
    status: Mapped[str]
    terms_json: Mapped[list] = mapped_column(JSON)


class AuditEventRow(Base):
    __tablename__ = "audit_event"

    event_id: Mapped[str] = mapped_column(primary_key=True)
    sequence: Mapped[int]
    product: Mapped[str]
    aggregate_id: Mapped[str]
    actor_id: Mapped[str]
    actor_type: Mapped[str]
    action: Mapped[str]
    reason_code: Mapped[str | None] = mapped_column(Text)
    object_version: Mapped[str]
    object_content_hash: Mapped[str]
    event_timestamp: Mapped[dt.datetime] = mapped_column(_TZ_DATETIME)
    previous_event_hash: Mapped[str]
    simulation_provenance_json: Mapped[dict] = mapped_column(JSON)
    trace_id: Mapped[str]
    event_hash: Mapped[str]


class OutboxRow(Base):
    """Minimal outbox per plan Section 8.4 / this slice's item 9: enough to
    prove the pattern (unprocessed events are visible and can be marked
    processed), not a full at-least-once delivery worker."""

    __tablename__ = "outbox"

    event_id: Mapped[str] = mapped_column(primary_key=True)
    payload_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(_TZ_DATETIME)
    processed_at: Mapped[dt.datetime | None] = mapped_column(_TZ_DATETIME)

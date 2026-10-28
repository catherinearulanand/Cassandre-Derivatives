"""SQLAlchemy-backed implementation of `clauseos.application.ports.BundleRepository`.

Requires a real Postgres connection (`CASSANDRE_TEST_DATABASE_URL` in tests;
Docker Desktop is not functional on this development machine, see
`products/clauseos/tests/conftest.py`, so this adapter's own tests skip
cleanly rather than fail when no database is reachable). Each `save_*` call
opens and commits its own transaction; `append_audit_event` reads the
current chain head for the aggregate and appends inside the same
transaction as its own row insert, per plan Section 25.5 item 9.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from audit_protocol.chain import AuditEvent, append_event
from clauseos.adapters.db.mapping import (
    _ensure_utc,
    assertion_from_row,
    assertion_to_row,
    bundle_from_row,
    bundle_to_row,
    document_from_row,
    document_to_row,
    evidence_from_row,
    evidence_to_row,
    policy_release_from_row,
    policy_release_to_row,
    review_case_from_row,
    review_case_to_row,
)
from clauseos.adapters.db.models import (
    AgreementBundleRow,
    AssertionRow,
    AuditEventRow,
    DocumentNodeRow,
    EvidenceSpanRow,
    OutboxRow,
    PolicyReleaseRow,
    ReviewCaseRow,
)
from clauseos.domain.assertion import ContractAssertion
from clauseos.domain.bundle import AgreementBundle, DocumentNode
from clauseos.domain.policy import PolicyRelease
from clauseos.domain.review import ReviewCase

PRODUCT_NAME = "clauseos"


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


class SqlAlchemyBundleRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_bundle(self, bundle: AgreementBundle) -> None:
        with self._session_factory() as session:
            session.merge(bundle_to_row(bundle))
            for document in bundle.documents.values():
                session.merge(document_to_row(document))
            session.commit()

    def get_bundle(self, bundle_id: str) -> AgreementBundle | None:
        with self._session_factory() as session:
            row = session.get(AgreementBundleRow, bundle_id)
            if row is None:
                return None
            doc_rows = session.scalars(
                select(DocumentNodeRow).where(DocumentNodeRow.bundle_id == bundle_id)
            ).all()
            documents = {r.document_name: document_from_row(r) for r in doc_rows}
            return bundle_from_row(row, documents)

    def list_bundles(self) -> list[AgreementBundle]:
        with self._session_factory() as session:
            rows = session.scalars(select(AgreementBundleRow)).all()
            result = []
            for row in rows:
                doc_rows = session.scalars(
                    select(DocumentNodeRow).where(DocumentNodeRow.bundle_id == row.bundle_id)
                ).all()
                documents = {r.document_name: document_from_row(r) for r in doc_rows}
                result.append(bundle_from_row(row, documents))
            return result

    def save_assertion(self, assertion: ContractAssertion) -> None:
        with self._session_factory() as session:
            session.merge(evidence_to_row(assertion.candidate.evidence))
            session.merge(assertion_to_row(assertion))
            session.commit()

    def get_assertion(self, assertion_id: str) -> ContractAssertion | None:
        with self._session_factory() as session:
            row = session.get(AssertionRow, assertion_id)
            if row is None:
                return None
            span_row = session.get(EvidenceSpanRow, row.span_id)
            assert span_row is not None
            return assertion_from_row(row, evidence_from_row(span_row))

    def list_assertions(
        self, *, bundle_id: str | None = None, concept: str | None = None, status: str | None = None
    ) -> list[ContractAssertion]:
        with self._session_factory() as session:
            stmt = select(AssertionRow)
            if bundle_id is not None:
                stmt = stmt.where(AssertionRow.bundle_id == bundle_id)
            if concept is not None:
                stmt = stmt.where(AssertionRow.concept == concept)
            if status is not None:
                stmt = stmt.where(AssertionRow.status == status)
            rows = session.scalars(stmt).all()
            result = []
            for row in rows:
                span_row = session.get(EvidenceSpanRow, row.span_id)
                assert span_row is not None
                result.append(assertion_from_row(row, evidence_from_row(span_row)))
            return result

    def save_review_case(self, review_case: ReviewCase) -> None:
        with self._session_factory() as session:
            session.merge(review_case_to_row(review_case))
            session.commit()

    def get_review_case(self, review_id: str) -> ReviewCase | None:
        with self._session_factory() as session:
            row = session.get(ReviewCaseRow, review_id)
            return None if row is None else review_case_from_row(row)

    def list_review_cases(self, *, bundle_id: str | None = None) -> list[ReviewCase]:
        with self._session_factory() as session:
            stmt = select(ReviewCaseRow)
            if bundle_id is not None:
                stmt = stmt.where(ReviewCaseRow.bundle_id == bundle_id)
            rows = session.scalars(stmt).all()
            return [review_case_from_row(r) for r in rows]

    def save_policy_release(self, release: PolicyRelease) -> None:
        with self._session_factory() as session:
            session.merge(policy_release_to_row(release))
            session.commit()

    def get_policy_release(self, release_id: str) -> PolicyRelease | None:
        with self._session_factory() as session:
            row = session.get(PolicyReleaseRow, release_id)
            return None if row is None else policy_release_from_row(row)

    def list_policy_releases(self, *, bundle_id: str | None = None) -> list[PolicyRelease]:
        with self._session_factory() as session:
            stmt = select(PolicyReleaseRow)
            if bundle_id is not None:
                stmt = stmt.where(PolicyReleaseRow.bundle_id == bundle_id)
            rows = session.scalars(stmt).all()
            return [policy_release_from_row(r) for r in rows]

    def append_audit_event(
        self,
        *,
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
        import uuid

        with self._session_factory() as session:
            last_row = session.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.aggregate_id == aggregate_id)
                .order_by(AuditEventRow.sequence.desc())
                .limit(1)
            ).first()
            previous_event = None
            if last_row is not None:
                previous_event = AuditEvent(
                    event_id=last_row.event_id,
                    sequence=last_row.sequence,
                    product=last_row.product,
                    aggregate_id=last_row.aggregate_id,
                    actor_id=last_row.actor_id,
                    actor_type=last_row.actor_type,
                    action=last_row.action,
                    reason_code=last_row.reason_code,
                    object_version=last_row.object_version,
                    object_content_hash=last_row.object_content_hash,
                    event_timestamp=_ensure_utc(last_row.event_timestamp),
                    previous_event_hash=last_row.previous_event_hash,
                    simulation_provenance=last_row.simulation_provenance_json,
                    trace_id=last_row.trace_id,
                    event_hash=last_row.event_hash,
                )
            event = append_event(
                previous_event=previous_event,
                event_id=str(uuid.uuid4()),
                product=PRODUCT_NAME,
                aggregate_id=aggregate_id,
                actor_id=actor_id,
                actor_type=actor_type,
                action=action,
                reason_code=reason_code,
                object_version=object_version,
                object_content_hash=object_content_hash,
                event_timestamp=event_timestamp,
                simulation_provenance=simulation_provenance,
                trace_id=trace_id,
            )
            session.add(
                AuditEventRow(
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
                    simulation_provenance_json=event.simulation_provenance,
                    trace_id=event.trace_id,
                    event_hash=event.event_hash,
                )
            )
            session.commit()
            return event

    def list_audit_events(self, aggregate_id: str) -> list[AuditEvent]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.aggregate_id == aggregate_id)
                .order_by(AuditEventRow.sequence.asc())
            ).all()
            return [
                AuditEvent(
                    event_id=r.event_id,
                    sequence=r.sequence,
                    product=r.product,
                    aggregate_id=r.aggregate_id,
                    actor_id=r.actor_id,
                    actor_type=r.actor_type,
                    action=r.action,
                    reason_code=r.reason_code,
                    object_version=r.object_version,
                    object_content_hash=r.object_content_hash,
                    event_timestamp=_ensure_utc(r.event_timestamp),
                    previous_event_hash=r.previous_event_hash,
                    simulation_provenance=r.simulation_provenance_json,
                    trace_id=r.trace_id,
                    event_hash=r.event_hash,
                )
                for r in rows
            ]

    def write_outbox_event(self, *, event_id: str, payload: dict[str, Any], created_at: dt.datetime) -> None:
        with self._session_factory() as session:
            session.merge(OutboxRow(event_id=event_id, payload_json=payload, created_at=created_at, processed_at=None))
            session.commit()

    def mark_outbox_processed(self, *, event_id: str, processed_at: dt.datetime) -> None:
        with self._session_factory() as session:
            row = session.get(OutboxRow, event_id)
            if row is not None:
                row.processed_at = processed_at
                session.commit()

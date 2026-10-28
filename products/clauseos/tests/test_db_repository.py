"""DB-backed repository round-trip tests. Skipped cleanly (via the
`clauseos_db_engine` fixture in conftest.py) when `CASSANDRE_TEST_DATABASE_URL`
is unset or unreachable -- Docker Desktop is not functional on this
development machine (see docs/INTERPRETATION.md DEC-ENV-01)."""

import datetime as dt

from audit_protocol.chain import verify_chain
from money_types import Money

from clauseos.adapters.db.models import Base
from clauseos.adapters.db.repository import SqlAlchemyBundleRepository, make_session_factory
from clauseos.domain.assertion import AssertionCandidate, ContractAssertion, compute_evidence_content_hash
from clauseos.domain.bundle import AgreementBundle, DocumentNode
from clauseos.domain.evidence import EvidenceSpan
from clauseos.domain.review import ReviewCase
from clauseos.domain.status import AssertionStatus, BundleStatus, MaterialTermConcept, ReviewCaseStatus
from clauseos.domain.values import MoneyTermValue


def test_bundle_document_assertion_review_round_trip(clauseos_db_engine):
    Base.metadata.create_all(clauseos_db_engine)
    repo = SqlAlchemyBundleRepository(make_session_factory(clauseos_db_engine))

    bundle = AgreementBundle(
        bundle_id="db-bundle-1",
        scenario_id="SIM-SCEN-CLAUSEOS-0001",
        status=BundleStatus.CREATED,
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    )
    document = DocumentNode(
        document_id="db-doc-1",
        bundle_id=bundle.bundle_id,
        document_name="CSA",
        raw_bytes_sha256="a" * 64,
        normalized_text="Threshold shall be USD 1,000.00",
        normalized_text_sha256="b" * 64,
    )
    bundle.documents[document.document_name] = document
    repo.save_bundle(bundle)

    fetched = repo.get_bundle(bundle.bundle_id)
    assert fetched is not None
    assert fetched.status is BundleStatus.CREATED
    assert fetched.documents["CSA"].normalized_text == document.normalized_text

    evidence = EvidenceSpan(
        span_id="db-span-1",
        document_id=document.document_id,
        start_offset=0,
        end_offset=9,
        snippet="Threshold",
        source_content_hash="b" * 64,
    )
    candidate = AssertionCandidate(
        candidate_id="db-cand-1",
        bundle_id=bundle.bundle_id,
        document_id=document.document_id,
        concept=MaterialTermConcept.THRESHOLD,
        proposed_value=MoneyTermValue(money=Money(amount="1000.00", currency="USD", rounding_context="TEST")),
        evidence=evidence,
    )
    assertion = ContractAssertion(
        assertion_id="db-assert-1",
        bundle_id=bundle.bundle_id,
        concept=MaterialTermConcept.THRESHOLD,
        candidate=candidate,
        status=AssertionStatus.STRUCTURALLY_VERIFIED,
        evidence_content_hash=compute_evidence_content_hash(candidate),
    )
    repo.save_assertion(assertion)
    fetched_assertion = repo.get_assertion(assertion.assertion_id)
    assert fetched_assertion is not None
    assert fetched_assertion.status is AssertionStatus.STRUCTURALLY_VERIFIED
    assert isinstance(fetched_assertion.candidate.proposed_value, MoneyTermValue)
    assert str(fetched_assertion.candidate.proposed_value.money.amount) == "1000.00"

    review_case = ReviewCase(
        review_id="db-review-1",
        assertion_id=assertion.assertion_id,
        bundle_id=bundle.bundle_id,
        bound_evidence_hash=assertion.evidence_content_hash,
    )
    repo.save_review_case(review_case)
    fetched_review = repo.get_review_case(review_case.review_id)
    assert fetched_review is not None
    assert fetched_review.status is ReviewCaseStatus.OPEN

    event_1 = repo.append_audit_event(
        aggregate_id=bundle.bundle_id,
        actor_id="tester",
        actor_type="HUMAN",
        action="TEST_EVENT_1",
        reason_code=None,
        object_version="0",
        object_content_hash="c" * 64,
        event_timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        simulation_provenance={"is_simulated": True, "scenario_id": bundle.scenario_id, "generator_version": "0.1.0"},
        trace_id="trace-1",
    )
    event_2 = repo.append_audit_event(
        aggregate_id=bundle.bundle_id,
        actor_id="tester",
        actor_type="HUMAN",
        action="TEST_EVENT_2",
        reason_code=None,
        object_version="1",
        object_content_hash="d" * 64,
        event_timestamp=dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc),
        simulation_provenance={"is_simulated": True, "scenario_id": bundle.scenario_id, "generator_version": "0.1.0"},
        trace_id="trace-2",
    )
    assert event_2.previous_event_hash == event_1.event_hash

    chain = repo.list_audit_events(bundle.bundle_id)
    result = verify_chain(chain)
    assert result.ok, result.reason


def test_outbox_round_trip(clauseos_db_engine):
    Base.metadata.create_all(clauseos_db_engine)
    repo = SqlAlchemyBundleRepository(make_session_factory(clauseos_db_engine))

    repo.write_outbox_event(
        event_id="outbox-1",
        payload={"kind": "POLICY_RELEASE_PUBLISHED"},
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    )
    repo.mark_outbox_processed(event_id="outbox-1", processed_at=dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc))

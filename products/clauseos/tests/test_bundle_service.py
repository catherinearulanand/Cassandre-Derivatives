import datetime as dt

import pytest
from audit_protocol.keys import generate_keypair
from cassandre_contracts import TrustBundle, verify_envelope

from clauseos.adapters.memory import InMemoryBundleRepository
from clauseos.application.bundle_service import BundleService
from clauseos.domain.errors import BundleIncompleteError, IncompletePolicyError
from clauseos.domain.status import AssertionStatus, BundleStatus, MaterialTermConcept, ReviewDecision

from helpers import ANNEX_DOCUMENT_NAME, CSA_DOCUMENT_NAME, annex_bytes, build_manifest, csa_bytes

MAKER = "maker.alice@sim.cassandre.test"
CHECKER = "checker.bob@sim.cassandre.test"
SCENARIO_ID = "SIM-SCEN-CLAUSEOS-0001"


def _service() -> BundleService:
    return BundleService(InMemoryBundleRepository())


def _ingest_complete_bundle(service: BundleService) -> str:
    bundle = service.create_bundle(scenario_id=SCENARIO_ID, actor_id=MAKER)
    service.ingest_document(
        bundle_id=bundle.bundle_id, document_name=CSA_DOCUMENT_NAME, raw_bytes=csa_bytes(), actor_id=MAKER
    )
    service.ingest_document(
        bundle_id=bundle.bundle_id, document_name=ANNEX_DOCUMENT_NAME, raw_bytes=annex_bytes(), actor_id=MAKER
    )
    service.approve_manifest(
        bundle_id=bundle.bundle_id, manifest=build_manifest(incorporate_annex=True), actor_id=MAKER
    )
    return bundle.bundle_id


def _approve_all_review_cases(service: BundleService, bundle_id: str) -> None:
    review_cases = service.list_review_cases(bundle_id=bundle_id)
    assert len(review_cases) == 3
    for review_case in review_cases:
        service.record_maker_decision(review_id=review_case.review_id, actor_id=MAKER, decision=ReviewDecision.APPROVE)
        service.record_checker_decision(
            review_id=review_case.review_id, actor_id=CHECKER, decision=ReviewDecision.APPROVE
        )


def test_end_to_end_slice_one_bundle_to_signed_policy_release():
    service = _service()
    bundle_id = _ingest_complete_bundle(service)
    bundle = service.get_bundle(bundle_id)
    assert bundle.status is BundleStatus.PARSED

    assertions = service.analyze_bundle(bundle_id=bundle_id, actor_id=MAKER)
    assert {a.concept for a in assertions} == {
        MaterialTermConcept.THRESHOLD,
        MaterialTermConcept.MTA,
        MaterialTermConcept.NOTIFICATION_DEADLINE,
    }
    assert all(a.status is AssertionStatus.HUMAN_REVIEW_REQUIRED for a in assertions)
    assert service.get_bundle(bundle_id).status is BundleStatus.REVIEW_REQUIRED

    _approve_all_review_cases(service, bundle_id)

    keypair = generate_keypair("clauseos-slice-key-1")
    release, envelope = service.publish_policy_release(bundle_id=bundle_id, actor_id=CHECKER, keypair=keypair)

    assert len(release.terms) == 3
    assert service.get_bundle(bundle_id).status is BundleStatus.PUBLISHED
    assert envelope.signature is not None
    assert envelope.payload["bundle_id"] == bundle_id

    trust_bundle = TrustBundle(keys_by_id={keypair.key_id: keypair.public_key_b64})
    result = verify_envelope(envelope, trust_bundle=trust_bundle, expected_audience="cassandre")
    assert result.ok, result.failures


def test_tampered_signed_envelope_fails_verification():
    service = _service()
    bundle_id = _ingest_complete_bundle(service)
    service.analyze_bundle(bundle_id=bundle_id, actor_id=MAKER)
    _approve_all_review_cases(service, bundle_id)

    keypair = generate_keypair("clauseos-slice-key-1")
    release, envelope = service.publish_policy_release(bundle_id=bundle_id, actor_id=CHECKER, keypair=keypair)

    tampered = envelope.model_copy(
        update={"payload": {**envelope.payload, "terms": []}}
    )
    trust_bundle = TrustBundle(keys_by_id={keypair.key_id: keypair.public_key_b64})
    result = verify_envelope(tampered, trust_bundle=trust_bundle, expected_audience="cassandre")
    assert not result.ok
    assert "CONTENT_HASH_MISMATCH" in result.failures


def test_publish_without_full_checker_approval_raises_incomplete_policy_error():
    service = _service()
    bundle_id = _ingest_complete_bundle(service)
    service.analyze_bundle(bundle_id=bundle_id, actor_id=MAKER)

    review_cases = service.list_review_cases(bundle_id=bundle_id)
    # Only approve two of the three required material terms.
    for review_case in review_cases[:2]:
        service.record_maker_decision(review_id=review_case.review_id, actor_id=MAKER, decision=ReviewDecision.APPROVE)
        service.record_checker_decision(
            review_id=review_case.review_id, actor_id=CHECKER, decision=ReviewDecision.APPROVE
        )

    keypair = generate_keypair("clauseos-slice-key-1")
    with pytest.raises(IncompletePolicyError):
        service.publish_policy_release(bundle_id=bundle_id, actor_id=CHECKER, keypair=keypair)

    # Fail closed: the bundle must not have been silently advanced to PUBLISHED.
    assert service.get_bundle(bundle_id).status is BundleStatus.REVIEW_REQUIRED


def test_missing_incorporated_document_via_service_produces_bundle_incomplete():
    service = _service()
    bundle = service.create_bundle(scenario_id=SCENARIO_ID, actor_id=MAKER)
    service.ingest_document(
        bundle_id=bundle.bundle_id, document_name=CSA_DOCUMENT_NAME, raw_bytes=csa_bytes(), actor_id=MAKER
    )
    # Annex is never ingested, but the manifest declares it incorporated.
    with pytest.raises(BundleIncompleteError):
        service.approve_manifest(
            bundle_id=bundle.bundle_id, manifest=build_manifest(incorporate_annex=True), actor_id=MAKER
        )
    assert service.get_bundle(bundle.bundle_id).status is BundleStatus.BUNDLE_INCOMPLETE

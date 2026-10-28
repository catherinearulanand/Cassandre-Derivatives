"""Tests proving `cassandre_security` is genuinely wired into ClauseOS's
service layer, not merely imported and left unused:

- SEC-04: an unauthorized action is actually DENIED and the mutation never
  happens (both directly against `BundleService` and through the FastAPI
  app, where it must surface as HTTP 403).
- SEC-05: a self-approval attempt is rejected by the
  `cassandre_security.maker_checker` defense-in-depth call, even in a case
  ClauseOS's own domain-layer string-equality check would not catch (two
  different actor_id strings resolved to the same natural person by a
  custom `NaturalPersonDirectory`).
- SEC-08: a sealed document round-trips through `envelope.seal`/`open_`,
  and a tampered ciphertext fails closed.
- SEC-14: the revocation/security-fence mechanism genuinely blocks a
  critical mutation (publish policy release / checker approval) that
  presents a stale epoch, proven against real Postgres via the existing
  `clauseos_db_engine` fixture -- skipped cleanly if
  `CASSANDRE_TEST_DATABASE_URL` is unset, exactly like every other
  DB-backed ClauseOS test.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from cassandre_security.authorization import ActorKind, AuthorizationRequest, Outcome, ReasonCode
from cassandre_security.envelope import EnvelopeTamperError
from cassandre_security.maker_checker import NaturalPersonDirectory
from cassandre_security.revocation import Base as FenceBase, RevocationState

from clauseos.adapters.db.fence_gateway import (
    SqlAlchemyRevocationFenceGateway,
    clauseos_fence_scope_key,
    make_session_factory,
)
from clauseos.adapters.memory import InMemoryBundleRepository
from clauseos.application.bundle_service import BundleService
from clauseos.application.document_sealing import DocumentSealingService
from clauseos.application.security import ClauseOsAuthorizationService
from clauseos.domain.bundle import BundleStatus
from clauseos.domain.errors import AuthorizationDeniedError, RevocationFenceBlockedError, SameActorError
from clauseos.domain.status import ReviewDecision

from helpers import ANNEX_DOCUMENT_NAME, CSA_DOCUMENT_NAME, annex_bytes, build_manifest, csa_bytes

MAKER = "maker.alice@sim.cassandre.test"
CHECKER = "checker.bob@sim.cassandre.test"
SCENARIO_ID = "SIM-SCEN-CLAUSEOS-0001"


# ---------------------------------------------------------------------------
# SEC-04: authorization gate
# ---------------------------------------------------------------------------


class _DenyOneActorRevocationChecker:
    """Simulates a revoked/deactivated actor session: reports every actor
    EXCEPT `denied_actor_subject` as current. Standing in for the real
    IdP-backed RevocationChecker ClauseOS does not have yet -- see
    `clauseos.application.security` module docstring."""

    def __init__(self, denied_actor_subject: str) -> None:
        self._denied = denied_actor_subject

    def is_current(self, *, tenant_id: str, actor_subject: str, revocation_epoch: int) -> bool:
        return actor_subject != self._denied


def test_authorization_gate_denies_ingest_for_revoked_actor_and_blocks_mutation():
    authz = ClauseOsAuthorizationService.build_default(
        revocation_checker=_DenyOneActorRevocationChecker(denied_actor_subject=MAKER)
    )
    service = BundleService(InMemoryBundleRepository(), authorization=authz)
    bundle = service.create_bundle(scenario_id=SCENARIO_ID, actor_id=MAKER)

    with pytest.raises(AuthorizationDeniedError) as excinfo:
        service.ingest_document(
            bundle_id=bundle.bundle_id, document_name=CSA_DOCUMENT_NAME, raw_bytes=csa_bytes(), actor_id=MAKER
        )
    assert excinfo.value.reason_code == ReasonCode.REVOKED.value

    # The DENY must have actually blocked the mutation, not merely logged it.
    reloaded = service.get_bundle(bundle.bundle_id)
    assert reloaded.documents == {}
    assert reloaded.status is BundleStatus.CREATED


def test_authorization_gate_allows_a_different_non_denied_actor():
    authz = ClauseOsAuthorizationService.build_default(
        revocation_checker=_DenyOneActorRevocationChecker(denied_actor_subject="someone-else@sim.cassandre.test")
    )
    service = BundleService(InMemoryBundleRepository(), authorization=authz)
    bundle = service.create_bundle(scenario_id=SCENARIO_ID, actor_id=MAKER)

    document = service.ingest_document(
        bundle_id=bundle.bundle_id, document_name=CSA_DOCUMENT_NAME, raw_bytes=csa_bytes(), actor_id=MAKER
    )
    assert document.document_name == CSA_DOCUMENT_NAME


def test_authorization_gate_denies_unknown_action_by_default():
    """Proves the engine's deny-by-default core directly: an action not on
    ClauseOS's allowlist (`clauseos.application.security.default_rules`)
    is denied, even with an otherwise-valid request."""
    authz = ClauseOsAuthorizationService.build_default()
    request = AuthorizationRequest(
        request_id="req-1",
        actor_subject=MAKER,
        actor_kind=ActorKind.HUMAN,
        tenant_id=SCENARIO_ID,
        legal_entity_id=None,
        product_id="clauseos",
        object_id="bundle-1",
        object_version="0",
        content_digest="sha256:abc",
        action="DELETE_EVERYTHING",
        purpose="NOT_A_REAL_PURPOSE",
        policy_release_digest=authz.engine.policy_release_digest,
        revocation_epoch=0,
        session_assurance="sim-actor-assurance",
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5),
    )
    decision = authz.engine.decide(request, now=dt.datetime.now(dt.timezone.utc), decision_id="d-1")
    assert decision.outcome is Outcome.DENY
    assert decision.reason_code is ReasonCode.UNKNOWN_ACTION


def test_authorization_gate_denies_document_ingestion_over_api_returns_403():
    from fastapi.testclient import TestClient

    from clauseos_api.app import create_app

    authz = ClauseOsAuthorizationService.build_default(
        revocation_checker=_DenyOneActorRevocationChecker(denied_actor_subject=MAKER)
    )
    app = create_app(authorization=authz)
    client = TestClient(app)

    resp = client.post("/v1/bundles", json={"scenario_id": SCENARIO_ID, "actor_id": MAKER})
    assert resp.status_code == 200, resp.text
    bundle_id = resp.json()["bundle_id"]

    resp = client.post(
        f"/v1/bundles/{bundle_id}/documents",
        json={"document_name": CSA_DOCUMENT_NAME, "content": "Threshold shall be USD 1,000.00", "actor_id": MAKER},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "AuthorizationDeniedError"

    # The document must not have been admitted.
    resp = client.get(f"/v1/bundles/{bundle_id}")
    assert resp.json()["documents"] == []


# ---------------------------------------------------------------------------
# SEC-05: maker/checker defense-in-depth via cassandre_security.maker_checker
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _AliasAwareDirectory(NaturalPersonDirectory):
    """A directory where two different actor_id strings resolve to the
    SAME natural person -- something ClauseOS's own domain-layer
    `SameActorError` check (a plain `actor_id == actor_id` string compare
    in `clauseos.domain.review.ReviewCase.record_checker_decision`) cannot
    catch, since the strings differ. Proves the
    `cassandre_security.maker_checker` call is genuine additional
    defense-in-depth, not a no-op duplicate."""

    aliases: dict[str, str]

    def natural_person_for(self, actor_subject: str) -> str:
        return self.aliases.get(actor_subject, actor_subject)


def _service_with_aliases() -> BundleService:
    directory = _AliasAwareDirectory(
        aliases={
            "maker.primary@sim.cassandre.test": "PERSON-1",
            "maker.alias@sim.cassandre.test": "PERSON-1",  # same human, second login
        }
    )
    return BundleService(InMemoryBundleRepository(), natural_person_directory=directory)


def test_alias_aware_self_approval_rejected_even_with_distinct_actor_ids():
    service = _service_with_aliases()
    bundle = service.create_bundle(scenario_id=SCENARIO_ID, actor_id="maker.primary@sim.cassandre.test")
    service.ingest_document(
        bundle_id=bundle.bundle_id,
        document_name=CSA_DOCUMENT_NAME,
        raw_bytes=csa_bytes(),
        actor_id="maker.primary@sim.cassandre.test",
    )
    service.ingest_document(
        bundle_id=bundle.bundle_id,
        document_name=ANNEX_DOCUMENT_NAME,
        raw_bytes=annex_bytes(),
        actor_id="maker.primary@sim.cassandre.test",
    )
    service.approve_manifest(
        bundle_id=bundle.bundle_id, manifest=build_manifest(incorporate_annex=True), actor_id="maker.primary@sim.cassandre.test"
    )
    service.analyze_bundle(bundle_id=bundle.bundle_id, actor_id="maker.primary@sim.cassandre.test")
    review_case = service.list_review_cases(bundle_id=bundle.bundle_id)[0]

    service.record_maker_decision(
        review_id=review_case.review_id, actor_id="maker.primary@sim.cassandre.test", decision=ReviewDecision.APPROVE
    )

    # Different actor_id string ("maker.alias@..." vs "maker.primary@...") --
    # ClauseOS's own domain check alone would allow this. The
    # cassandre_security.maker_checker alias-aware directory resolves both
    # to PERSON-1 and must still reject it.
    with pytest.raises(SameActorError):
        service.record_checker_decision(
            review_id=review_case.review_id, actor_id="maker.alias@sim.cassandre.test", decision=ReviewDecision.APPROVE
        )


def test_domain_layer_alone_would_have_allowed_the_alias_case():
    """Sanity check establishing the previous test is genuinely testing
    something new: with NO directory override (ClauseOS's existing,
    unmodified domain behavior), two different actor_id strings are
    accepted as distinct -- this is exactly the gap the SEC-05
    defense-in-depth call closes."""
    service = BundleService(InMemoryBundleRepository())  # no natural_person_directory override
    bundle = service.create_bundle(scenario_id=SCENARIO_ID, actor_id="maker.primary@sim.cassandre.test")
    service.ingest_document(
        bundle_id=bundle.bundle_id,
        document_name=CSA_DOCUMENT_NAME,
        raw_bytes=csa_bytes(),
        actor_id="maker.primary@sim.cassandre.test",
    )
    service.ingest_document(
        bundle_id=bundle.bundle_id,
        document_name=ANNEX_DOCUMENT_NAME,
        raw_bytes=annex_bytes(),
        actor_id="maker.primary@sim.cassandre.test",
    )
    service.approve_manifest(
        bundle_id=bundle.bundle_id, manifest=build_manifest(incorporate_annex=True), actor_id="maker.primary@sim.cassandre.test"
    )
    service.analyze_bundle(bundle_id=bundle.bundle_id, actor_id="maker.primary@sim.cassandre.test")
    review_case = service.list_review_cases(bundle_id=bundle.bundle_id)[0]
    service.record_maker_decision(
        review_id=review_case.review_id, actor_id="maker.primary@sim.cassandre.test", decision=ReviewDecision.APPROVE
    )
    # No directory override -> identity resolution -> different strings ->
    # allowed (this is pre-existing, unmodified ClauseOS behavior).
    result = service.record_checker_decision(
        review_id=review_case.review_id, actor_id="maker.alias@sim.cassandre.test", decision=ReviewDecision.APPROVE
    )
    assert result.checker_approval.actor_id == "maker.alias@sim.cassandre.test"


# ---------------------------------------------------------------------------
# SEC-08: envelope encryption round trip / tamper detection
# ---------------------------------------------------------------------------


def test_sealed_document_round_trips_and_tampered_ciphertext_fails_closed():
    service = BundleService(InMemoryBundleRepository())
    original = b"CONFIDENTIAL: Threshold shall be USD 1,000.00"

    envelope = service.seal_restricted_document_bytes(document_id="doc-1", raw_bytes=original)
    assert envelope.schema_version == "cassandre.envelope.v1"

    recovered = service.open_restricted_document_bytes(document_id="doc-1", envelope=envelope)
    assert recovered == original

    # Tamper with the ciphertext -- must fail closed, never return partial
    # or best-guess plaintext.
    tampered = dataclasses.replace(envelope, ciphertext_b64=envelope.ciphertext_b64[:-4] + "AAAA")
    with pytest.raises(EnvelopeTamperError):
        service.open_restricted_document_bytes(document_id="doc-1", envelope=tampered)

    # Opening under the WRONG document_id (context/AAD mismatch) must also
    # fail closed, identically to ciphertext tampering.
    with pytest.raises(EnvelopeTamperError):
        service.open_restricted_document_bytes(document_id="doc-WRONG", envelope=envelope)


def test_document_sealing_service_used_directly():
    sealing = DocumentSealingService()
    envelope = sealing.seal_document_bytes(raw_bytes=b"hello restricted world", document_id="doc-2")
    assert sealing.open_document_bytes(envelope, document_id="doc-2") == b"hello restricted world"


# ---------------------------------------------------------------------------
# SEC-14: revocation/security-fence blocks a stale-epoch mutation (real
# Postgres, via the existing clauseos_db_engine fixture -- skips cleanly
# without CASSANDRE_TEST_DATABASE_URL, same as every other DB-backed test).
# ---------------------------------------------------------------------------


def _full_flow_plain_service_up_to_review_required() -> tuple["InMemoryBundleRepository", BundleService, str]:
    """Runs the ordinary (unfenced) flow up to REVIEW_REQUIRED using a
    plain `BundleService` with no `fence_gateway` -- fencing in these
    tests is deliberately scoped to just the one critical-mutation call
    under test, via a second `BundleService` sharing the SAME repository
    (see callers below), so approving the other review cases doesn't
    require provisioning fence scopes this test isn't exercising."""
    repo = InMemoryBundleRepository()
    service = BundleService(repo)
    bundle = service.create_bundle(scenario_id=SCENARIO_ID, actor_id=MAKER)
    service.ingest_document(
        bundle_id=bundle.bundle_id, document_name=CSA_DOCUMENT_NAME, raw_bytes=csa_bytes(), actor_id=MAKER
    )
    service.ingest_document(
        bundle_id=bundle.bundle_id, document_name=ANNEX_DOCUMENT_NAME, raw_bytes=annex_bytes(), actor_id=MAKER
    )
    service.approve_manifest(bundle_id=bundle.bundle_id, manifest=build_manifest(incorporate_annex=True), actor_id=MAKER)
    service.analyze_bundle(bundle_id=bundle.bundle_id, actor_id=MAKER)
    return repo, service, bundle.bundle_id


def test_revocation_fence_blocks_stale_epoch_on_publish_against_real_postgres(clauseos_db_engine):
    FenceBase.metadata.create_all(clauseos_db_engine)
    gateway = SqlAlchemyRevocationFenceGateway(make_session_factory(clauseos_db_engine))

    repo, plain_service, bundle_id = _full_flow_plain_service_up_to_review_required()
    for review_case in plain_service.list_review_cases(bundle_id=bundle_id):
        plain_service.record_maker_decision(review_id=review_case.review_id, actor_id=MAKER, decision=ReviewDecision.APPROVE)
        plain_service.record_checker_decision(
            review_id=review_case.review_id, actor_id=CHECKER, decision=ReviewDecision.APPROVE
        )
    assert plain_service.get_bundle(bundle_id).status is BundleStatus.REVIEW_REQUIRED

    # A second BundleService instance, sharing the SAME repository, with
    # the fence gateway attached -- fencing is scoped to just the publish
    # call this test is exercising.
    fenced_service = BundleService(repo, fence_gateway=gateway)

    scope_key = clauseos_fence_scope_key(tenant_id=SCENARIO_ID, object_id=bundle_id)
    now = dt.datetime.now(dt.timezone.utc)
    gateway.ensure_scope_provisioned(scope_key, now=now)

    # A revocation event bumps the epoch from 0 to 1 (simulating e.g. an
    # actor's access being revoked between when they last observed the
    # fence and now).
    gateway.advance(scope_key=scope_key, new_state=RevocationState.REVOCATION_REQUESTED, now=now)

    from audit_protocol.keys import generate_keypair

    keypair = generate_keypair("clauseos-fence-test-key")

    # Presenting the STALE epoch (0) must genuinely block the mutation --
    # the bundle must not be published.
    with pytest.raises(RevocationFenceBlockedError):
        fenced_service.publish_policy_release(
            bundle_id=bundle_id, actor_id=CHECKER, keypair=keypair, presented_revocation_epoch=0
        )
    assert fenced_service.get_bundle(bundle_id).status is BundleStatus.REVIEW_REQUIRED

    # Presenting the CURRENT epoch (1) succeeds.
    release, envelope = fenced_service.publish_policy_release(
        bundle_id=bundle_id, actor_id=CHECKER, keypair=keypair, presented_revocation_epoch=1
    )
    assert len(release.terms) == 3
    assert fenced_service.get_bundle(bundle_id).status is BundleStatus.PUBLISHED


def test_revocation_fence_blocks_unprovisioned_scope_on_checker_decision(clauseos_db_engine):
    FenceBase.metadata.create_all(clauseos_db_engine)
    gateway = SqlAlchemyRevocationFenceGateway(make_session_factory(clauseos_db_engine))

    repo, plain_service, bundle_id = _full_flow_plain_service_up_to_review_required()
    review_case = plain_service.list_review_cases(bundle_id=bundle_id)[0]
    plain_service.record_maker_decision(review_id=review_case.review_id, actor_id=MAKER, decision=ReviewDecision.APPROVE)

    fenced_service = BundleService(repo, fence_gateway=gateway)

    # No fence scope has been provisioned for this review case yet -- an
    # unknown scope must block the critical mutation (spec: unknown
    # revocation state is never implicitly "current").
    with pytest.raises(RevocationFenceBlockedError):
        fenced_service.record_checker_decision(
            review_id=review_case.review_id, actor_id=CHECKER, decision=ReviewDecision.APPROVE
        )

    # Now provision it and retry with the correct (initial) epoch -- succeeds.
    scope_key = clauseos_fence_scope_key(tenant_id=SCENARIO_ID, object_id=review_case.review_id)
    gateway.ensure_scope_provisioned(scope_key, now=dt.datetime.now(dt.timezone.utc))
    result = fenced_service.record_checker_decision(
        review_id=review_case.review_id, actor_id=CHECKER, decision=ReviewDecision.APPROVE, presented_revocation_epoch=0
    )
    assert result.checker_approval.actor_id == CHECKER


# ---------------------------------------------------------------------------
# SEC-10 (lighter touch): canonical evidence v2 for ONE new evidence type --
# policy-release-publication evidence. Additive: does not touch
# audit_protocol or ClauseOS's existing signed-envelope V1 format.
# ---------------------------------------------------------------------------


def test_canonical_evidence_v2_for_policy_release_publication_round_trips_and_fails_closed_on_tamper():
    from audit_protocol.keys import generate_keypair
    from cassandre_security.canonical_evidence import EvidenceVerificationError

    from clauseos.application.evidence_v2 import (
        seal_policy_release_publication_evidence,
        verify_policy_release_publication_evidence,
    )
    from clauseos.domain.policy import PolicyRelease, PublishedTerm

    release = PolicyRelease(
        release_id="release-1",
        bundle_id="bundle-1",
        scenario_id=SCENARIO_ID,
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        terms=(
            PublishedTerm(
                concept="THRESHOLD",
                assertion_id="assert-1",
                evidence_content_hash="h" * 64,
                checker_actor_id=CHECKER,
                value_dict={"amount": "1000.00"},
            ),
        ),
    )
    keypair = generate_keypair("clauseos-evidence-v2-key")
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

    container = seal_policy_release_publication_evidence(
        release=release,
        tenant_id=SCENARIO_ID,
        signer_key_set_id=keypair.key_id,
        keypair=keypair,
        policy_epoch=0,
        valid_from=now,
        valid_until=now + dt.timedelta(days=1),
    )
    assert container.body.schema_version == "cassandre.evidence.v2"

    # Round trip: verifies cleanly within the validity window.
    verify_policy_release_publication_evidence(
        container, trusted_ed25519_public_key_b64=keypair.public_key_b64, now=now + dt.timedelta(hours=1)
    )

    # Tamper with the protected body's content digest -- must fail closed.
    tampered_body = dataclasses.replace(container.body, content_digest_hex="00" * 48)
    tampered = dataclasses.replace(container, body=tampered_body)
    with pytest.raises(EvidenceVerificationError):
        verify_policy_release_publication_evidence(
            tampered, trusted_ed25519_public_key_b64=keypair.public_key_b64, now=now + dt.timedelta(hours=1)
        )

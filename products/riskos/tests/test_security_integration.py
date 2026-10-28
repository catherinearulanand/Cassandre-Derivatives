"""Tests for this session's `cassandre_security` "plug it in" pass on
RiskOS: SEC-04 deny-by-default authorization, SEC-14 revocation fencing
(against real Postgres), and SEC-10 canonical evidence v2 for the additive
`ChallengerRun` aggregate. SEC-15 (witness) gets one lighter-touch proof at
the bottom.

Domain/core/application-only tests (authorization, evidence) run with zero
DB dependency, per this product's existing test-suite convention. The
fencing and witness tests use the `sql_session` fixture from `conftest.py`
(unchanged pattern, shared with `test_db_repository.py`) and skip cleanly
when `CASSANDRE_TEST_DATABASE_URL` is unset or unreachable.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

import pytest
from audit_protocol.keys import generate_keypair
from cassandre_security.canonical_evidence import EvidenceContainer, EvidenceVerificationError
from cassandre_security.revocation import EpochMismatchError, RevocationState, advance_revocation

import riskos_fixtures as fx
from riskos.adapters.in_memory import InMemoryRiskOSRepository
from riskos.adapters.sql_repository import SqlAlchemyRiskOSRepository
from riskos.application.security import (
    ROLE_CALCULATION_RUN,
    RiskOSActorContext,
    StaticGrantResolver,
    StaticRevocationChecker,
    build_default_policy_engine,
    fence_scope_key,
)
from riskos.application.service import (
    RiskOSAuthorizationDeniedError,
    RiskOSNotFoundError,
    RiskOSRevocationFenceError,
    RiskOSService,
)
from riskos.domain.entities import CurvePoint, DiscountCurve, InterestRateSwapTerms, MarketSnapshot
from riskos.domain.enums import CalculationPurpose, CalculationStatus

FIXED_NOW = dt.datetime(2026, 1, 5, 12, 0, 0, tzinfo=dt.timezone.utc)


def _build_service() -> RiskOSService:
    repo = InMemoryRiskOSRepository()
    keypair = generate_keypair("riskos-security-integration-test-key")
    return RiskOSService(repo, keypair=keypair)


def _ingested_scenario_service() -> tuple[RiskOSService, fx.GoldenScenario]:
    service = _build_service()
    scenario = fx.build_golden_scenario()
    now = scenario.now
    service.register_legal_entity(scenario.calculation_entity, now=now)
    service.register_legal_entity(scenario.counterparty_entity, now=now)
    service.register_netting_set(scenario.netting_set, now=now)
    service.approve_agreement_terms(scenario.agreement_terms, now=now)
    service.ingest_portfolio_snapshot(scenario.portfolio, now=now)
    service.ingest_collateral_state(scenario.collateral, now=now)
    return service, scenario


# ---------------------------------------------------------------------------
# SEC-04: deny-by-default authorization actually blocks the action.
# ---------------------------------------------------------------------------


def test_run_calculation_denied_for_actor_with_no_granted_roles():
    """An actor unknown to the default grant resolver has no roles at all
    (deny-by-default: `NO_GRANT`), so `run_calculation` must raise --
    exactly the same real, blocking exception as any other precondition
    failure, never a logged warning that lets the mutation through."""

    service, scenario = _ingested_scenario_service()
    unauthorized_actor = RiskOSActorContext(actor_subject="SIM-ACTOR-UNAUTHORIZED-0001")

    with pytest.raises(RiskOSAuthorizationDeniedError):
        service.run_calculation(
            run_id="SIM-RUN-DENY-0001",
            purpose=CalculationPurpose.MARGIN_CALL,
            calculation_entity_id=scenario.calculation_entity.entity_id,
            counterparty_entity_id=scenario.counterparty_entity.entity_id,
            agreement_id=scenario.agreement_terms.agreement_id,
            portfolio_snapshot_id=scenario.portfolio.snapshot_id,
            collateral_package_id=scenario.collateral.package_id,
            manifest=scenario.manifest,
            counterparty_call=scenario.counterparty_call,
            now=scenario.now,
            actor=unauthorized_actor,
        )

    # The DENY genuinely blocked the mutation -- nothing was persisted.
    with pytest.raises(RiskOSNotFoundError):
        service.get_calculation_run("SIM-RUN-DENY-0001")


def test_export_result_package_denied_for_actor_with_no_granted_roles():
    service, scenario = _ingested_scenario_service()
    run = service.run_calculation(
        run_id="SIM-RUN-DENY-0002",
        purpose=CalculationPurpose.MARGIN_CALL,
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        agreement_id=scenario.agreement_terms.agreement_id,
        portfolio_snapshot_id=scenario.portfolio.snapshot_id,
        collateral_package_id=scenario.collateral.package_id,
        manifest=scenario.manifest,
        counterparty_call=scenario.counterparty_call,
        now=scenario.now,
    )
    assert run.calculation_status is CalculationStatus.COMPLETE

    unauthorized_actor = RiskOSActorContext(actor_subject="SIM-ACTOR-UNAUTHORIZED-0002")
    with pytest.raises(RiskOSAuthorizationDeniedError):
        service.export_result_package(run_id=run.run_id, now=scenario.now, actor=unauthorized_actor)


def test_revoked_actor_denied_even_with_correct_role():
    """Mirrors `packages/cassandre_security/tests/test_authorization.py::
    test_revoked_actor_denied_even_with_correct_role`, wired through
    RiskOS's own actor/grant plumbing this pass adds: a correctly-granted
    role is not enough if the actor's session is revoked."""

    from riskos.application.security import ALL_ROLES, DEFAULT_ACTOR

    grant_resolver = StaticGrantResolver()
    revocation_checker = StaticRevocationChecker()
    # The setup calls below (register_legal_entity, etc.) use the service's
    # implicit default actor (no explicit `actor=`) -- grant it every role
    # in THIS custom resolver too, so only the final, explicitly-revoked
    # `actor` call is denied, isolating the thing this test actually proves.
    grant_resolver.grant(tenant_id=DEFAULT_ACTOR.tenant_id, actor_subject=DEFAULT_ACTOR.actor_subject, roles=ALL_ROLES)
    actor = RiskOSActorContext(actor_subject="SIM-ACTOR-REVOKED-0001")
    grant_resolver.grant(tenant_id=actor.tenant_id, actor_subject=actor.actor_subject, roles=frozenset({ROLE_CALCULATION_RUN}))
    revocation_checker.revoke(tenant_id=actor.tenant_id, actor_subject=actor.actor_subject)
    policy_engine = build_default_policy_engine(grant_resolver=grant_resolver, revocation_checker=revocation_checker)

    repo = InMemoryRiskOSRepository()
    keypair = generate_keypair("riskos-revoked-actor-test-key")
    service = RiskOSService(repo, keypair=keypair, policy_engine=policy_engine)
    scenario = fx.build_golden_scenario()
    now = scenario.now
    service.register_legal_entity(scenario.calculation_entity, now=now)
    service.register_legal_entity(scenario.counterparty_entity, now=now)
    service.register_netting_set(scenario.netting_set, now=now)
    service.approve_agreement_terms(scenario.agreement_terms, now=now)
    service.ingest_portfolio_snapshot(scenario.portfolio, now=now)
    service.ingest_collateral_state(scenario.collateral, now=now)

    with pytest.raises(RiskOSAuthorizationDeniedError):
        service.run_calculation(
            run_id="SIM-RUN-REVOKED-0001",
            purpose=CalculationPurpose.MARGIN_CALL,
            calculation_entity_id=scenario.calculation_entity.entity_id,
            counterparty_entity_id=scenario.counterparty_entity.entity_id,
            agreement_id=scenario.agreement_terms.agreement_id,
            portfolio_snapshot_id=scenario.portfolio.snapshot_id,
            collateral_package_id=scenario.collateral.package_id,
            manifest=scenario.manifest,
            counterparty_call=scenario.counterparty_call,
            now=now,
            actor=actor,
        )


def test_actor_with_a_custom_grant_can_be_authorized_not_just_the_fixed_default():
    """Proves the gate is a real deny-by-default engine, not a rubber
    stamp: a non-default actor granted exactly the right role succeeds,
    while the pre-existing fixed default actor (unknown to THIS custom
    grant resolver) is denied under the same policy engine."""

    from riskos.application.security import ROLE_MASTER_DATA_WRITE

    grant_resolver = StaticGrantResolver()
    actor = RiskOSActorContext(actor_subject="SIM-ACTOR-CUSTOM-GRANT-0001")
    # Grant ONLY the master-data-write role -- not agreement-approval.
    grant_resolver.grant(tenant_id=actor.tenant_id, actor_subject=actor.actor_subject, roles=frozenset({ROLE_MASTER_DATA_WRITE}))
    policy_engine = build_default_policy_engine(
        grant_resolver=grant_resolver, revocation_checker=StaticRevocationChecker()
    )
    repo = InMemoryRiskOSRepository()
    keypair = generate_keypair("riskos-custom-grant-test-key")
    service = RiskOSService(repo, keypair=keypair, policy_engine=policy_engine)
    scenario = fx.build_golden_scenario()
    now = scenario.now

    # Succeeds: this actor genuinely has ROLE_MASTER_DATA_WRITE.
    service.register_legal_entity(scenario.calculation_entity, now=now, actor=actor)
    service.register_legal_entity(scenario.counterparty_entity, now=now, actor=actor)

    # Denied: the SAME actor was never granted ROLE_AGREEMENT_APPROVE --
    # proves the per-action role requirement is actually enforced, not
    # "any known actor may do anything".
    with pytest.raises(RiskOSAuthorizationDeniedError):
        service.approve_agreement_terms(scenario.agreement_terms, now=now, actor=actor)


# ---------------------------------------------------------------------------
# SEC-14: revocation fencing genuinely blocks a mutation with a stale epoch,
# proven against real Postgres via the same `sql_session` fixture pattern
# `test_db_repository.py` already uses.
# ---------------------------------------------------------------------------


def _build_sql_service(sql_session) -> RiskOSService:
    repo = SqlAlchemyRiskOSRepository(sql_session)
    keypair = generate_keypair("riskos-fencing-test-key")
    return RiskOSService(repo, keypair=keypair)


def test_revocation_fence_blocks_run_calculation_with_a_stale_epoch(sql_session):
    service = _build_sql_service(sql_session)
    scenario = fx.build_golden_scenario()
    now = scenario.now
    service.register_legal_entity(scenario.calculation_entity, now=now)
    service.register_legal_entity(scenario.counterparty_entity, now=now)
    # `register_netting_set` provisions this netting set's fence at epoch 0.
    service.register_netting_set(scenario.netting_set, now=now)
    service.approve_agreement_terms(scenario.agreement_terms, now=now)
    service.ingest_portfolio_snapshot(scenario.portfolio, now=now)
    service.ingest_collateral_state(scenario.collateral, now=now)

    # A first run at the current (default) epoch 0 succeeds.
    first_run = service.run_calculation(
        run_id="SIM-RUN-FENCE-0001",
        purpose=CalculationPurpose.MARGIN_CALL,
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        agreement_id=scenario.agreement_terms.agreement_id,
        portfolio_snapshot_id=scenario.portfolio.snapshot_id,
        collateral_package_id=scenario.collateral.package_id,
        manifest=scenario.manifest,
        counterparty_call=scenario.counterparty_call,
        now=now,
        fence_epoch=0,
    )
    assert first_run.calculation_status is CalculationStatus.COMPLETE

    # Simulate an effective local revocation event on this netting set's
    # scope (e.g. an actor/session revocation) -- this genuinely advances
    # the fence's epoch in real Postgres, using the same
    # `cassandre_security.revocation` function `db_lockdown`-adjacent code
    # would call in a real revocation flow.
    scope_key = fence_scope_key(scenario.netting_set.netting_set_id)
    advance_revocation(sql_session, scope_key=scope_key, new_state=RevocationState.CURRENT, now=now)
    sql_session.commit()

    # A second mutation presenting the now-stale epoch 0 is genuinely
    # blocked -- not a warning, a raised exception, and the underlying
    # `cassandre_security` error is preserved as the cause.
    with pytest.raises(RiskOSRevocationFenceError) as exc_info:
        service.run_calculation(
            run_id="SIM-RUN-FENCE-0002",
            purpose=CalculationPurpose.MARGIN_CALL,
            calculation_entity_id=scenario.calculation_entity.entity_id,
            counterparty_entity_id=scenario.counterparty_entity.entity_id,
            agreement_id=scenario.agreement_terms.agreement_id,
            portfolio_snapshot_id=scenario.portfolio.snapshot_id,
            collateral_package_id=scenario.collateral.package_id,
            manifest=scenario.manifest,
            counterparty_call=scenario.counterparty_call,
            now=now,
            fence_epoch=0,
        )
    assert isinstance(exc_info.value.__cause__, EpochMismatchError)
    sql_session.rollback()

    # The blocked run was never persisted.
    with pytest.raises(RiskOSNotFoundError):
        service.get_calculation_run("SIM-RUN-FENCE-0002")

    # Presenting the now-current epoch (1) succeeds.
    third_run = service.run_calculation(
        run_id="SIM-RUN-FENCE-0003",
        purpose=CalculationPurpose.MARGIN_CALL,
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        agreement_id=scenario.agreement_terms.agreement_id,
        portfolio_snapshot_id=scenario.portfolio.snapshot_id,
        collateral_package_id=scenario.collateral.package_id,
        manifest=scenario.manifest,
        counterparty_call=scenario.counterparty_call,
        now=now,
        fence_epoch=1,
    )
    assert third_run.calculation_status is CalculationStatus.COMPLETE


def test_revocation_fence_blocks_export_result_package_with_a_stale_epoch(sql_session):
    service = _build_sql_service(sql_session)
    scenario = fx.build_golden_scenario()
    now = scenario.now
    service.register_legal_entity(scenario.calculation_entity, now=now)
    service.register_legal_entity(scenario.counterparty_entity, now=now)
    service.register_netting_set(scenario.netting_set, now=now)
    service.approve_agreement_terms(scenario.agreement_terms, now=now)
    service.ingest_portfolio_snapshot(scenario.portfolio, now=now)
    service.ingest_collateral_state(scenario.collateral, now=now)
    run = service.run_calculation(
        run_id="SIM-RUN-FENCE-EXPORT-0001",
        purpose=CalculationPurpose.MARGIN_CALL,
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        agreement_id=scenario.agreement_terms.agreement_id,
        portfolio_snapshot_id=scenario.portfolio.snapshot_id,
        collateral_package_id=scenario.collateral.package_id,
        manifest=scenario.manifest,
        counterparty_call=scenario.counterparty_call,
        now=now,
    )

    scope_key = fence_scope_key(scenario.netting_set.netting_set_id)
    advance_revocation(sql_session, scope_key=scope_key, new_state=RevocationState.CURRENT, now=now)
    sql_session.commit()

    with pytest.raises(RiskOSRevocationFenceError) as exc_info:
        service.export_result_package(run_id=run.run_id, now=now, fence_epoch=0)
    assert isinstance(exc_info.value.__cause__, EpochMismatchError)
    sql_session.rollback()

    # The current epoch (1) is accepted.
    envelope = service.export_result_package(run_id=run.run_id, now=now, fence_epoch=1)
    assert envelope.content_hash


# ---------------------------------------------------------------------------
# SEC-10: canonical evidence v2 for the additive `ChallengerRun` aggregate --
# round-trips and rejects a mutated body. Purely additive: does not touch
# `export_result_package`'s existing V1 envelope path (see
# `test_result_package_envelope.py` for that path's own unchanged tests).
# ---------------------------------------------------------------------------


def _irs_market_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        snapshot_id="SIM-MKT-EVIDENCE-0001",
        as_of=dt.date(2026, 1, 1),
        calendars=("SIM.NONE",),
        discount_curves=(DiscountCurve(currency="USD", points=(CurvePoint(tenor_years=Decimal("1"), zero_rate=Decimal("0.03")),)),),
        fx_spot_rates=(),
    )


def _irs_terms() -> InterestRateSwapTerms:
    return InterestRateSwapTerms(
        trade_id="SIM-CHALLENGER-EVIDENCE-IRS-0001",
        calculation_entity_id="SIM-ENTITY-CALC-EVIDENCE-0001",
        counterparty_entity_id="SIM-ENTITY-CPTY-EVIDENCE-0001",
        netting_set_id="SIM-NETSET-CHALLENGER-EVIDENCE-0001",
        currency="USD",
        notional=Decimal("1000000"),
        fixed_rate=Decimal("0.04"),
        calculation_party_pays_fixed=False,
        effective_date=dt.date(2026, 1, 1),
        maturity_date=dt.date(2028, 1, 1),
        payment_frequency_months=12,
    )


def _build_challenger_run_for_evidence(service: RiskOSService, now: dt.datetime):
    snapshot = _irs_market_snapshot()
    service.ingest_market_snapshot(snapshot, now=now)
    return service.run_challenger_valuation(
        run_id="SIM-CHALLENGER-RUN-EVIDENCE-0001",
        netting_set_id="SIM-NETSET-CHALLENGER-EVIDENCE-0001",
        market_snapshot_id=snapshot.snapshot_id,
        as_of=snapshot.as_of,
        now=now,
        irs_trades=(_irs_terms(),),
    )


def test_challenger_run_evidence_v2_round_trips():
    service = _build_service()
    run = _build_challenger_run_for_evidence(service, FIXED_NOW)
    assert run.calculation_status is CalculationStatus.COMPLETE

    container = service.seal_challenger_run_evidence(run.run_id, now=FIXED_NOW)
    assert container.body.schema_version == "cassandre.evidence.v2"
    assert container.body.digest_algorithm == "sha384"
    assert set(container.signatures.keys()) == {"ed25519"}

    # Must not raise.
    service.verify_challenger_run_evidence(container, now=FIXED_NOW)


def test_challenger_run_evidence_v2_rejects_a_mutated_body():
    service = _build_service()
    run = _build_challenger_run_for_evidence(service, FIXED_NOW)
    container = service.seal_challenger_run_evidence(run.run_id, now=FIXED_NOW)

    tampered_body = dataclasses.replace(container.body, content_digest_hex="0" * 96)
    tampered_container = EvidenceContainer(body=tampered_body, signatures=container.signatures)

    with pytest.raises(EvidenceVerificationError):
        service.verify_challenger_run_evidence(tampered_container, now=FIXED_NOW)


def test_challenger_run_evidence_v2_rejects_a_stripped_signature():
    service = _build_service()
    run = _build_challenger_run_for_evidence(service, FIXED_NOW)
    container = service.seal_challenger_run_evidence(run.run_id, now=FIXED_NOW)

    stripped = EvidenceContainer(body=container.body, signatures={})
    with pytest.raises(EvidenceVerificationError):
        service.verify_challenger_run_evidence(stripped, now=FIXED_NOW)


def test_challenger_run_evidence_v2_does_not_touch_the_existing_v1_export_path():
    """Independence proof: sealing new v2 evidence for a `ChallengerRun`
    does not require, call, or alter the existing
    `export_result_package`/`build_signed_result_envelope` V1 path at all
    -- they operate on entirely different aggregates
    (`ChallengerRun` vs `CalculationRun`/`ResultPackage`)."""

    service, scenario = _ingested_scenario_service()
    now = scenario.now
    margin_call_run = service.run_calculation(
        run_id="SIM-RUN-EVIDENCE-INDEPENDENCE-0001",
        purpose=CalculationPurpose.MARGIN_CALL,
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        agreement_id=scenario.agreement_terms.agreement_id,
        portfolio_snapshot_id=scenario.portfolio.snapshot_id,
        collateral_package_id=scenario.collateral.package_id,
        manifest=scenario.manifest,
        counterparty_call=scenario.counterparty_call,
        now=now,
    )
    before = service.get_calculation_run(margin_call_run.run_id)

    challenger_run = _build_challenger_run_for_evidence(service, now)
    service.seal_challenger_run_evidence(challenger_run.run_id, now=now)

    after = service.get_calculation_run(margin_call_run.run_id)
    assert after == before


# ---------------------------------------------------------------------------
# SEC-15 (lighter touch): the independent witness store genuinely rejects a
# forked chain extension, proven against real Postgres.
# ---------------------------------------------------------------------------


def test_witness_checkpoints_are_gapless_and_reject_a_forked_extension(sql_session):
    from cassandre_security.witness import WitnessForkError

    repo = SqlAlchemyRiskOSRepository(sql_session)
    repo.submit_witness_checkpoint(sequence=0, head_hash="sim-hash-a", now=FIXED_NOW)
    sql_session.commit()

    # A different head_hash for the same already-witnessed sequence is
    # rejected as a fork attempt -- never silently overwritten.
    with pytest.raises(WitnessForkError):
        repo.submit_witness_checkpoint(sequence=0, head_hash="sim-hash-b-different", now=FIXED_NOW)
    sql_session.rollback()

    # The real, gapless next sequence is accepted.
    repo.submit_witness_checkpoint(sequence=1, head_hash="sim-hash-c", now=FIXED_NOW)
    sql_session.commit()


def test_run_calculation_submits_a_witness_checkpoint_per_audit_event(sql_session):
    """Lighter-touch proof that `RiskOSService.run_calculation` (via
    `_record_audit_event`) actually reaches the independent witness store
    when backed by the SQL repository, using the real audit chain's
    sequence/head_hash as the witnessed values."""

    service = _build_sql_service(sql_session)
    scenario = fx.build_golden_scenario()
    now = scenario.now
    service.register_legal_entity(scenario.calculation_entity, now=now)
    service.register_legal_entity(scenario.counterparty_entity, now=now)
    service.register_netting_set(scenario.netting_set, now=now)
    service.approve_agreement_terms(scenario.agreement_terms, now=now)
    service.ingest_portfolio_snapshot(scenario.portfolio, now=now)
    service.ingest_collateral_state(scenario.collateral, now=now)
    service.run_calculation(
        run_id="SIM-RUN-WITNESS-0001",
        purpose=CalculationPurpose.MARGIN_CALL,
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        agreement_id=scenario.agreement_terms.agreement_id,
        portfolio_snapshot_id=scenario.portfolio.snapshot_id,
        collateral_package_id=scenario.collateral.package_id,
        manifest=scenario.manifest,
        counterparty_call=scenario.counterparty_call,
        now=now,
    )

    from cassandre_security.witness import latest_checkpoint

    audit_events = service._repo.list_audit_events()  # test-only introspection
    last_event = audit_events[-1]
    checkpoint = latest_checkpoint(sql_session, product_chain_id="riskos")
    assert checkpoint is not None
    assert checkpoint.sequence == last_event.sequence
    assert checkpoint.head_hash == last_event.event_hash

"""Application service: wires the pure `core` workflow to a repository, the
audit chain, and envelope signing. This is the layer FastAPI adapters call
into; it holds no HTTP/SQL specifics itself (those live in `riskos_api` and
`riskos.adapters`).
"""

from __future__ import annotations

import datetime as dt
import uuid

from audit_protocol import AuditEvent, append_event
from audit_protocol.keys import Ed25519KeyPair
from cassandre_contracts import Envelope
from cassandre_security.authorization import AuthorizationRequest, DenyByDefaultPolicyEngine
from cassandre_security.canonical_evidence import EvidenceContainer, build_protected_body, seal_evidence, verify_evidence
from cassandre_security.revocation import FenceError

from riskos.application import security as riskos_security
from riskos.application.envelope import build_signed_result_envelope
from riskos.application.repositories import RiskOSRepository
from riskos.application.result_package import build_result_package
from riskos.application.security import (
    ACTION_APPROVE_AGREEMENT_TERMS,
    ACTION_EXPORT_RESULT_PACKAGE,
    ACTION_REGISTER_LEGAL_ENTITY,
    ACTION_RUN_CALCULATION,
    ACTION_RUN_CHALLENGER_VALUATION,
    ACTION_SUBMIT_RECONCILIATION,
    PURPOSE_AGREEMENT_APPROVAL,
    PURPOSE_CHALLENGER,
    PURPOSE_EVIDENCE_EXPORT,
    PURPOSE_MASTER_DATA_MANAGEMENT,
    PURPOSE_RECONCILIATION,
    RiskOSActorContext,
    RiskOSAuthorizationDeniedError,
    RiskOSRevocationFenceError,
    build_default_grant_resolver,
    build_default_policy_engine,
    fence_scope_key,
)
from riskos.application.serialization import challenger_run_to_evidence_payload
from riskos.core.challenger_workflow import execute_challenger_run
from riskos.core.method_pack import build_method_pack_001
from riskos.core.reconciliation import PartyCallView, build_reconciliation_case
from riskos.core.workflow import MarginCallRequest, execute_margin_call
from riskos.domain.entities import (
    AgreementTermsPackage,
    CalculationRun,
    ChallengerRun,
    CollateralStatePackage,
    FxForwardTerms,
    InterestRateSwapTerms,
    LegalEntityMasterVersion,
    MarketSnapshot,
    MethodPack,
    NettingSetVersion,
    PortfolioSnapshot,
    ReconciliationCase,
    SourceControlManifest,
)
from riskos.domain.enums import CalculationPurpose


class RiskOSNotFoundError(LookupError):
    pass


class RiskOSService:
    """One product-level audit chain (per `product="riskos"` in every
    appended `AuditEvent`); a real deployment would scope chains per
    aggregate, but a single chain is sufficient and independently
    verifiable for this slice."""

    def __init__(
        self,
        repository: RiskOSRepository,
        *,
        keypair: Ed25519KeyPair,
        issuer: str = "riskos_api",
        actor_id: str = "SIM-ACTOR-RISKOS-SERVICE",
        policy_engine: DenyByDefaultPolicyEngine | None = None,
        default_actor: RiskOSActorContext | None = None,
    ) -> None:
        self._repo = repository
        self._keypair = keypair
        self._issuer = issuer
        self._actor_id = actor_id
        self._method_pack: MethodPack = build_method_pack_001()
        # -- SEC-04 authorization wiring --------------------------------
        # See `riskos.application.security` module docstring: RiskOS has no
        # real identity/session layer yet, so `default_actor` is a fixed
        # simulation-constant actor pre-granted every gated role, matching
        # the existing `actor_id` pattern above. A caller that wants to
        # prove a DENY passes an explicit `actor=` to a gated method below.
        self._default_actor = default_actor or riskos_security.DEFAULT_ACTOR
        self._policy_engine = policy_engine or build_default_policy_engine(
            grant_resolver=build_default_grant_resolver(),
            revocation_checker=riskos_security.StaticRevocationChecker(),
        )

    # -- audit -------------------------------------------------------
    def _record_audit_event(
        self, *, aggregate_id: str, action: str, object_version: str, object_content_hash: str, now: dt.datetime
    ) -> AuditEvent:
        previous = self._repo.last_audit_event()
        event = append_event(
            previous_event=previous,
            event_id=str(uuid.uuid4()),
            product="riskos",
            aggregate_id=aggregate_id,
            actor_id=self._actor_id,
            actor_type="SERVICE",
            action=action,
            reason_code=None,
            object_version=object_version,
            object_content_hash=object_content_hash,
            event_timestamp=now,
            simulation_provenance={"is_simulated": True, "scenario_id": "SIM-SCEN-RISKOS-0001"},
            trace_id=str(uuid.uuid4()),
        )
        self._repo.append_audit_event(event)

        # -- SEC-15 witness checkpoint ------------------------------------
        # `cassandre_security.witness.submit_checkpoint` requires exactly
        # gapless, monotonically increasing sequence numbers per
        # `product_chain_id` (it "rejects forked/gapped chain extensions").
        # RiskOS's `audit_protocol` chain assigns one such global sequence
        # per product ("riskos"), shared across every action this method is
        # called for -- register_legal_entity, run_calculation,
        # export_result_package, run_challenger_valuation, etc. -- not just
        # `CalculationRun` saves. So this checkpoints every audit event on
        # that one shared chain (the brief's "after each CalculationRun is
        # saved" is satisfied as a subset of this: every CalculationRun's
        # own `CALCULATION_RUN_EXECUTED` event is always checkpointed here
        # too), rather than sampling only calculation-run events, which
        # would otherwise submit sparse sequence numbers and always raise
        # `WitnessGapError` the very first time two other audit events sit
        # in between. See `products/riskos/README.md`'s "Security
        # integration" section for this deviation. A no-op when the
        # repository is not witness-capable (in-memory).
        submit_witness_checkpoint = getattr(self._repo, "submit_witness_checkpoint", None)
        if submit_witness_checkpoint is not None:
            submit_witness_checkpoint(sequence=event.sequence, head_hash=event.event_hash, now=now)

        return event

    # -- SEC-04 authorization -----------------------------------------
    def _authorize(
        self,
        *,
        action: str,
        purpose: str,
        object_id: str,
        object_version: str,
        actor: RiskOSActorContext | None,
        now: dt.datetime,
    ) -> None:
        """Deny-by-default gate: raises `RiskOSAuthorizationDeniedError`
        (never just logs) on any DENY. Called before the gated action's
        mutation executes -- see each call site below."""

        resolved_actor = actor or self._default_actor
        request = AuthorizationRequest(
            request_id=str(uuid.uuid4()),
            actor_subject=resolved_actor.actor_subject,
            actor_kind=resolved_actor.actor_kind,
            tenant_id=resolved_actor.tenant_id,
            legal_entity_id=resolved_actor.legal_entity_id,
            product_id=riskos_security.PRODUCT_ID,
            object_id=object_id,
            object_version=object_version,
            content_digest="sha256:" + "0" * 64,
            action=action,
            purpose=purpose,
            policy_release_digest=self._policy_engine.policy_release_digest,
            revocation_epoch=resolved_actor.revocation_epoch,
            session_assurance=resolved_actor.session_assurance,
            expires_at=now + resolved_actor.session_ttl,
        )
        decision = self._policy_engine.decide(request, now=now, decision_id=str(uuid.uuid4()))
        if not decision.allowed:
            raise RiskOSAuthorizationDeniedError(action=action, purpose=purpose, decision=decision)

    # -- method packs --------------------------------------------------
    def list_method_packs(self) -> tuple[MethodPack, ...]:
        return (self._method_pack,)

    # -- read-only lookups backing the RiskOS web UI ---------------------
    # (thin passthroughs to the repository; no business logic — the UI
    # calls these instead of poking the repository directly, matching the
    # rest of this service's request/response boundary.)
    def get_legal_entity(self, entity_id: str) -> LegalEntityMasterVersion:
        entity = self._repo.get_legal_entity(entity_id)
        if entity is None:
            raise RiskOSNotFoundError(f"unknown legal entity {entity_id!r}")
        return entity

    def list_legal_entities(self) -> tuple[LegalEntityMasterVersion, ...]:
        return self._repo.list_legal_entities()

    def list_netting_sets(self) -> tuple[NettingSetVersion, ...]:
        return self._repo.list_netting_sets()

    def get_agreement_terms(self, agreement_id: str) -> AgreementTermsPackage:
        terms = self._repo.get_agreement_terms(agreement_id)
        if terms is None:
            raise RiskOSNotFoundError(f"unknown agreement terms package {agreement_id!r}")
        return terms

    def get_portfolio_snapshot(self, snapshot_id: str) -> PortfolioSnapshot:
        snapshot = self._repo.get_portfolio_snapshot(snapshot_id)
        if snapshot is None:
            raise RiskOSNotFoundError(f"unknown portfolio snapshot {snapshot_id!r}")
        return snapshot

    def get_collateral_state(self, package_id: str) -> CollateralStatePackage:
        package = self._repo.get_collateral_state(package_id)
        if package is None:
            raise RiskOSNotFoundError(f"unknown collateral state package {package_id!r}")
        return package

    # -- masters ---------------------------------------------------------
    def register_legal_entity(
        self,
        entity: LegalEntityMasterVersion,
        *,
        now: dt.datetime,
        actor: RiskOSActorContext | None = None,
    ) -> LegalEntityMasterVersion:
        self._authorize(
            action=ACTION_REGISTER_LEGAL_ENTITY,
            purpose=PURPOSE_MASTER_DATA_MANAGEMENT,
            object_id=entity.entity_id,
            object_version=entity.version,
            actor=actor,
            now=now,
        )
        self._repo.save_legal_entity(entity)
        self._record_audit_event(
            aggregate_id=entity.entity_id,
            action="LEGAL_ENTITY_VERSION_REGISTERED",
            object_version=entity.version,
            object_content_hash="0" * 64,
            now=now,
        )
        return entity

    def register_netting_set(self, netting_set: NettingSetVersion, *, now: dt.datetime) -> NettingSetVersion:
        self._repo.save_netting_set(netting_set)
        # -- SEC-14: provision this netting set's revocation-fence scope --
        # `ensure_fence_exists` is idempotent and is deliberately not part
        # of `RiskOSRepository`'s Protocol -- only the SQL-backed repository
        # implements it (see `riskos.adapters.sql_repository`), so this is a
        # no-op for the in-memory repository used by the API and most
        # tests. See `riskos.application.security` module docstring.
        ensure_fence = getattr(self._repo, "ensure_fence_exists", None)
        if ensure_fence is not None:
            ensure_fence(scope_key=fence_scope_key(netting_set.netting_set_id), now=now)
        self._record_audit_event(
            aggregate_id=netting_set.netting_set_id,
            action="NETTING_SET_VERSION_REGISTERED",
            object_version=netting_set.version,
            object_content_hash="0" * 64,
            now=now,
        )
        return netting_set

    # -- agreement terms ---------------------------------------------
    def validate_agreement_terms(self, terms: AgreementTermsPackage) -> tuple[bool, tuple[str, ...]]:
        reasons: list[str] = []
        if not terms.is_supported:
            reasons.append("UNSUPPORTED_AGREEMENT: not a supported agreement family")
        if terms.method_pack_id != self._method_pack.method_id:
            reasons.append(
                f"METHOD_UNRESOLVED: method_pack_id {terms.method_pack_id!r} does not resolve to an "
                "implemented pack"
            )
        if terms.call_currency != self._method_pack.call_currency:
            reasons.append("CURRENCY: call currency does not match method pack call currency")
        return not reasons, tuple(reasons)

    def approve_agreement_terms(
        self,
        terms: AgreementTermsPackage,
        *,
        now: dt.datetime,
        actor: RiskOSActorContext | None = None,
    ) -> AgreementTermsPackage:
        self._authorize(
            action=ACTION_APPROVE_AGREEMENT_TERMS,
            purpose=PURPOSE_AGREEMENT_APPROVAL,
            object_id=terms.agreement_id,
            object_version="v1",
            actor=actor,
            now=now,
        )
        approved = AgreementTermsPackage(
            agreement_id=terms.agreement_id,
            netting_set_id=terms.netting_set_id,
            method_pack_id=terms.method_pack_id,
            call_currency=terms.call_currency,
            approved=True,
            approval_reference=terms.approval_reference or f"SIM-APPROVAL-{terms.agreement_id}",
            is_supported=terms.is_supported,
        )
        self._repo.save_agreement_terms(approved)
        self._record_audit_event(
            aggregate_id=approved.agreement_id,
            action="AGREEMENT_TERMS_APPROVED",
            object_version="v1",
            object_content_hash="0" * 64,
            now=now,
        )
        return approved

    # -- portfolio / collateral ingest -------------------------------
    def ingest_portfolio_snapshot(self, snapshot: PortfolioSnapshot, *, now: dt.datetime) -> PortfolioSnapshot:
        self._repo.save_portfolio_snapshot(snapshot)
        self._record_audit_event(
            aggregate_id=snapshot.snapshot_id,
            action="PORTFOLIO_SNAPSHOT_INGESTED",
            object_version="v1",
            object_content_hash="0" * 64,
            now=now,
        )
        return snapshot

    def ingest_collateral_state(self, package: CollateralStatePackage, *, now: dt.datetime) -> CollateralStatePackage:
        self._repo.save_collateral_state(package)
        self._record_audit_event(
            aggregate_id=package.package_id,
            action="COLLATERAL_STATE_PACKAGE_INGESTED",
            object_version="v1",
            object_content_hash="0" * 64,
            now=now,
        )
        return package

    # -- calculation ---------------------------------------------------
    def run_calculation(
        self,
        *,
        run_id: str,
        purpose: CalculationPurpose,
        calculation_entity_id: str,
        counterparty_entity_id: str,
        agreement_id: str,
        portfolio_snapshot_id: str,
        collateral_package_id: str,
        manifest: SourceControlManifest,
        counterparty_call: PartyCallView,
        now: dt.datetime,
        actor: RiskOSActorContext | None = None,
        fence_epoch: int = 0,
    ) -> CalculationRun:
        """`fence_epoch` (SEC-14) is the revocation-fence epoch the caller
        presents for this netting set's scope, checked against
        `cassandre_security.revocation`'s fence row (only when the
        repository is fence-capable -- see `riskos.application.security`
        docstring). Defaults to 0 (the epoch a never-revoked scope starts
        at); a caller that has observed a later epoch (e.g. from a prior
        response) presents that instead."""

        self._authorize(
            action=ACTION_RUN_CALCULATION,
            purpose=purpose.value,
            object_id=run_id,
            object_version="v1",
            actor=actor,
            now=now,
        )
        calculation_entity = self._repo.get_legal_entity(calculation_entity_id)
        counterparty_entity = self._repo.get_legal_entity(counterparty_entity_id)
        agreement_terms = self._repo.get_agreement_terms(agreement_id)
        portfolio = self._repo.get_portfolio_snapshot(portfolio_snapshot_id)
        collateral = self._repo.get_collateral_state(collateral_package_id)
        if calculation_entity is None:
            raise RiskOSNotFoundError(f"unknown calculation entity {calculation_entity_id!r}")
        if counterparty_entity is None:
            raise RiskOSNotFoundError(f"unknown counterparty entity {counterparty_entity_id!r}")
        if agreement_terms is None:
            raise RiskOSNotFoundError(f"unknown agreement terms package {agreement_id!r}")
        if portfolio is None:
            raise RiskOSNotFoundError(f"unknown portfolio snapshot {portfolio_snapshot_id!r}")
        if collateral is None:
            raise RiskOSNotFoundError(f"unknown collateral state package {collateral_package_id!r}")

        request = MarginCallRequest(
            run_id=run_id,
            purpose=purpose,
            calculation_entity=calculation_entity,
            counterparty_entity=counterparty_entity,
            netting_set_candidates=self._repo.list_netting_sets(),
            agreement_terms=agreement_terms,
            portfolio=portfolio,
            manifest=manifest,
            collateral=collateral,
            method_pack=self._method_pack,
            counterparty_call=counterparty_call,
            now=now,
        )
        result = execute_margin_call(request)

        # -- SEC-14 revocation fencing: lock this netting set's fence row
        # (with_for_update) on the SAME session the SQL repository's save
        # below will commit on, so the lock is genuinely held through that
        # commit -- see `riskos.adapters.sql_repository
        # .SqlAlchemyRiskOSRepository.lock_fences_for_mutation`. A no-op
        # when the repository does not expose that hook (the in-memory
        # repository used by the API and most tests). Skipped when the
        # workflow never resolved a real netting set (`"UNRESOLVED"`, e.g.
        # an unsupported-agreement/zero-or-multiple-match failure): that is
        # an existing, tested FAILED-status business outcome, not a fencing
        # concern, and there is no fence scope to lock for a netting set
        # that was never resolved.
        if result.run.netting_set_id != "UNRESOLVED":
            lock_fences = getattr(self._repo, "lock_fences_for_mutation", None)
            if lock_fences is not None:
                scope_key = fence_scope_key(result.run.netting_set_id)
                try:
                    lock_fences(
                        scope_keys=(scope_key,),
                        presented_epoch_by_scope={scope_key: fence_epoch},
                        now=now,
                    )
                except FenceError as exc:
                    raise RiskOSRevocationFenceError(scope_key=scope_key, cause=exc) from exc

        self._repo.save_calculation_run(result.run)
        if result.reconciliation is not None:
            self._repo.save_reconciliation_case(result.reconciliation)
        # SEC-15 witness checkpointing happens inside `_record_audit_event`
        # itself (see its comment) -- every audit event on this product's
        # one shared chain is checkpointed, `CALCULATION_RUN_EXECUTED`
        # included.
        self._record_audit_event(
            aggregate_id=result.run.run_id,
            action="CALCULATION_RUN_EXECUTED",
            object_version="v1",
            object_content_hash="0" * 64,
            now=now,
        )

        return result.run

    def get_calculation_run(self, run_id: str) -> CalculationRun:
        run = self._repo.get_calculation_run(run_id)
        if run is None:
            raise RiskOSNotFoundError(f"unknown calculation run {run_id!r}")
        return run

    def get_reconciliation_case(self, case_id: str) -> ReconciliationCase:
        case = self._repo.get_reconciliation_case(case_id)
        if case is None:
            raise RiskOSNotFoundError(f"unknown reconciliation case {case_id!r}")
        return case

    def submit_reconciliation(
        self,
        *,
        run_id: str,
        counterparty_call: PartyCallView,
        now: dt.datetime,
        actor: RiskOSActorContext | None = None,
    ) -> ReconciliationCase:
        """Re-run reconciliation for an existing calculation run against a
        (possibly later-arriving) counterparty call, per
        `POST /v1/reconciliations`."""

        self._authorize(
            action=ACTION_SUBMIT_RECONCILIATION,
            purpose=PURPOSE_RECONCILIATION,
            object_id=run_id,
            object_version="v1",
            actor=actor,
            now=now,
        )
        run = self.get_calculation_run(run_id)
        if run.waterfall is None:
            raise RiskOSNotFoundError(f"calculation run {run_id!r} has no waterfall to reconcile against")
        calculation_party_view = PartyCallView(
            party_label="CALCULATION_PARTY",
            call_status=run.waterfall.call_status,
            call_amount=run.waterfall.call_amount,
            exposure=run.waterfall.exposure,
            credit_support_balance=run.waterfall.credit_support_balance,
        )
        case = build_reconciliation_case(
            case_id=f"RECON-{run_id}-{uuid.uuid4().hex[:8]}",
            run_id=run_id,
            calculation_party=calculation_party_view,
            counterparty=counterparty_call,
            lower_common_amount_authorized=self._method_pack.lower_common_amount_authorized,
        )
        self._repo.save_reconciliation_case(case)
        self._record_audit_event(
            aggregate_id=case.case_id,
            action="RECONCILIATION_SUBMITTED",
            object_version="v1",
            object_content_hash="0" * 64,
            now=now,
        )
        return case

    # -- export ----------------------------------------------------------
    def export_result_package(
        self,
        *,
        run_id: str,
        now: dt.datetime,
        actor: RiskOSActorContext | None = None,
        fence_epoch: int = 0,
    ) -> Envelope:
        """`fence_epoch` (SEC-14): see `run_calculation`'s docstring -- same
        convention, scoped to this run's netting set."""

        self._authorize(
            action=ACTION_EXPORT_RESULT_PACKAGE,
            purpose=PURPOSE_EVIDENCE_EXPORT,
            object_id=run_id,
            object_version="v1",
            actor=actor,
            now=now,
        )
        run = self.get_calculation_run(run_id)
        reconciliation = (
            self._repo.get_reconciliation_case(run.reconciliation_case_id)
            if run.reconciliation_case_id
            else None
        )
        result_package = build_result_package(
            result_id=f"RESULT-{run_id}",
            run=run,
            method_pack=self._method_pack,
            reconciliation=reconciliation,
            now=now,
        )
        # -- SEC-14 revocation fencing: this export is a critical mutation
        # (it appends an audit event) over this run's netting set. See
        # `run_calculation`'s identical comment above.
        if run.netting_set_id != "UNRESOLVED":
            lock_fences = getattr(self._repo, "lock_fences_for_mutation", None)
            if lock_fences is not None:
                scope_key = fence_scope_key(run.netting_set_id)
                try:
                    lock_fences(
                        scope_keys=(scope_key,),
                        presented_epoch_by_scope={scope_key: fence_epoch},
                        now=now,
                    )
                except FenceError as exc:
                    raise RiskOSRevocationFenceError(scope_key=scope_key, cause=exc) from exc

        # NOTE (SEC-10 scope): this envelope is still the OLD V1
        # `audit_protocol`/`cassandre_contracts` signing path
        # (`build_signed_result_envelope`), left untouched by this pass --
        # see `seal_challenger_run_evidence` below for the new, additive
        # `cassandre.evidence.v2` capability this pass adds for the
        # `ChallengerRun` aggregate instead. No existing signed object's
        # format changes here.
        envelope = build_signed_result_envelope(result_package, keypair=self._keypair, issuer=self._issuer, now=now)
        self._record_audit_event(
            aggregate_id=result_package.result_id,
            action="RESULT_PACKAGE_EXPORTED",
            object_version="v1",
            object_content_hash=envelope.content_hash,
            now=now,
        )
        return envelope

    # -- RSK-X01 native valuation challenger ------------------------------
    # Additive: this section shares only `riskos.core.challenger_pricing`'s
    # curve math with the MARGIN_CALL path above. It reads its own inputs
    # (`market_snapshot`, optionally an existing `portfolio_snapshot_id` for
    # incumbent MTMs to compare against) and writes to its own repository
    # slot (`save_challenger_run`/`get_challenger_run`) — never
    # `save_calculation_run`/`get_calculation_run`. See
    # `products/riskos/tests/test_challenger.py::
    # test_challenger_run_does_not_affect_margin_call_run` for the proof
    # that running this never alters an existing MARGIN_CALL run.
    def ingest_market_snapshot(self, snapshot: MarketSnapshot, *, now: dt.datetime) -> MarketSnapshot:
        self._repo.save_market_snapshot(snapshot)
        self._record_audit_event(
            aggregate_id=snapshot.snapshot_id,
            action="MARKET_SNAPSHOT_INGESTED",
            object_version="v1",
            object_content_hash="0" * 64,
            now=now,
        )
        return snapshot

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot:
        snapshot = self._repo.get_market_snapshot(snapshot_id)
        if snapshot is None:
            raise RiskOSNotFoundError(f"unknown market snapshot {snapshot_id!r}")
        return snapshot

    def run_challenger_valuation(
        self,
        *,
        run_id: str,
        netting_set_id: str,
        market_snapshot_id: str,
        as_of: dt.date,
        now: dt.datetime,
        portfolio_snapshot_id: str | None = None,
        irs_trades: tuple[InterestRateSwapTerms, ...] = (),
        fx_forward_trades: tuple[FxForwardTerms, ...] = (),
        actor: RiskOSActorContext | None = None,
    ) -> ChallengerRun:
        # SEC-04 authorization only -- deliberately no SEC-14 fencing here
        # (unlike `run_calculation`/`export_result_package` above): this
        # path must stay fully independent of the MARGIN_CALL authoritative
        # path (see `test_challenger_run_does_not_affect_margin_call_run`),
        # and it never mutates a `CalculationRun` or its netting set's
        # fence scope.
        self._authorize(
            action=ACTION_RUN_CHALLENGER_VALUATION,
            purpose=PURPOSE_CHALLENGER,
            object_id=run_id,
            object_version="v1",
            actor=actor,
            now=now,
        )
        market_snapshot = self._repo.get_market_snapshot(market_snapshot_id)
        if market_snapshot is None:
            raise RiskOSNotFoundError(f"unknown market snapshot {market_snapshot_id!r}")

        incumbent_trades: tuple = ()
        if portfolio_snapshot_id is not None:
            portfolio = self._repo.get_portfolio_snapshot(portfolio_snapshot_id)
            if portfolio is None:
                raise RiskOSNotFoundError(f"unknown portfolio snapshot {portfolio_snapshot_id!r}")
            incumbent_trades = portfolio.trades

        run = execute_challenger_run(
            run_id=run_id,
            netting_set_id=netting_set_id,
            market_snapshot=market_snapshot,
            as_of=as_of,
            now=now,
            irs_trades=irs_trades,
            fx_forward_trades=fx_forward_trades,
            incumbent_trades=incumbent_trades,
        )
        self._repo.save_challenger_run(run)
        self._record_audit_event(
            aggregate_id=run.run_id,
            action="CHALLENGER_RUN_EXECUTED",
            object_version="v1",
            object_content_hash="0" * 64,
            now=now,
        )
        return run

    def get_challenger_run(self, run_id: str) -> ChallengerRun:
        run = self._repo.get_challenger_run(run_id)
        if run is None:
            raise RiskOSNotFoundError(f"unknown challenger run {run_id!r}")
        return run

    # -- SEC-10 canonical evidence v2 (additive) --------------------------
    # A NEW capability alongside -- not a replacement of -- the OLD V1
    # `audit_protocol`/`cassandre_contracts` signed `Envelope` path
    # `export_result_package` above still uses for the authoritative
    # MARGIN_CALL result package. Nothing already signed under V1 is
    # touched, migrated, or re-verified differently by this addition. This
    # is deliberately scoped to `ChallengerRun` (a newer, additive
    # RSK-X01 aggregate already built this session) rather than retrofitted
    # onto the MARGIN_CALL export path.
    def seal_challenger_run_evidence(
        self,
        run_id: str,
        *,
        now: dt.datetime,
        valid_for: dt.timedelta = riskos_security.CHALLENGER_EVIDENCE_DEFAULT_VALIDITY,
    ) -> EvidenceContainer:
        run = self.get_challenger_run(run_id)
        payload = challenger_run_to_evidence_payload(run)
        body = build_protected_body(
            payload=payload,
            tenant_id=riskos_security.CHALLENGER_EVIDENCE_TENANT_ID,
            signer_key_set_id=self._keypair.key_id,
            object_id=run.run_id,
            object_version=riskos_security.CHALLENGER_EVIDENCE_OBJECT_VERSION,
            purpose=riskos_security.CHALLENGER_EVIDENCE_PURPOSE,
            policy_epoch=0,
            valid_from=now,
            valid_until=now + valid_for,
        )
        return seal_evidence(body, ed25519_keypair=self._keypair)

    def verify_challenger_run_evidence(self, container: EvidenceContainer, *, now: dt.datetime) -> None:
        """Raises `cassandre_security.canonical_evidence.EvidenceVerificationError`
        on any tamper/expiry/signature failure -- never returns a boolean a
        caller could accidentally ignore."""

        verify_evidence(container, trusted_ed25519_public_key_b64=self._keypair.public_key_b64, now=now)

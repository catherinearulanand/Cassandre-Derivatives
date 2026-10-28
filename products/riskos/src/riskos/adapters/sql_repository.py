"""SQLAlchemy-backed `RiskOSRepository` implementation. Requires a real
Postgres (or other SQLAlchemy-supported) database; domain/core tests never
depend on this module (see `products/riskos/tests/conftest.py`, which skips
any DB-dependent test when `CASSANDRE_TEST_DATABASE_URL` is unset or
unreachable — Docker Desktop is not functional on this build machine).
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from audit_protocol import AuditEvent
from cassandre_security.revocation import LockedFence
from cassandre_security.revocation import ensure_fence_exists as _cassandre_ensure_fence_exists
from cassandre_security.revocation import lock_fences_for_mutation as _cassandre_lock_fences_for_mutation
from cassandre_security.witness import WitnessRecord
from cassandre_security.witness import submit_checkpoint as _cassandre_submit_checkpoint

from riskos.adapters import codec
from riskos.adapters.models import (
    AgreementTermsPackageRow,
    AuditEventRow,
    CalculationRunRow,
    ChallengerRunRow,
    CollateralStatePackageRow,
    LegalEntityVersionRow,
    MarginWaterfallRow,
    MarketSnapshotRow,
    NettingSetVersionRow,
    OutboxRow,
    PortfolioSnapshotRow,
    ReconciliationCaseRow,
    TradeMTMRow,
)
from riskos.domain.entities import (
    AgreementTermsPackage,
    CalculationRun,
    ChallengerRun,
    CollateralStatePackage,
    LegalEntityMasterVersion,
    MarketSnapshot,
    NettingSetVersion,
    PortfolioSnapshot,
    ReconciliationCase,
)


class SqlAlchemyRiskOSRepository:
    """One instance per unit-of-work `Session`; each method commits its own
    transaction (including its outbox row) for this slice's simplicity."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _add_outbox(self, *, aggregate_id: str, event_type: str, payload: dict) -> None:
        self._session.add(
            OutboxRow(
                outbox_id=str(uuid.uuid4()),
                aggregate_id=aggregate_id,
                event_type=event_type,
                payload=payload,
                created_at=dt.datetime.now(dt.timezone.utc),
                published_at=None,
            )
        )

    def save_legal_entity(self, entity: LegalEntityMasterVersion) -> None:
        payload = codec.legal_entity_to_payload(entity)
        row = self._session.get(LegalEntityVersionRow, (entity.entity_id, entity.version))
        now = dt.datetime.now(dt.timezone.utc)
        if row is None:
            self._session.add(
                LegalEntityVersionRow(entity_id=entity.entity_id, version=entity.version, payload=payload, created_at=now)
            )
        else:
            row.payload = payload
        self._add_outbox(aggregate_id=entity.entity_id, event_type="LEGAL_ENTITY_VERSION_SAVED", payload=payload)
        self._session.commit()

    def get_legal_entity(self, entity_id: str) -> LegalEntityMasterVersion | None:
        stmt = (
            select(LegalEntityVersionRow)
            .where(LegalEntityVersionRow.entity_id == entity_id)
            .order_by(LegalEntityVersionRow.version.desc())
        )
        row = self._session.execute(stmt).scalars().first()
        return codec.legal_entity_from_payload(row.payload) if row else None

    def list_legal_entities(self) -> tuple[LegalEntityMasterVersion, ...]:
        rows = self._session.execute(select(LegalEntityVersionRow)).scalars().all()
        return tuple(codec.legal_entity_from_payload(r.payload) for r in rows)

    def save_netting_set(self, netting_set: NettingSetVersion) -> None:
        payload = codec.netting_set_to_payload(netting_set)
        now = dt.datetime.now(dt.timezone.utc)
        row = self._session.get(NettingSetVersionRow, (netting_set.netting_set_id, netting_set.version))
        if row is None:
            self._session.add(
                NettingSetVersionRow(
                    netting_set_id=netting_set.netting_set_id,
                    version=netting_set.version,
                    calculation_entity_id=netting_set.calculation_entity_id,
                    counterparty_entity_id=netting_set.counterparty_entity_id,
                    payload=payload,
                    created_at=now,
                )
            )
        else:
            row.payload = payload
        self._add_outbox(aggregate_id=netting_set.netting_set_id, event_type="NETTING_SET_VERSION_SAVED", payload=payload)
        self._session.commit()

    def list_netting_sets(self) -> tuple[NettingSetVersion, ...]:
        rows = self._session.execute(select(NettingSetVersionRow)).scalars().all()
        return tuple(codec.netting_set_from_payload(r.payload) for r in rows)

    def save_agreement_terms(self, terms: AgreementTermsPackage) -> None:
        payload = codec.agreement_terms_to_payload(terms)
        now = dt.datetime.now(dt.timezone.utc)
        row = self._session.get(AgreementTermsPackageRow, terms.agreement_id)
        if row is None:
            self._session.add(
                AgreementTermsPackageRow(
                    agreement_id=terms.agreement_id,
                    netting_set_id=terms.netting_set_id,
                    payload=payload,
                    created_at=now,
                )
            )
        else:
            row.payload = payload
        self._add_outbox(aggregate_id=terms.agreement_id, event_type="AGREEMENT_TERMS_SAVED", payload=payload)
        self._session.commit()

    def get_agreement_terms(self, agreement_id: str) -> AgreementTermsPackage | None:
        row = self._session.get(AgreementTermsPackageRow, agreement_id)
        return codec.agreement_terms_from_payload(row.payload) if row else None

    def save_portfolio_snapshot(self, snapshot: PortfolioSnapshot) -> None:
        payload = codec.portfolio_snapshot_to_payload(snapshot)
        now = dt.datetime.now(dt.timezone.utc)
        row = self._session.get(PortfolioSnapshotRow, snapshot.snapshot_id)
        if row is None:
            self._session.add(
                PortfolioSnapshotRow(
                    snapshot_id=snapshot.snapshot_id, as_of=snapshot.as_of, payload=payload, created_at=now
                )
            )
        else:
            row.payload = payload
            row.as_of = snapshot.as_of
        for trade in snapshot.trades:
            trade_row = self._session.get(TradeMTMRow, (trade.trade_id, snapshot.snapshot_id))
            trade_payload = codec.trade_to_payload(trade)
            if trade_row is None:
                self._session.add(
                    TradeMTMRow(
                        trade_id=trade.trade_id,
                        snapshot_id=snapshot.snapshot_id,
                        netting_set_id=trade.netting_set_id,
                        disposition=trade.disposition.value,
                        payload=trade_payload,
                    )
                )
            else:
                trade_row.payload = trade_payload
                trade_row.disposition = trade.disposition.value
        self._add_outbox(aggregate_id=snapshot.snapshot_id, event_type="PORTFOLIO_SNAPSHOT_SAVED", payload=payload)
        self._session.commit()

    def get_portfolio_snapshot(self, snapshot_id: str) -> PortfolioSnapshot | None:
        row = self._session.get(PortfolioSnapshotRow, snapshot_id)
        return codec.portfolio_snapshot_from_payload(row.payload) if row else None

    def save_collateral_state(self, package: CollateralStatePackage) -> None:
        payload = codec.collateral_state_to_payload(package)
        now = dt.datetime.now(dt.timezone.utc)
        row = self._session.get(CollateralStatePackageRow, package.package_id)
        if row is None:
            self._session.add(
                CollateralStatePackageRow(
                    package_id=package.package_id,
                    netting_set_id=package.netting_set_id,
                    as_of=package.as_of,
                    payload=payload,
                    created_at=now,
                )
            )
        else:
            row.payload = payload
        self._add_outbox(aggregate_id=package.package_id, event_type="COLLATERAL_STATE_SAVED", payload=payload)
        self._session.commit()

    def get_collateral_state(self, package_id: str) -> CollateralStatePackage | None:
        row = self._session.get(CollateralStatePackageRow, package_id)
        return codec.collateral_state_from_payload(row.payload) if row else None

    def save_calculation_run(self, run: CalculationRun) -> None:
        payload = codec.calculation_run_to_payload(run)
        now = dt.datetime.now(dt.timezone.utc)
        row = self._session.get(CalculationRunRow, run.run_id)
        if row is None:
            self._session.add(
                CalculationRunRow(
                    run_id=run.run_id,
                    netting_set_id=run.netting_set_id,
                    calculation_status=run.calculation_status.value,
                    reconciliation_case_id=run.reconciliation_case_id,
                    payload=payload,
                    created_at=now,
                )
            )
        else:
            row.payload = payload
            row.calculation_status = run.calculation_status.value
            row.reconciliation_case_id = run.reconciliation_case_id
        if run.waterfall is not None:
            wf_payload = codec.waterfall_to_payload(run.waterfall)
            wf_row = self._session.get(MarginWaterfallRow, run.run_id)
            if wf_row is None:
                self._session.add(
                    MarginWaterfallRow(
                        run_id=run.run_id,
                        netting_set_id=run.waterfall.netting_set_id,
                        call_status=run.waterfall.call_status.value,
                        payload=wf_payload,
                    )
                )
            else:
                wf_row.payload = wf_payload
                wf_row.call_status = run.waterfall.call_status.value
        self._add_outbox(aggregate_id=run.run_id, event_type="CALCULATION_RUN_SAVED", payload=payload)
        self._session.commit()

    def get_calculation_run(self, run_id: str) -> CalculationRun | None:
        row = self._session.get(CalculationRunRow, run_id)
        return codec.calculation_run_from_payload(row.payload) if row else None

    def save_reconciliation_case(self, case: ReconciliationCase) -> None:
        payload = codec.reconciliation_case_to_payload(case)
        now = dt.datetime.now(dt.timezone.utc)
        row = self._session.get(ReconciliationCaseRow, case.case_id)
        if row is None:
            self._session.add(
                ReconciliationCaseRow(
                    case_id=case.case_id,
                    run_id=case.run_id,
                    reconciliation_status=case.reconciliation_status.value,
                    payload=payload,
                    created_at=now,
                )
            )
        else:
            row.payload = payload
            row.reconciliation_status = case.reconciliation_status.value
        self._add_outbox(aggregate_id=case.case_id, event_type="RECONCILIATION_CASE_SAVED", payload=payload)
        self._session.commit()

    def get_reconciliation_case(self, case_id: str) -> ReconciliationCase | None:
        row = self._session.get(ReconciliationCaseRow, case_id)
        return codec.reconciliation_case_from_payload(row.payload) if row else None

    # -- RSK-X01 native valuation challenger (additive) -------------------
    def save_market_snapshot(self, snapshot: MarketSnapshot) -> None:
        payload = codec.market_snapshot_to_payload(snapshot)
        now = dt.datetime.now(dt.timezone.utc)
        as_of_dt = dt.datetime.combine(snapshot.as_of, dt.time.min, tzinfo=dt.timezone.utc)
        row = self._session.get(MarketSnapshotRow, snapshot.snapshot_id)
        if row is None:
            self._session.add(
                MarketSnapshotRow(
                    snapshot_id=snapshot.snapshot_id, as_of=as_of_dt, payload=payload, created_at=now
                )
            )
        else:
            row.payload = payload
            row.as_of = as_of_dt
        self._add_outbox(aggregate_id=snapshot.snapshot_id, event_type="MARKET_SNAPSHOT_SAVED", payload=payload)
        self._session.commit()

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot | None:
        row = self._session.get(MarketSnapshotRow, snapshot_id)
        return codec.market_snapshot_from_payload(row.payload) if row else None

    def save_challenger_run(self, run: ChallengerRun) -> None:
        payload = codec.challenger_run_to_payload(run)
        now = dt.datetime.now(dt.timezone.utc)
        row = self._session.get(ChallengerRunRow, run.run_id)
        if row is None:
            self._session.add(
                ChallengerRunRow(
                    run_id=run.run_id,
                    netting_set_id=run.netting_set_id,
                    calculation_status=run.calculation_status.value,
                    reconciliation_status=run.reconciliation_status.value,
                    payload=payload,
                    created_at=now,
                )
            )
        else:
            row.payload = payload
            row.calculation_status = run.calculation_status.value
            row.reconciliation_status = run.reconciliation_status.value
        self._add_outbox(aggregate_id=run.run_id, event_type="CHALLENGER_RUN_SAVED", payload=payload)
        self._session.commit()

    def get_challenger_run(self, run_id: str) -> ChallengerRun | None:
        row = self._session.get(ChallengerRunRow, run_id)
        return codec.challenger_run_from_payload(row.payload) if row else None

    # -- SEC-14 revocation fencing / SEC-15 witness (cassandre_security) --
    # These three methods are deliberately NOT part of `RiskOSRepository`'s
    # Protocol: `RiskOSService` looks them up via `getattr(..., None)` (duck
    # typing) so the in-memory repository (no fence/witness table at all)
    # is a clean no-op, and so this SQL adapter can add security-plumbing
    # methods without forcing every other `RiskOSRepository` implementation
    # to grow matching no-op stubs. Both delegate to `self._session` --
    # the SAME session every other method on this class writes/commits
    # through -- so a fence lock acquired here and released only by this
    # session's own next commit (in `save_calculation_run`/
    # `append_audit_event`) is genuinely held through that write, per
    # `cassandre_security.revocation`'s "hold the lock through the same
    # atomic commit" contract. Neither method commits itself (matching that
    # module's contract that the CALLER commits).
    def ensure_fence_exists(self, *, scope_key: str, now: dt.datetime):
        return _cassandre_ensure_fence_exists(self._session, scope_key, now=now)

    def lock_fences_for_mutation(
        self,
        *,
        scope_keys: tuple[str, ...],
        presented_epoch_by_scope: dict[str, int],
        now: dt.datetime,
    ) -> tuple[LockedFence, ...]:
        return _cassandre_lock_fences_for_mutation(
            self._session,
            scope_keys=scope_keys,
            presented_epoch_by_scope=presented_epoch_by_scope,
            now=now,
        )

    # RiskOS-wide witness chain identity (see `riskos.application.security`
    # for the same values under `WITNESS_*` names -- duplicated here rather
    # than imported so this adapters-layer module does not depend upward on
    # the application layer, matching this codebase's existing layering).
    _WITNESS_PRODUCT_CHAIN_ID = "riskos"
    _WITNESS_RELEASE_MANIFEST_DIGEST = "sha256:riskos-release-manifest-provisional"
    _WITNESS_TRUST_BUNDLE_VERSION = "riskos-trust-bundle-v1-provisional"

    def submit_witness_checkpoint(self, *, sequence: int, head_hash: str, now: dt.datetime) -> WitnessRecord:
        return _cassandre_submit_checkpoint(
            self._session,
            product_chain_id=self._WITNESS_PRODUCT_CHAIN_ID,
            sequence=sequence,
            head_hash=head_hash,
            release_manifest_digest=self._WITNESS_RELEASE_MANIFEST_DIGEST,
            trust_bundle_version=self._WITNESS_TRUST_BUNDLE_VERSION,
            now=now,
        )

    def last_audit_event(self) -> AuditEvent | None:
        stmt = select(AuditEventRow).order_by(AuditEventRow.sequence.desc())
        row = self._session.execute(stmt).scalars().first()
        return _audit_row_to_event(row) if row else None

    def append_audit_event(self, event: AuditEvent) -> None:
        self._session.add(
            AuditEventRow(
                sequence=event.sequence,
                event_id=event.event_id,
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
                event_hash=event.event_hash,
            )
        )
        self._session.commit()

    def list_audit_events(self) -> tuple[AuditEvent, ...]:
        stmt = select(AuditEventRow).order_by(AuditEventRow.sequence.asc())
        rows = self._session.execute(stmt).scalars().all()
        return tuple(_audit_row_to_event(r) for r in rows)


def _audit_row_to_event(row: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        event_id=row.event_id,
        sequence=row.sequence,
        product=row.product,
        aggregate_id=row.aggregate_id,
        actor_id=row.actor_id,
        actor_type=row.actor_type,
        action=row.action,
        reason_code=row.reason_code,
        object_version=row.object_version,
        object_content_hash=row.object_content_hash,
        event_timestamp=row.event_timestamp,
        previous_event_hash=row.previous_event_hash,
        simulation_provenance=row.simulation_provenance,
        trace_id=row.trace_id,
        event_hash=row.event_hash,
    )

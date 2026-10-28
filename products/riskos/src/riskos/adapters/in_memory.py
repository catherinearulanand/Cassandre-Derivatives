"""Process-local in-memory repository implementing `RiskOSRepository`. Used
by `riskos_api` so its API tests run without Postgres (Docker Desktop is not
functional on this build machine — see `products/riskos/README.md`). Not a
durable adapter: nothing here survives process restart.
"""

from __future__ import annotations

import threading

from audit_protocol import AuditEvent

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


class InMemoryRiskOSRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._legal_entities: dict[str, LegalEntityMasterVersion] = {}
        self._netting_sets: dict[str, NettingSetVersion] = {}
        self._agreement_terms: dict[str, AgreementTermsPackage] = {}
        self._portfolio_snapshots: dict[str, PortfolioSnapshot] = {}
        self._collateral_states: dict[str, CollateralStatePackage] = {}
        self._calculation_runs: dict[str, CalculationRun] = {}
        self._reconciliation_cases: dict[str, ReconciliationCase] = {}
        self._market_snapshots: dict[str, MarketSnapshot] = {}
        self._challenger_runs: dict[str, ChallengerRun] = {}
        self._audit_events: list[AuditEvent] = []

    def save_legal_entity(self, entity: LegalEntityMasterVersion) -> None:
        with self._lock:
            self._legal_entities[entity.entity_id] = entity

    def get_legal_entity(self, entity_id: str) -> LegalEntityMasterVersion | None:
        with self._lock:
            return self._legal_entities.get(entity_id)

    def list_legal_entities(self) -> tuple[LegalEntityMasterVersion, ...]:
        with self._lock:
            return tuple(self._legal_entities.values())

    def save_netting_set(self, netting_set: NettingSetVersion) -> None:
        with self._lock:
            self._netting_sets[netting_set.netting_set_id] = netting_set

    def list_netting_sets(self) -> tuple[NettingSetVersion, ...]:
        with self._lock:
            return tuple(self._netting_sets.values())

    def save_agreement_terms(self, terms: AgreementTermsPackage) -> None:
        with self._lock:
            self._agreement_terms[terms.agreement_id] = terms

    def get_agreement_terms(self, agreement_id: str) -> AgreementTermsPackage | None:
        with self._lock:
            return self._agreement_terms.get(agreement_id)

    def save_portfolio_snapshot(self, snapshot: PortfolioSnapshot) -> None:
        with self._lock:
            self._portfolio_snapshots[snapshot.snapshot_id] = snapshot

    def get_portfolio_snapshot(self, snapshot_id: str) -> PortfolioSnapshot | None:
        with self._lock:
            return self._portfolio_snapshots.get(snapshot_id)

    def save_collateral_state(self, package: CollateralStatePackage) -> None:
        with self._lock:
            self._collateral_states[package.package_id] = package

    def get_collateral_state(self, package_id: str) -> CollateralStatePackage | None:
        with self._lock:
            return self._collateral_states.get(package_id)

    def save_calculation_run(self, run: CalculationRun) -> None:
        with self._lock:
            self._calculation_runs[run.run_id] = run

    def get_calculation_run(self, run_id: str) -> CalculationRun | None:
        with self._lock:
            return self._calculation_runs.get(run_id)

    def save_reconciliation_case(self, case: ReconciliationCase) -> None:
        with self._lock:
            self._reconciliation_cases[case.case_id] = case

    def get_reconciliation_case(self, case_id: str) -> ReconciliationCase | None:
        with self._lock:
            return self._reconciliation_cases.get(case_id)

    def save_market_snapshot(self, snapshot: MarketSnapshot) -> None:
        with self._lock:
            self._market_snapshots[snapshot.snapshot_id] = snapshot

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot | None:
        with self._lock:
            return self._market_snapshots.get(snapshot_id)

    def save_challenger_run(self, run: ChallengerRun) -> None:
        with self._lock:
            self._challenger_runs[run.run_id] = run

    def get_challenger_run(self, run_id: str) -> ChallengerRun | None:
        with self._lock:
            return self._challenger_runs.get(run_id)

    def last_audit_event(self) -> AuditEvent | None:
        with self._lock:
            return self._audit_events[-1] if self._audit_events else None

    def append_audit_event(self, event: AuditEvent) -> None:
        with self._lock:
            self._audit_events.append(event)

    def list_audit_events(self) -> tuple[AuditEvent, ...]:
        with self._lock:
            return tuple(self._audit_events)

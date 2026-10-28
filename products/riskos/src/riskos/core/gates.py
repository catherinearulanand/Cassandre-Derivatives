"""Explicit authority, completeness, freshness, currency, duplicate, and
support-matrix gates, per plan Section 11.3 step 6: "Run authority,
completeness, freshness, sign, currency, duplicate, and support-matrix
gates." Each gate is a named, independently-failing check — never one
implicit boolean.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

from riskos.core.constants import FRESHNESS_WINDOW_HOURS
from riskos.domain.entities import (
    AgreementTermsPackage,
    CollateralStatePackage,
    LegalEntityMasterVersion,
    MethodPack,
    NettingSetVersion,
    PortfolioSnapshot,
)
from riskos.domain.enums import CalculationPurpose, TradePopulationDisposition


@dataclass(frozen=True)
class GateResult:
    ok: bool
    failed_gates: tuple[str, ...] = field(default_factory=tuple)


def run_authority_gate(
    *,
    calculation_entity: LegalEntityMasterVersion,
    counterparty_entity: LegalEntityMasterVersion,
    netting_set: NettingSetVersion,
    agreement_terms: AgreementTermsPackage,
) -> GateResult:
    failed = []
    if not calculation_entity.approved:
        failed.append("AUTHORITY: calculation entity master version not approved")
    if not counterparty_entity.approved:
        failed.append("AUTHORITY: counterparty entity master version not approved")
    if not netting_set.approved:
        failed.append("AUTHORITY: netting set version not approved")
    if not agreement_terms.approved:
        failed.append("AUTHORITY: agreement terms package not approved")
    return GateResult(ok=not failed, failed_gates=tuple(failed))


def run_completeness_gate(
    *,
    portfolio: PortfolioSnapshot,
    collateral: CollateralStatePackage,
) -> GateResult:
    failed = []
    if not portfolio.trades:
        failed.append("COMPLETENESS: portfolio snapshot has no trades")
    if collateral.custodian_reconciliation_status.value == "NOT_AVAILABLE":
        failed.append("COMPLETENESS: collateral state custodian reconciliation not available")
    return GateResult(ok=not failed, failed_gates=tuple(failed))


def run_freshness_gate(
    *,
    portfolio: PortfolioSnapshot,
    collateral: CollateralStatePackage,
    now: dt.datetime,
    freshness_window_hours: int = FRESHNESS_WINDOW_HOURS,
) -> GateResult:
    window = dt.timedelta(hours=freshness_window_hours)
    failed = []
    if now - portfolio.as_of > window:
        failed.append(
            f"FRESHNESS: portfolio snapshot as_of {portfolio.as_of.isoformat()} exceeds "
            f"{freshness_window_hours}h freshness window"
        )
    if now - collateral.as_of > window:
        failed.append(
            f"FRESHNESS: collateral state as_of {collateral.as_of.isoformat()} exceeds "
            f"{freshness_window_hours}h freshness window"
        )
    return GateResult(ok=not failed, failed_gates=tuple(failed))


def run_currency_gate(*, agreement_terms: AgreementTermsPackage, method_pack: MethodPack) -> GateResult:
    failed = []
    if agreement_terms.call_currency != method_pack.call_currency:
        failed.append(
            f"CURRENCY: agreement call currency {agreement_terms.call_currency!r} does not "
            f"match method pack call currency {method_pack.call_currency!r}"
        )
    return GateResult(ok=not failed, failed_gates=tuple(failed))


def run_duplicate_gate(*, portfolio: PortfolioSnapshot) -> GateResult:
    ids = [t.trade_id for t in portfolio.trades if t.disposition is TradePopulationDisposition.ACCEPTED]
    dupes = [trade_id for trade_id, count in Counter(ids).items() if count > 1]
    failed = [f"DUPLICATE: trade_id {tid!r} appears more than once among accepted trades" for tid in dupes]
    return GateResult(ok=not failed, failed_gates=tuple(failed))


def run_support_matrix_gate(
    *,
    purpose: CalculationPurpose,
    agreement_terms: AgreementTermsPackage,
    method_pack: MethodPack,
) -> GateResult:
    failed = []
    if purpose is not CalculationPurpose.MARGIN_CALL:
        failed.append(f"SUPPORT_MATRIX: purpose {purpose.value} is not implemented by this build")
    if not agreement_terms.is_supported:
        failed.append("SUPPORT_MATRIX: agreement terms package is flagged unsupported")
    if agreement_terms.method_pack_id != method_pack.method_id:
        failed.append(
            f"SUPPORT_MATRIX: agreement method_pack_id {agreement_terms.method_pack_id!r} does not "
            f"match the one implemented method pack {method_pack.method_id!r}"
        )
    return GateResult(ok=not failed, failed_gates=tuple(failed))


def run_all_gates(
    *,
    calculation_entity: LegalEntityMasterVersion,
    counterparty_entity: LegalEntityMasterVersion,
    netting_set: NettingSetVersion,
    agreement_terms: AgreementTermsPackage,
    portfolio: PortfolioSnapshot,
    collateral: CollateralStatePackage,
    method_pack: MethodPack,
    purpose: CalculationPurpose,
    now: dt.datetime,
) -> GateResult:
    results = [
        run_authority_gate(
            calculation_entity=calculation_entity,
            counterparty_entity=counterparty_entity,
            netting_set=netting_set,
            agreement_terms=agreement_terms,
        ),
        run_completeness_gate(portfolio=portfolio, collateral=collateral),
        run_freshness_gate(portfolio=portfolio, collateral=collateral, now=now),
        run_currency_gate(agreement_terms=agreement_terms, method_pack=method_pack),
        run_duplicate_gate(portfolio=portfolio),
        run_support_matrix_gate(purpose=purpose, agreement_terms=agreement_terms, method_pack=method_pack),
    ]
    all_failed = tuple(reason for r in results for reason in r.failed_gates)
    return GateResult(ok=not all_failed, failed_gates=all_failed)

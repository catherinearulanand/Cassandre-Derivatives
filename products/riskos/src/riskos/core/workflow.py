"""The S1 authoritative workflow, per plan Section 11.3's twelve numbered
steps. This module is the single place that decides whether a margin-call
run is `COMPLETE`, `PARTIAL`, or `FAILED`, and composes the multidimensional
result status (plan 11.7). It never silently continues with a weaker path
after an authoritative check fails (ADR-014 fail-closed, plan 25.3): any
blocking condition below produces an explicit `FAILED` run with a stated
`failure_reason`, not a partial computation dressed up as a result.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from riskos.core.exposure import aggregate_exposure, derive_credit_support_balance
from riskos.core.gates import run_all_gates
from riskos.core.method_pack import resolve_transfer_parties, run_method_pack_001
from riskos.core.netting import resolve_netting_set, verify_trade_membership
from riskos.core.population import reconcile_population
from riskos.core.reconciliation import PartyCallView, build_reconciliation_case
from riskos.domain.entities import (
    AgreementTermsPackage,
    CalculationRun,
    CollateralStatePackage,
    LegalEntityMasterVersion,
    MarginWaterfall,
    MarginWaterfallStep,
    MethodPack,
    NettingSetVersion,
    PortfolioSnapshot,
    ReconciliationCase,
    SourceControlManifest,
)
from riskos.domain.enums import (
    Actionability,
    AgreementSupportStatus,
    CalculationPurpose,
    CalculationStatus,
    DataQualityStatus,
    ReconciliationStatus,
    ValidationStatus,
)


@dataclass(frozen=True)
class MarginCallRequest:
    run_id: str
    purpose: CalculationPurpose
    calculation_entity: LegalEntityMasterVersion
    counterparty_entity: LegalEntityMasterVersion
    netting_set_candidates: tuple[NettingSetVersion, ...]
    agreement_terms: AgreementTermsPackage
    portfolio: PortfolioSnapshot
    manifest: SourceControlManifest
    collateral: CollateralStatePackage
    method_pack: MethodPack
    counterparty_call: PartyCallView
    now: dt.datetime


@dataclass(frozen=True)
class MarginCallExecutionResult:
    run: CalculationRun
    reconciliation: ReconciliationCase | None


def _blocked_run(
    request: MarginCallRequest,
    *,
    failure_reason: str,
    agreement_support_status: AgreementSupportStatus = AgreementSupportStatus.SUPPORTED,
    data_quality_status: DataQualityStatus = DataQualityStatus.INCOMPLETE,
) -> MarginCallExecutionResult:
    run = CalculationRun(
        run_id=request.run_id,
        purpose=request.purpose,
        netting_set_id="UNRESOLVED",
        method_pack_id=request.method_pack.method_id,
        calculation_status=CalculationStatus.FAILED,
        data_quality_status=data_quality_status,
        reconciliation_status=ReconciliationStatus.NOT_AVAILABLE,
        validation_status=ValidationStatus.UNVALIDATED,
        actionability=Actionability.REJECTED,
        agreement_support_status=agreement_support_status,
        created_at=request.now,
        waterfall=None,
        reconciliation_case_id=None,
        failure_reason=failure_reason,
    )
    return MarginCallExecutionResult(run=run, reconciliation=None)


def execute_margin_call(request: MarginCallRequest) -> MarginCallExecutionResult:
    # Step: calculation purpose (plan 11.2). No automatic fallback to a
    # different purpose's support matrix.
    if request.purpose is not CalculationPurpose.MARGIN_CALL:
        return _blocked_run(
            request,
            failure_reason=f"unsupported calculation purpose {request.purpose.value!r} for this build",
        )

    # Step 3: resolve calculation entity, counterparty, agreement, netting
    # set from approved masters. Zero or multiple matches block the run.
    resolution = resolve_netting_set(
        calculation_entity_id=request.calculation_entity.entity_id,
        counterparty_entity_id=request.counterparty_entity.entity_id,
        candidates=request.netting_set_candidates,
    )
    if not resolution.ok or resolution.resolved is None:
        return _blocked_run(request, failure_reason=resolution.blocking_reason or "netting set resolution failed")
    netting_set = resolution.resolved

    membership = verify_trade_membership(request.portfolio.trades, netting_set=netting_set)
    if not membership.ok:
        return _blocked_run(
            request,
            failure_reason=(
                "trade population failure: trades resolve outside the approved netting set: "
                f"{', '.join(membership.mismatched_trade_ids)}"
            ),
        )

    # Step 2 (population control, plan 9.3/11.3): reconcile counts and MTM
    # totals against the source-control manifest before aggregation.
    population = reconcile_population(request.portfolio, request.manifest)
    if not population.ok:
        return _blocked_run(
            request,
            failure_reason="trade population reconciliation failed: " + "; ".join(population.mismatches),
        )

    # Step: agreement/method-pack resolution (plan 11.4). An unresolved
    # election or unsupported form returns METHOD_UNRESOLVED or
    # UNSUPPORTED_AGREEMENT and no call amount — never a guessed formula.
    if not request.agreement_terms.is_supported:
        return _blocked_run(
            request,
            failure_reason="agreement terms package is not a supported agreement family",
            agreement_support_status=AgreementSupportStatus.UNSUPPORTED_AGREEMENT,
        )
    if request.agreement_terms.method_pack_id != request.method_pack.method_id:
        return _blocked_run(
            request,
            failure_reason=(
                f"agreement references method_pack_id {request.agreement_terms.method_pack_id!r}, "
                f"which does not resolve to the one implemented pack {request.method_pack.method_id!r}"
            ),
            agreement_support_status=AgreementSupportStatus.METHOD_UNRESOLVED,
        )

    # Step 6: authority, completeness, freshness, currency, duplicate, and
    # support-matrix gates.
    gate_result = run_all_gates(
        calculation_entity=request.calculation_entity,
        counterparty_entity=request.counterparty_entity,
        netting_set=netting_set,
        agreement_terms=request.agreement_terms,
        portfolio=request.portfolio,
        collateral=request.collateral,
        method_pack=request.method_pack,
        purpose=request.purpose,
        now=request.now,
    )
    if not gate_result.ok:
        reasons = gate_result.failed_gates
        data_quality = (
            DataQualityStatus.STALE
            if any(r.startswith("FRESHNESS") for r in reasons)
            else DataQualityStatus.INCOMPLETE
        )
        return _blocked_run(
            request,
            failure_reason="gate failure: " + "; ".join(reasons),
            data_quality_status=data_quality,
        )

    # Step 7: deterministically aggregate exposure within the one approved
    # netting set.
    exposure = aggregate_exposure(request.portfolio.trades)

    # Step 4/11.3 collateral component list: Credit Support Balance derived
    # only through the selected method pack.
    credit_support_balance = derive_credit_support_balance(request.collateral)

    # Step 8: run the selected versioned CSA/margin state machine.
    calc_result = run_method_pack_001(
        method_pack=request.method_pack,
        exposure=exposure,
        credit_support_balance=credit_support_balance,
    )
    transferor_id, transferee_id = resolve_transfer_parties(
        call_status=calc_result.call_status,
        calculation_entity_id=request.calculation_entity.entity_id,
        counterparty_entity_id=request.counterparty_entity.entity_id,
    )

    waterfall = MarginWaterfall(
        run_id=request.run_id,
        netting_set_id=netting_set.netting_set_id,
        exposure=exposure,
        threshold_calculation_party=request.method_pack.threshold_calculation_party,
        threshold_counterparty=request.method_pack.threshold_counterparty,
        independent_amount_net=request.method_pack.independent_amount_calculation_party,
        credit_support_balance=credit_support_balance,
        minimum_transfer_amount=request.method_pack.minimum_transfer_amount,
        call_status=calc_result.call_status,
        call_amount=calc_result.call_amount,
        transferor_entity_id=transferor_id,
        transferee_entity_id=transferee_id,
        steps=(
            MarginWaterfallStep(step_name="exposure", value=exposure),
            MarginWaterfallStep(
                step_name="threshold_calculation_party", value=request.method_pack.threshold_calculation_party
            ),
            MarginWaterfallStep(step_name="credit_support_balance", value=credit_support_balance),
            MarginWaterfallStep(step_name="call_amount", value=calc_result.call_amount, note=calc_result.call_status.value),
        ),
    )

    # Step 9/10/11: reconcile against the synthetic counterparty result,
    # classify any break, and derive the undisputed amount only under the
    # method pack's explicit rule.
    calculation_party_view = PartyCallView(
        party_label="CALCULATION_PARTY",
        call_status=calc_result.call_status,
        call_amount=calc_result.call_amount,
        exposure=exposure,
        credit_support_balance=credit_support_balance,
        trade_population_count=sum(1 for t in request.portfolio.trades if t.disposition.value == "ACCEPTED"),
        as_of=request.portfolio.as_of,
    )
    reconciliation = build_reconciliation_case(
        case_id=f"RECON-{request.run_id}",
        run_id=request.run_id,
        calculation_party=calculation_party_view,
        counterparty=request.counterparty_call,
        lower_common_amount_authorized=request.method_pack.lower_common_amount_authorized,
    )

    # Step 12: publish statuses.
    reconciliation_status = reconciliation.reconciliation_status
    actionability = (
        Actionability.REVIEW_REQUIRED
        if reconciliation_status is ReconciliationStatus.BREAK
        else Actionability.SIMULATION_ELIGIBLE
    )
    run = CalculationRun(
        run_id=request.run_id,
        purpose=request.purpose,
        netting_set_id=netting_set.netting_set_id,
        method_pack_id=request.method_pack.method_id,
        calculation_status=CalculationStatus.COMPLETE,
        data_quality_status=DataQualityStatus.APPROVED,
        reconciliation_status=reconciliation_status,
        validation_status=ValidationStatus.RESTRICTED_USE,
        actionability=actionability,
        agreement_support_status=AgreementSupportStatus.SUPPORTED,
        created_at=request.now,
        waterfall=waterfall,
        reconciliation_case_id=reconciliation.case_id,
        limitations=(
            "METHOD-PACK-001 is a PROVISIONAL synthetic placeholder convention (DEC-RSK-01), "
            "not an approved production margin methodology.",
            "Simulation-only: no real institution, agreement, or price is represented.",
        ),
    )
    return MarginCallExecutionResult(run=run, reconciliation=reconciliation)

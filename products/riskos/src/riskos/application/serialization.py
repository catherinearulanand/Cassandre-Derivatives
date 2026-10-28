"""Convert domain objects into the JSON-safe, canonicalizable payload shape
required by `cassandre_contracts.Envelope` / `audit_protocol.canonical`:
every amount a decimal string, every timestamp RFC3339, every enum its
`.value` — never a float (`audit_protocol.canonical` rejects floats
outright)."""

from __future__ import annotations

from typing import Any

from money_types import Money

from riskos.domain.entities import (
    ChallengerComparison,
    ChallengerRun,
    MarginWaterfall,
    ReconciliationCase,
    ResultPackage,
)


def money_to_dict(money: Money) -> dict[str, str]:
    return {
        "amount": str(money.amount),
        "currency": money.currency,
        "rounding_context": money.rounding_context,
    }


def waterfall_to_dict(waterfall: MarginWaterfall) -> dict[str, Any]:
    return {
        "run_id": waterfall.run_id,
        "netting_set_id": waterfall.netting_set_id,
        "exposure": money_to_dict(waterfall.exposure),
        "threshold_calculation_party": money_to_dict(waterfall.threshold_calculation_party),
        "threshold_counterparty": money_to_dict(waterfall.threshold_counterparty),
        "independent_amount_net": money_to_dict(waterfall.independent_amount_net),
        "credit_support_balance": money_to_dict(waterfall.credit_support_balance),
        "minimum_transfer_amount": money_to_dict(waterfall.minimum_transfer_amount),
        "call_status": waterfall.call_status.value,
        "call_amount": money_to_dict(waterfall.call_amount),
        "transferor_entity_id": waterfall.transferor_entity_id,
        "transferee_entity_id": waterfall.transferee_entity_id,
        "steps": [
            {
                "step_name": s.step_name,
                "value": money_to_dict(s.value),
                "note": s.note,
            }
            for s in waterfall.steps
        ],
    }


def reconciliation_to_dict(case: ReconciliationCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "run_id": case.run_id,
        "calculation_party_call_status": case.calculation_party_call_status.value,
        "calculation_party_call_amount": money_to_dict(case.calculation_party_call_amount),
        "counterparty_call_status": case.counterparty_call_status.value,
        "counterparty_call_amount": money_to_dict(case.counterparty_call_amount),
        "dispute_case": case.dispute_case.value,
        "undisputed_status": case.undisputed_status.value,
        "undisputed_amount": money_to_dict(case.undisputed_amount) if case.undisputed_amount else None,
        "reconciliation_status": case.reconciliation_status.value,
        "breaks": [
            {
                "break_id": b.break_id,
                "break_type": b.break_type.value,
                "description": b.description,
                "calculation_party_amount": money_to_dict(b.calculation_party_amount),
                "counterparty_amount": money_to_dict(b.counterparty_amount),
                "difference": money_to_dict(b.difference),
            }
            for b in case.breaks
        ],
    }


def result_package_to_payload(result_package: ResultPackage) -> dict[str, Any]:
    return {
        "result_id": result_package.result_id,
        "run_id": result_package.run_id,
        "netting_set_id": result_package.netting_set_id,
        "calculation_status": result_package.calculation_status.value,
        "data_quality_status": result_package.data_quality_status.value,
        "reconciliation_status": result_package.reconciliation_status.value,
        "validation_status": result_package.validation_status.value,
        "actionability": result_package.actionability.value,
        "method_pack_id": result_package.method_pack_id,
        "method_pack_version": result_package.method_pack_version,
        "waterfall": waterfall_to_dict(result_package.waterfall) if result_package.waterfall else None,
        "reconciliation": (
            reconciliation_to_dict(result_package.reconciliation) if result_package.reconciliation else None
        ),
        "assumptions": list(result_package.assumptions),
        "limitations": list(result_package.limitations),
        "generated_at": result_package.generated_at.isoformat(),
    }


# -- SEC-10 canonical evidence v2 (additive; see `riskos.application.security`
# and `RiskOSService.seal_challenger_run_evidence`) -------------------------
#
# Deliberately a separate serializer from `riskos.adapters.codec`'s SQL
# storage codec for the same aggregate: the evidence-signing payload's
# shape must never silently change just because the storage codec's shape
# changes, and this application-layer module should not depend on the
# adapters layer.


def _opt_money_to_dict(money) -> dict[str, str] | None:
    return money_to_dict(money) if money is not None else None


def challenger_comparison_to_evidence_dict(comparison: ChallengerComparison) -> dict[str, Any]:
    return {
        "trade_id": comparison.trade_id,
        "product_family": comparison.product_family,
        "challenger_pv": _opt_money_to_dict(comparison.challenger_pv),
        "incumbent_pv": _opt_money_to_dict(comparison.incumbent_pv),
        "difference": _opt_money_to_dict(comparison.difference),
        "tolerance": _opt_money_to_dict(comparison.tolerance),
        "comparison_status": comparison.comparison_status.value,
        "note": comparison.note,
    }


def challenger_run_to_evidence_payload(run: ChallengerRun) -> dict[str, Any]:
    """SEC-10 canonical evidence v2 payload for one `ChallengerRun`."""

    return {
        "run_id": run.run_id,
        "netting_set_id": run.netting_set_id,
        "market_snapshot_id": run.market_snapshot_id,
        "calculation_status": run.calculation_status.value,
        "data_quality_status": run.data_quality_status.value,
        "reconciliation_status": run.reconciliation_status.value,
        "validation_status": run.validation_status.value,
        "actionability": run.actionability.value,
        "comparisons": [challenger_comparison_to_evidence_dict(c) for c in run.comparisons],
        "created_at": run.created_at.isoformat(),
        "limitations": list(run.limitations),
        "failure_reason": run.failure_reason,
    }

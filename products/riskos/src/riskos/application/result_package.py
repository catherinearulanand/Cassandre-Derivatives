"""Build a `ResultPackage` from a completed (or blocked) `CalculationRun` /
`ReconciliationCase`, per plan 3.2's terminal outputs and Section 11.3 step
12: "Publish statuses, trace, limitations, and signed evidence package."
"""

from __future__ import annotations

import datetime as dt

from riskos.domain.entities import CalculationRun, MethodPack, ReconciliationCase, ResultPackage


def build_result_package(
    *,
    result_id: str,
    run: CalculationRun,
    method_pack: MethodPack,
    reconciliation: ReconciliationCase | None,
    now: dt.datetime,
) -> ResultPackage:
    assumptions = (
        f"method_pack={method_pack.method_id}@{method_pack.version} (PROVISIONAL, DEC-RSK-01)",
        f"supported_agreement_family={method_pack.supported_agreement_family}",
        f"rounding_rule={method_pack.rounding_rule}; mta_rounding_order={method_pack.mta_rounding_order}",
        "simulation_only: no real institution, agreement, LEI, or price is represented",
    )
    return ResultPackage(
        result_id=result_id,
        run_id=run.run_id,
        netting_set_id=run.netting_set_id,
        calculation_status=run.calculation_status,
        data_quality_status=run.data_quality_status,
        reconciliation_status=run.reconciliation_status,
        validation_status=run.validation_status,
        actionability=run.actionability,
        method_pack_id=method_pack.method_id,
        method_pack_version=method_pack.version,
        waterfall=run.waterfall,
        reconciliation=reconciliation,
        assumptions=assumptions,
        limitations=run.limitations,
        generated_at=now,
    )

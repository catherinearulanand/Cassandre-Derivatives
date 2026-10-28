"""JSON-safe (de)serialization for RiskOS domain aggregates, used by the
SQLAlchemy adapter to store each aggregate as (indexed scalar columns) +
(a JSON payload column holding the full structured body). Round-trips
`Money`/`Quantity` as explicit dicts (never as float), enums as `.value`,
and `date`/`datetime` as ISO strings.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from money_types import Money, Quantity

from riskos.domain.entities import (
    AgreementTermsPackage,
    CalculationRun,
    ChallengerComparison,
    ChallengerRun,
    CollateralPosition,
    CollateralStatePackage,
    CurvePoint,
    DiscountCurve,
    FxSpotRate,
    LegalEntityMasterVersion,
    MarginWaterfall,
    MarginWaterfallStep,
    MarketSnapshot,
    NettingSetVersion,
    PendingTransfer,
    PortfolioSnapshot,
    ReconciliationBreak,
    ReconciliationCase,
    SourceControlManifest,
    TradeMTM,
)
from riskos.domain.enums import (
    Actionability,
    AgreementSupportStatus,
    BreakType,
    CalculationPurpose,
    CalculationStatus,
    CallStatus,
    DataQualityStatus,
    DisputeAgreementCase,
    ReconciliationStatus,
    TradeLifecycleState,
    TradePopulationDisposition,
    UndisputedDeterminationStatus,
    ValidationStatus,
)


def _money(m: Money) -> dict[str, str]:
    return {"amount": str(m.amount), "currency": m.currency, "rounding_context": m.rounding_context}


def _unmoney(d: dict[str, str]) -> Money:
    return Money(amount=d["amount"], currency=d["currency"], rounding_context=d["rounding_context"])


def _opt_money(m: Money | None) -> dict[str, str] | None:
    return _money(m) if m is not None else None


def _unopt_money(d: dict[str, str] | None) -> Money | None:
    return _unmoney(d) if d is not None else None


def _quantity(q: Quantity | None) -> dict[str, str] | None:
    return {"amount": str(q.amount), "unit": q.unit} if q is not None else None


def _unquantity(d: dict[str, str] | None) -> Quantity | None:
    return Quantity(amount=d["amount"], unit=d["unit"]) if d is not None else None


# -- LegalEntityMasterVersion --------------------------------------------


def legal_entity_to_payload(e: LegalEntityMasterVersion) -> dict[str, Any]:
    return {
        "entity_id": e.entity_id,
        "display_name": e.display_name,
        "lei_shaped_id": e.lei_shaped_id,
        "home_currency": e.home_currency,
        "is_active_calculation_entity": e.is_active_calculation_entity,
        "version": e.version,
        "approved": e.approved,
    }


def legal_entity_from_payload(d: dict[str, Any]) -> LegalEntityMasterVersion:
    return LegalEntityMasterVersion(**d)


# -- NettingSetVersion -----------------------------------------------------


def netting_set_to_payload(n: NettingSetVersion) -> dict[str, Any]:
    return {
        "netting_set_id": n.netting_set_id,
        "calculation_entity_id": n.calculation_entity_id,
        "counterparty_entity_id": n.counterparty_entity_id,
        "governing_agreement_id": n.governing_agreement_id,
        "version": n.version,
        "effective_from": n.effective_from.isoformat(),
        "effective_to": n.effective_to.isoformat() if n.effective_to else None,
        "approved": n.approved,
    }


def netting_set_from_payload(d: dict[str, Any]) -> NettingSetVersion:
    return NettingSetVersion(
        netting_set_id=d["netting_set_id"],
        calculation_entity_id=d["calculation_entity_id"],
        counterparty_entity_id=d["counterparty_entity_id"],
        governing_agreement_id=d["governing_agreement_id"],
        version=d["version"],
        effective_from=dt.date.fromisoformat(d["effective_from"]),
        effective_to=dt.date.fromisoformat(d["effective_to"]) if d["effective_to"] else None,
        approved=d["approved"],
    )


# -- AgreementTermsPackage --------------------------------------------------


def agreement_terms_to_payload(a: AgreementTermsPackage) -> dict[str, Any]:
    return {
        "agreement_id": a.agreement_id,
        "netting_set_id": a.netting_set_id,
        "method_pack_id": a.method_pack_id,
        "call_currency": a.call_currency,
        "approved": a.approved,
        "approval_reference": a.approval_reference,
        "is_supported": a.is_supported,
    }


def agreement_terms_from_payload(d: dict[str, Any]) -> AgreementTermsPackage:
    return AgreementTermsPackage(**d)


# -- PortfolioSnapshot / TradeMTM -------------------------------------------


def trade_to_payload(t: TradeMTM) -> dict[str, Any]:
    return {
        "trade_id": t.trade_id,
        "calculation_entity_id": t.calculation_entity_id,
        "counterparty_entity_id": t.counterparty_entity_id,
        "netting_set_id": t.netting_set_id,
        "product_family": t.product_family,
        "lifecycle_state": t.lifecycle_state.value,
        "disposition": t.disposition.value,
        "mtm": _money(t.mtm),
    }


def trade_from_payload(d: dict[str, Any]) -> TradeMTM:
    return TradeMTM(
        trade_id=d["trade_id"],
        calculation_entity_id=d["calculation_entity_id"],
        counterparty_entity_id=d["counterparty_entity_id"],
        netting_set_id=d["netting_set_id"],
        product_family=d["product_family"],
        lifecycle_state=TradeLifecycleState(d["lifecycle_state"]),
        disposition=TradePopulationDisposition(d["disposition"]),
        mtm=_unmoney(d["mtm"]),
    )


def portfolio_snapshot_to_payload(p: PortfolioSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": p.snapshot_id,
        "as_of": p.as_of.isoformat(),
        "trades": [trade_to_payload(t) for t in p.trades],
    }


def portfolio_snapshot_from_payload(d: dict[str, Any]) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id=d["snapshot_id"],
        as_of=dt.datetime.fromisoformat(d["as_of"]),
        trades=tuple(trade_from_payload(t) for t in d["trades"]),
    )


def source_control_manifest_to_payload(m: SourceControlManifest) -> dict[str, Any]:
    return {
        "manifest_id": m.manifest_id,
        "expected_accepted_count": m.expected_accepted_count,
        "expected_rejected_count": m.expected_rejected_count,
        "expected_duplicate_count": m.expected_duplicate_count,
        "expected_matured_count": m.expected_matured_count,
        "expected_cancelled_count": m.expected_cancelled_count,
        "expected_amended_count": m.expected_amended_count,
        "expected_unsupported_count": m.expected_unsupported_count,
        "expected_accepted_mtm_total": _money(m.expected_accepted_mtm_total),
    }


def source_control_manifest_from_payload(d: dict[str, Any]) -> SourceControlManifest:
    return SourceControlManifest(
        manifest_id=d["manifest_id"],
        expected_accepted_count=d["expected_accepted_count"],
        expected_rejected_count=d["expected_rejected_count"],
        expected_duplicate_count=d["expected_duplicate_count"],
        expected_matured_count=d["expected_matured_count"],
        expected_cancelled_count=d["expected_cancelled_count"],
        expected_amended_count=d["expected_amended_count"],
        expected_unsupported_count=d["expected_unsupported_count"],
        expected_accepted_mtm_total=_unmoney(d["expected_accepted_mtm_total"]),
    )


# -- CollateralStatePackage --------------------------------------------------


def _position_to_payload(p: CollateralPosition) -> dict[str, Any]:
    return {
        "asset_or_cash_id": p.asset_or_cash_id,
        "currency": p.currency,
        "quantity": _quantity(p.quantity),
        "adjusted_value": _money(p.adjusted_value),
        "title_transfer_status": p.title_transfer_status,
        "security_interest_status": p.security_interest_status,
        "segregation_status": p.segregation_status,
        "reuse_status": p.reuse_status,
    }


def _position_from_payload(d: dict[str, Any]) -> CollateralPosition:
    return CollateralPosition(
        asset_or_cash_id=d["asset_or_cash_id"],
        currency=d["currency"],
        quantity=_unquantity(d["quantity"]),
        adjusted_value=_unmoney(d["adjusted_value"]),
        title_transfer_status=d["title_transfer_status"],
        security_interest_status=d["security_interest_status"],
        segregation_status=d["segregation_status"],
        reuse_status=d["reuse_status"],
    )


def _transfer_to_payload(t: PendingTransfer) -> dict[str, Any]:
    return {
        "transfer_id": t.transfer_id,
        "amount": _money(t.amount),
        "direction": t.direction,
        "status": t.status,
    }


def _transfer_from_payload(d: dict[str, Any]) -> PendingTransfer:
    return PendingTransfer(
        transfer_id=d["transfer_id"], amount=_unmoney(d["amount"]), direction=d["direction"], status=d["status"]
    )


def collateral_state_to_payload(c: CollateralStatePackage) -> dict[str, Any]:
    return {
        "package_id": c.package_id,
        "calculation_entity_id": c.calculation_entity_id,
        "netting_set_id": c.netting_set_id,
        "as_of": c.as_of.isoformat(),
        "source_watermark": c.source_watermark,
        "collateral_held": [_position_to_payload(p) for p in c.collateral_held],
        "collateral_posted": [_position_to_payload(p) for p in c.collateral_posted],
        "pending_inbound_transfers": [_transfer_to_payload(t) for t in c.pending_inbound_transfers],
        "pending_outbound_transfers": [_transfer_to_payload(t) for t in c.pending_outbound_transfers],
        "failed_transfers": [_transfer_to_payload(t) for t in c.failed_transfers],
        "cancelled_transfers": [_transfer_to_payload(t) for t in c.cancelled_transfers],
        "active_substitutions": list(c.active_substitutions),
        "return_legs": list(c.return_legs),
        "disputed_amount": _money(c.disputed_amount),
        "undisputed_amount": _opt_money(c.undisputed_amount),
        "valuation_source": c.valuation_source,
        "fx_source": c.fx_source,
        "custodian_reconciliation_status": c.custodian_reconciliation_status.value,
        "sign_convention": c.sign_convention,
        "accrued_interest": _opt_money(c.accrued_interest),
        "settled_balance": _opt_money(c.settled_balance),
    }


def collateral_state_from_payload(d: dict[str, Any]) -> CollateralStatePackage:
    return CollateralStatePackage(
        package_id=d["package_id"],
        calculation_entity_id=d["calculation_entity_id"],
        netting_set_id=d["netting_set_id"],
        as_of=dt.datetime.fromisoformat(d["as_of"]),
        source_watermark=d["source_watermark"],
        collateral_held=tuple(_position_from_payload(p) for p in d["collateral_held"]),
        collateral_posted=tuple(_position_from_payload(p) for p in d["collateral_posted"]),
        pending_inbound_transfers=tuple(_transfer_from_payload(t) for t in d["pending_inbound_transfers"]),
        pending_outbound_transfers=tuple(_transfer_from_payload(t) for t in d["pending_outbound_transfers"]),
        failed_transfers=tuple(_transfer_from_payload(t) for t in d["failed_transfers"]),
        cancelled_transfers=tuple(_transfer_from_payload(t) for t in d["cancelled_transfers"]),
        active_substitutions=tuple(d["active_substitutions"]),
        return_legs=tuple(d["return_legs"]),
        disputed_amount=_unmoney(d["disputed_amount"]),
        undisputed_amount=_unopt_money(d["undisputed_amount"]),
        valuation_source=d["valuation_source"],
        fx_source=d["fx_source"],
        custodian_reconciliation_status=ReconciliationStatus(d["custodian_reconciliation_status"]),
        sign_convention=d["sign_convention"],
        accrued_interest=_unopt_money(d.get("accrued_interest")),
        settled_balance=_unopt_money(d.get("settled_balance")),
    )


# -- MarginWaterfall / CalculationRun ----------------------------------------


def _waterfall_step_to_payload(s: MarginWaterfallStep) -> dict[str, Any]:
    return {"step_name": s.step_name, "value": _money(s.value), "note": s.note}


def _waterfall_step_from_payload(d: dict[str, Any]) -> MarginWaterfallStep:
    return MarginWaterfallStep(step_name=d["step_name"], value=_unmoney(d["value"]), note=d["note"])


def waterfall_to_payload(w: MarginWaterfall) -> dict[str, Any]:
    return {
        "run_id": w.run_id,
        "netting_set_id": w.netting_set_id,
        "exposure": _money(w.exposure),
        "threshold_calculation_party": _money(w.threshold_calculation_party),
        "threshold_counterparty": _money(w.threshold_counterparty),
        "independent_amount_net": _money(w.independent_amount_net),
        "credit_support_balance": _money(w.credit_support_balance),
        "minimum_transfer_amount": _money(w.minimum_transfer_amount),
        "call_status": w.call_status.value,
        "call_amount": _money(w.call_amount),
        "transferor_entity_id": w.transferor_entity_id,
        "transferee_entity_id": w.transferee_entity_id,
        "steps": [_waterfall_step_to_payload(s) for s in w.steps],
    }


def waterfall_from_payload(d: dict[str, Any]) -> MarginWaterfall:
    return MarginWaterfall(
        run_id=d["run_id"],
        netting_set_id=d["netting_set_id"],
        exposure=_unmoney(d["exposure"]),
        threshold_calculation_party=_unmoney(d["threshold_calculation_party"]),
        threshold_counterparty=_unmoney(d["threshold_counterparty"]),
        independent_amount_net=_unmoney(d["independent_amount_net"]),
        credit_support_balance=_unmoney(d["credit_support_balance"]),
        minimum_transfer_amount=_unmoney(d["minimum_transfer_amount"]),
        call_status=CallStatus(d["call_status"]),
        call_amount=_unmoney(d["call_amount"]),
        transferor_entity_id=d["transferor_entity_id"],
        transferee_entity_id=d["transferee_entity_id"],
        steps=tuple(_waterfall_step_from_payload(s) for s in d["steps"]),
    )


def calculation_run_to_payload(r: CalculationRun) -> dict[str, Any]:
    return {
        "run_id": r.run_id,
        "purpose": r.purpose.value,
        "netting_set_id": r.netting_set_id,
        "method_pack_id": r.method_pack_id,
        "calculation_status": r.calculation_status.value,
        "data_quality_status": r.data_quality_status.value,
        "reconciliation_status": r.reconciliation_status.value,
        "validation_status": r.validation_status.value,
        "actionability": r.actionability.value,
        "agreement_support_status": r.agreement_support_status.value,
        "created_at": r.created_at.isoformat(),
        "waterfall": waterfall_to_payload(r.waterfall) if r.waterfall else None,
        "reconciliation_case_id": r.reconciliation_case_id,
        "failure_reason": r.failure_reason,
        "limitations": list(r.limitations),
    }


def calculation_run_from_payload(d: dict[str, Any]) -> CalculationRun:
    return CalculationRun(
        run_id=d["run_id"],
        purpose=CalculationPurpose(d["purpose"]),
        netting_set_id=d["netting_set_id"],
        method_pack_id=d["method_pack_id"],
        calculation_status=CalculationStatus(d["calculation_status"]),
        data_quality_status=DataQualityStatus(d["data_quality_status"]),
        reconciliation_status=ReconciliationStatus(d["reconciliation_status"]),
        validation_status=ValidationStatus(d["validation_status"]),
        actionability=Actionability(d["actionability"]),
        agreement_support_status=AgreementSupportStatus(d["agreement_support_status"]),
        created_at=dt.datetime.fromisoformat(d["created_at"]),
        waterfall=waterfall_from_payload(d["waterfall"]) if d["waterfall"] else None,
        reconciliation_case_id=d["reconciliation_case_id"],
        failure_reason=d["failure_reason"],
        limitations=tuple(d["limitations"]),
    )


def _break_to_payload(b: ReconciliationBreak) -> dict[str, Any]:
    return {
        "break_id": b.break_id,
        "break_type": b.break_type.value,
        "description": b.description,
        "calculation_party_amount": _money(b.calculation_party_amount),
        "counterparty_amount": _money(b.counterparty_amount),
        "difference": _money(b.difference),
    }


def _break_from_payload(d: dict[str, Any]) -> ReconciliationBreak:
    return ReconciliationBreak(
        break_id=d["break_id"],
        break_type=BreakType(d["break_type"]),
        description=d["description"],
        calculation_party_amount=_unmoney(d["calculation_party_amount"]),
        counterparty_amount=_unmoney(d["counterparty_amount"]),
        difference=_unmoney(d["difference"]),
    )


def reconciliation_case_to_payload(c: ReconciliationCase) -> dict[str, Any]:
    return {
        "case_id": c.case_id,
        "run_id": c.run_id,
        "calculation_party_call_status": c.calculation_party_call_status.value,
        "calculation_party_call_amount": _money(c.calculation_party_call_amount),
        "counterparty_call_status": c.counterparty_call_status.value,
        "counterparty_call_amount": _money(c.counterparty_call_amount),
        "dispute_case": c.dispute_case.value,
        "undisputed_status": c.undisputed_status.value,
        "undisputed_amount": _opt_money(c.undisputed_amount),
        "reconciliation_status": c.reconciliation_status.value,
        "breaks": [_break_to_payload(b) for b in c.breaks],
    }


def reconciliation_case_from_payload(d: dict[str, Any]) -> ReconciliationCase:
    return ReconciliationCase(
        case_id=d["case_id"],
        run_id=d["run_id"],
        calculation_party_call_status=CallStatus(d["calculation_party_call_status"]),
        calculation_party_call_amount=_unmoney(d["calculation_party_call_amount"]),
        counterparty_call_status=CallStatus(d["counterparty_call_status"]),
        counterparty_call_amount=_unmoney(d["counterparty_call_amount"]),
        dispute_case=DisputeAgreementCase(d["dispute_case"]),
        undisputed_status=UndisputedDeterminationStatus(d["undisputed_status"]),
        undisputed_amount=_unopt_money(d["undisputed_amount"]),
        reconciliation_status=ReconciliationStatus(d["reconciliation_status"]),
        breaks=tuple(_break_from_payload(b) for b in d["breaks"]),
    )


# -- RSK-X01 native valuation challenger: MarketSnapshot / ChallengerRun ----
# Additive only; nothing above this section is touched or depended on by
# these functions, and nothing above depends on these.


def _curve_point_to_payload(p: CurvePoint) -> dict[str, str]:
    return {"tenor_years": str(p.tenor_years), "zero_rate": str(p.zero_rate)}


def _curve_point_from_payload(d: dict[str, str]) -> CurvePoint:
    return CurvePoint(tenor_years=Decimal(d["tenor_years"]), zero_rate=Decimal(d["zero_rate"]))


def _discount_curve_to_payload(c: DiscountCurve) -> dict[str, Any]:
    return {"currency": c.currency, "points": [_curve_point_to_payload(p) for p in c.points]}


def _discount_curve_from_payload(d: dict[str, Any]) -> DiscountCurve:
    return DiscountCurve(currency=d["currency"], points=tuple(_curve_point_from_payload(p) for p in d["points"]))


def _fx_spot_rate_to_payload(f: FxSpotRate) -> dict[str, str]:
    return {"base_currency": f.base_currency, "quote_currency": f.quote_currency, "rate": str(f.rate)}


def _fx_spot_rate_from_payload(d: dict[str, str]) -> FxSpotRate:
    return FxSpotRate(base_currency=d["base_currency"], quote_currency=d["quote_currency"], rate=Decimal(d["rate"]))


def market_snapshot_to_payload(m: MarketSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": m.snapshot_id,
        "as_of": m.as_of.isoformat(),
        "calendars": list(m.calendars),
        "discount_curves": [_discount_curve_to_payload(c) for c in m.discount_curves],
        "fx_spot_rates": [_fx_spot_rate_to_payload(f) for f in m.fx_spot_rates],
    }


def market_snapshot_from_payload(d: dict[str, Any]) -> MarketSnapshot:
    return MarketSnapshot(
        snapshot_id=d["snapshot_id"],
        as_of=dt.date.fromisoformat(d["as_of"]),
        calendars=tuple(d["calendars"]),
        discount_curves=tuple(_discount_curve_from_payload(c) for c in d["discount_curves"]),
        fx_spot_rates=tuple(_fx_spot_rate_from_payload(f) for f in d["fx_spot_rates"]),
    )


def challenger_comparison_to_payload(c: ChallengerComparison) -> dict[str, Any]:
    return {
        "trade_id": c.trade_id,
        "product_family": c.product_family,
        "challenger_pv": _opt_money(c.challenger_pv),
        "incumbent_pv": _opt_money(c.incumbent_pv),
        "difference": _opt_money(c.difference),
        "tolerance": _opt_money(c.tolerance),
        "comparison_status": c.comparison_status.value,
        "note": c.note,
    }


def challenger_comparison_from_payload(d: dict[str, Any]) -> ChallengerComparison:
    return ChallengerComparison(
        trade_id=d["trade_id"],
        product_family=d["product_family"],
        challenger_pv=_unopt_money(d["challenger_pv"]),
        incumbent_pv=_unopt_money(d["incumbent_pv"]),
        difference=_unopt_money(d["difference"]),
        tolerance=_unopt_money(d["tolerance"]),
        comparison_status=ReconciliationStatus(d["comparison_status"]),
        note=d.get("note"),
    )


def challenger_run_to_payload(r: ChallengerRun) -> dict[str, Any]:
    return {
        "run_id": r.run_id,
        "netting_set_id": r.netting_set_id,
        "market_snapshot_id": r.market_snapshot_id,
        "calculation_status": r.calculation_status.value,
        "data_quality_status": r.data_quality_status.value,
        "reconciliation_status": r.reconciliation_status.value,
        "validation_status": r.validation_status.value,
        "actionability": r.actionability.value,
        "comparisons": [challenger_comparison_to_payload(c) for c in r.comparisons],
        "created_at": r.created_at.isoformat(),
        "limitations": list(r.limitations),
        "failure_reason": r.failure_reason,
    }


def challenger_run_from_payload(d: dict[str, Any]) -> ChallengerRun:
    return ChallengerRun(
        run_id=d["run_id"],
        netting_set_id=d["netting_set_id"],
        market_snapshot_id=d["market_snapshot_id"],
        calculation_status=CalculationStatus(d["calculation_status"]),
        data_quality_status=DataQualityStatus(d["data_quality_status"]),
        reconciliation_status=ReconciliationStatus(d["reconciliation_status"]),
        validation_status=ValidationStatus(d["validation_status"]),
        actionability=Actionability(d["actionability"]),
        comparisons=tuple(challenger_comparison_from_payload(c) for c in d["comparisons"]),
        created_at=dt.datetime.fromisoformat(d["created_at"]),
        limitations=tuple(d["limitations"]),
        failure_reason=d.get("failure_reason"),
    )

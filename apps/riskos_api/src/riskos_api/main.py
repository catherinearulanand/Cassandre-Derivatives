"""RiskOS minimal API (plan Section 11.8 / this session's Section 25.5
slice subset). A thin composition root: request/response translation only,
no business logic (that lives in `riskos.core`/`riskos.application`).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from riskos.adapters.codec import (
    agreement_terms_to_payload,
    calculation_run_to_payload,
    challenger_run_to_payload,
    collateral_state_to_payload,
    legal_entity_to_payload,
    market_snapshot_to_payload,
    netting_set_to_payload,
    portfolio_snapshot_to_payload,
    reconciliation_case_to_payload,
    waterfall_to_payload,
)
from riskos.application.service import (
    RiskOSAuthorizationDeniedError,
    RiskOSNotFoundError,
    RiskOSRevocationFenceError,
    RiskOSService,
)
from riskos.core.reconciliation import PartyCallView
from riskos.domain.entities import (
    AgreementTermsPackage,
    CollateralPosition,
    CollateralStatePackage,
    CurvePoint,
    DiscountCurve,
    FxForwardTerms,
    FxSpotRate,
    InterestRateSwapTerms,
    LegalEntityMasterVersion,
    MarketSnapshot,
    NettingSetVersion,
    PendingTransfer,
    PortfolioSnapshot,
    SourceControlManifest,
    TradeMTM,
)
from riskos.domain.enums import (
    CalculationPurpose,
    CallStatus,
    ReconciliationStatus,
    TradeLifecycleState,
    TradePopulationDisposition,
)

from riskos_api.dependencies import get_service
from riskos_api.schemas import (
    AgreementTermsIn,
    CalculationRunIn,
    ChallengerRunIn,
    CollateralStatePackageIn,
    LegalEntityVersionIn,
    MarketSnapshotIn,
    NettingSetVersionIn,
    PartyCallViewIn,
    PortfolioSnapshotIn,
    ReconciliationIn,
    ResultPackageExportIn,
)

app = FastAPI(title="RiskOS API", version="0.1.0")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _party_call_view_from_in(body: PartyCallViewIn) -> PartyCallView:
    try:
        call_status = CallStatus(body.call_status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid call_status {body.call_status!r}") from exc
    return PartyCallView(
        party_label=body.party_label,
        call_status=call_status,
        call_amount=body.call_amount,
        exposure=body.exposure,
        credit_support_balance=body.credit_support_balance,
        trade_population_count=body.trade_population_count,
        as_of=body.as_of,
        acknowledged_amount=body.acknowledged_amount,
        responded=body.responded,
    )


@app.post("/v1/legal-entity-versions", status_code=201)
def create_legal_entity_version(body: LegalEntityVersionIn, service: RiskOSService = Depends(get_service)) -> dict:
    entity = LegalEntityMasterVersion(
        entity_id=body.entity_id,
        display_name=body.display_name,
        lei_shaped_id=body.lei_shaped_id,
        home_currency=body.home_currency,
        is_active_calculation_entity=body.is_active_calculation_entity,
        version=body.version,
        approved=body.approved,
    )
    try:
        service.register_legal_entity(entity, now=_now())
    except RiskOSAuthorizationDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"entity_id": entity.entity_id, "version": entity.version, "approved": entity.approved}


@app.get("/v1/legal-entity-versions")
def list_legal_entity_versions(service: RiskOSService = Depends(get_service)) -> dict:
    return {"legal_entities": [legal_entity_to_payload(e) for e in service.list_legal_entities()]}


@app.get("/v1/legal-entity-versions/{entity_id}")
def get_legal_entity_version(entity_id: str, service: RiskOSService = Depends(get_service)) -> dict:
    try:
        entity = service.get_legal_entity(entity_id)
    except RiskOSNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return legal_entity_to_payload(entity)


@app.post("/v1/netting-set-versions", status_code=201)
def create_netting_set_version(body: NettingSetVersionIn, service: RiskOSService = Depends(get_service)) -> dict:
    netting_set = NettingSetVersion(
        netting_set_id=body.netting_set_id,
        calculation_entity_id=body.calculation_entity_id,
        counterparty_entity_id=body.counterparty_entity_id,
        governing_agreement_id=body.governing_agreement_id,
        version=body.version,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        approved=body.approved,
    )
    service.register_netting_set(netting_set, now=_now())
    return {"netting_set_id": netting_set.netting_set_id, "version": netting_set.version}


@app.get("/v1/netting-set-versions")
def list_netting_set_versions(service: RiskOSService = Depends(get_service)) -> dict:
    return {"netting_sets": [netting_set_to_payload(n) for n in service.list_netting_sets()]}


@app.post("/v1/agreement-terms:validate")
def validate_agreement_terms(body: AgreementTermsIn, service: RiskOSService = Depends(get_service)) -> dict:
    terms = AgreementTermsPackage(
        agreement_id=body.agreement_id,
        netting_set_id=body.netting_set_id,
        method_pack_id=body.method_pack_id,
        call_currency=body.call_currency,
        approved=False,
        approval_reference=body.approval_reference,
        is_supported=body.is_supported,
    )
    ok, reasons = service.validate_agreement_terms(terms)
    return {"agreement_id": terms.agreement_id, "valid": ok, "reasons": list(reasons)}


@app.post("/v1/agreement-terms:approve", status_code=201)
def approve_agreement_terms(body: AgreementTermsIn, service: RiskOSService = Depends(get_service)) -> dict:
    terms = AgreementTermsPackage(
        agreement_id=body.agreement_id,
        netting_set_id=body.netting_set_id,
        method_pack_id=body.method_pack_id,
        call_currency=body.call_currency,
        approved=False,
        approval_reference=body.approval_reference,
        is_supported=body.is_supported,
    )
    try:
        approved = service.approve_agreement_terms(terms, now=_now())
    except RiskOSAuthorizationDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "agreement_id": approved.agreement_id,
        "approved": approved.approved,
        "approval_reference": approved.approval_reference,
    }


@app.get("/v1/agreement-terms/{agreement_id}")
def get_agreement_terms(agreement_id: str, service: RiskOSService = Depends(get_service)) -> dict:
    try:
        terms = service.get_agreement_terms(agreement_id)
    except RiskOSNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return agreement_terms_to_payload(terms)


@app.post("/v1/portfolio-snapshots", status_code=201)
def create_portfolio_snapshot(body: PortfolioSnapshotIn, service: RiskOSService = Depends(get_service)) -> dict:
    try:
        trades = tuple(
            TradeMTM(
                trade_id=t.trade_id,
                calculation_entity_id=t.calculation_entity_id,
                counterparty_entity_id=t.counterparty_entity_id,
                netting_set_id=t.netting_set_id,
                product_family=t.product_family,
                lifecycle_state=TradeLifecycleState(t.lifecycle_state),
                disposition=TradePopulationDisposition(t.disposition),
                mtm=t.mtm,
            )
            for t in body.trades
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    snapshot = PortfolioSnapshot(snapshot_id=body.snapshot_id, as_of=body.as_of, trades=trades)
    service.ingest_portfolio_snapshot(snapshot, now=_now())
    return {"snapshot_id": snapshot.snapshot_id, "trade_count": len(snapshot.trades)}


@app.get("/v1/portfolio-snapshots/{snapshot_id}")
def get_portfolio_snapshot(snapshot_id: str, service: RiskOSService = Depends(get_service)) -> dict:
    try:
        snapshot = service.get_portfolio_snapshot(snapshot_id)
    except RiskOSNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return portfolio_snapshot_to_payload(snapshot)


@app.post("/v1/collateral-state-packages", status_code=201)
def create_collateral_state_package(
    body: CollateralStatePackageIn, service: RiskOSService = Depends(get_service)
) -> dict:
    try:
        custodian_status = ReconciliationStatus(body.custodian_reconciliation_status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _position(p) -> CollateralPosition:
        return CollateralPosition(
            asset_or_cash_id=p.asset_or_cash_id,
            currency=p.currency,
            quantity=p.quantity,
            adjusted_value=p.adjusted_value,
            title_transfer_status=p.title_transfer_status,
            security_interest_status=p.security_interest_status,
            segregation_status=p.segregation_status,
            reuse_status=p.reuse_status,
        )

    def _transfer(t) -> PendingTransfer:
        return PendingTransfer(transfer_id=t.transfer_id, amount=t.amount, direction=t.direction, status=t.status)

    package = CollateralStatePackage(
        package_id=body.package_id,
        calculation_entity_id=body.calculation_entity_id,
        netting_set_id=body.netting_set_id,
        as_of=body.as_of,
        source_watermark=body.source_watermark,
        collateral_held=tuple(_position(p) for p in body.collateral_held),
        collateral_posted=tuple(_position(p) for p in body.collateral_posted),
        pending_inbound_transfers=tuple(_transfer(t) for t in body.pending_inbound_transfers),
        pending_outbound_transfers=tuple(_transfer(t) for t in body.pending_outbound_transfers),
        failed_transfers=tuple(_transfer(t) for t in body.failed_transfers),
        cancelled_transfers=tuple(_transfer(t) for t in body.cancelled_transfers),
        active_substitutions=tuple(body.active_substitutions),
        return_legs=tuple(body.return_legs),
        disputed_amount=body.disputed_amount,
        undisputed_amount=body.undisputed_amount,
        valuation_source=body.valuation_source,
        fx_source=body.fx_source,
        custodian_reconciliation_status=custodian_status,
        sign_convention=body.sign_convention,
        accrued_interest=body.accrued_interest,
        settled_balance=body.settled_balance,
    )
    service.ingest_collateral_state(package, now=_now())
    return {"package_id": package.package_id}


@app.get("/v1/collateral-state-packages/{package_id}")
def get_collateral_state_package(package_id: str, service: RiskOSService = Depends(get_service)) -> dict:
    try:
        package = service.get_collateral_state(package_id)
    except RiskOSNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return collateral_state_to_payload(package)


@app.get("/v1/method-packs")
def list_method_packs(service: RiskOSService = Depends(get_service)) -> dict:
    packs = service.list_method_packs()
    return {
        "method_packs": [
            {
                "method_id": p.method_id,
                "version": p.version,
                "supported_agreement_family": p.supported_agreement_family,
                "call_currency": p.call_currency,
                "minimum_transfer_amount": {
                    "amount": str(p.minimum_transfer_amount.amount),
                    "currency": p.minimum_transfer_amount.currency,
                },
                "validation_status": p.validation_status,
                "provisional": p.provisional,
            }
            for p in packs
        ]
    }


@app.post("/v1/calculation-runs", status_code=201)
def create_calculation_run(body: CalculationRunIn, service: RiskOSService = Depends(get_service)) -> dict:
    try:
        purpose = CalculationPurpose(body.purpose)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid purpose {body.purpose!r}") from exc

    manifest = SourceControlManifest(
        manifest_id=body.manifest.manifest_id,
        expected_accepted_count=body.manifest.expected_accepted_count,
        expected_rejected_count=body.manifest.expected_rejected_count,
        expected_duplicate_count=body.manifest.expected_duplicate_count,
        expected_matured_count=body.manifest.expected_matured_count,
        expected_cancelled_count=body.manifest.expected_cancelled_count,
        expected_amended_count=body.manifest.expected_amended_count,
        expected_unsupported_count=body.manifest.expected_unsupported_count,
        expected_accepted_mtm_total=body.manifest.expected_accepted_mtm_total,
    )
    counterparty_call = _party_call_view_from_in(body.counterparty_call)

    try:
        run = service.run_calculation(
            run_id=body.run_id,
            purpose=purpose,
            calculation_entity_id=body.calculation_entity_id,
            counterparty_entity_id=body.counterparty_entity_id,
            agreement_id=body.agreement_id,
            portfolio_snapshot_id=body.portfolio_snapshot_id,
            collateral_package_id=body.collateral_package_id,
            manifest=manifest,
            counterparty_call=counterparty_call,
            now=_now(),
        )
    except RiskOSNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RiskOSAuthorizationDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RiskOSRevocationFenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return calculation_run_to_payload(run)


@app.get("/v1/calculation-runs/{run_id}")
def get_calculation_run(run_id: str, service: RiskOSService = Depends(get_service)) -> dict:
    try:
        run = service.get_calculation_run(run_id)
    except RiskOSNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return calculation_run_to_payload(run)


@app.get("/v1/calculation-runs/{run_id}/waterfall")
def get_calculation_run_waterfall(run_id: str, service: RiskOSService = Depends(get_service)) -> dict:
    try:
        run = service.get_calculation_run(run_id)
    except RiskOSNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if run.waterfall is None:
        raise HTTPException(status_code=404, detail=f"calculation run {run_id!r} has no waterfall (run did not complete)")
    return waterfall_to_payload(run.waterfall)


@app.post("/v1/reconciliations", status_code=201)
def submit_reconciliation(body: ReconciliationIn, service: RiskOSService = Depends(get_service)) -> dict:
    counterparty_call = _party_call_view_from_in(body.counterparty_call)
    try:
        case = service.submit_reconciliation(run_id=body.run_id, counterparty_call=counterparty_call, now=_now())
    except RiskOSNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RiskOSAuthorizationDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return reconciliation_case_to_payload(case)


@app.get("/v1/reconciliation-cases/{case_id}")
def get_reconciliation_case(case_id: str, service: RiskOSService = Depends(get_service)) -> dict:
    try:
        case = service.get_reconciliation_case(case_id)
    except RiskOSNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return reconciliation_case_to_payload(case)


@app.post("/v1/result-packages:export")
def export_result_package(body: ResultPackageExportIn, service: RiskOSService = Depends(get_service)) -> dict:
    try:
        envelope = service.export_result_package(run_id=body.run_id, now=_now())
    except RiskOSNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RiskOSAuthorizationDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RiskOSRevocationFenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return envelope.model_dump(mode="json")


# -- RSK-X01 native valuation challenger (additive; see
# `riskos.core.challenger_workflow` / `docs/methods/RSK-X01-challenger-
# provisional.md`). These routes never call into, and are never called by,
# the MARGIN_CALL routes above. -------------------------------------------


@app.post("/v1/market-snapshots", status_code=201)
def create_market_snapshot(body: MarketSnapshotIn, service: RiskOSService = Depends(get_service)) -> dict:
    snapshot = MarketSnapshot(
        snapshot_id=body.snapshot_id,
        as_of=body.as_of,
        calendars=tuple(body.calendars),
        discount_curves=tuple(
            DiscountCurve(
                currency=c.currency,
                points=tuple(CurvePoint(tenor_years=p.tenor_years, zero_rate=p.zero_rate) for p in c.points),
            )
            for c in body.discount_curves
        ),
        fx_spot_rates=tuple(
            FxSpotRate(base_currency=f.base_currency, quote_currency=f.quote_currency, rate=f.rate)
            for f in body.fx_spot_rates
        ),
    )
    service.ingest_market_snapshot(snapshot, now=_now())
    return {"snapshot_id": snapshot.snapshot_id}


@app.get("/v1/market-snapshots/{snapshot_id}")
def get_market_snapshot(snapshot_id: str, service: RiskOSService = Depends(get_service)) -> dict:
    try:
        snapshot = service.get_market_snapshot(snapshot_id)
    except RiskOSNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return market_snapshot_to_payload(snapshot)


@app.post("/v1/challenger-runs", status_code=201)
def create_challenger_run(body: ChallengerRunIn, service: RiskOSService = Depends(get_service)) -> dict:
    irs_terms = tuple(
        InterestRateSwapTerms(
            trade_id=t.trade_id,
            calculation_entity_id=t.calculation_entity_id,
            counterparty_entity_id=t.counterparty_entity_id,
            netting_set_id=t.netting_set_id,
            currency=t.currency,
            notional=t.notional,
            fixed_rate=t.fixed_rate,
            calculation_party_pays_fixed=t.calculation_party_pays_fixed,
            effective_date=t.effective_date,
            maturity_date=t.maturity_date,
            payment_frequency_months=t.payment_frequency_months,
        )
        for t in body.irs_trades
    )
    fx_terms = tuple(
        FxForwardTerms(
            trade_id=t.trade_id,
            calculation_entity_id=t.calculation_entity_id,
            counterparty_entity_id=t.counterparty_entity_id,
            netting_set_id=t.netting_set_id,
            base_currency=t.base_currency,
            quote_currency=t.quote_currency,
            notional_base=t.notional_base,
            contract_rate=t.contract_rate,
            calculation_party_buys_base=t.calculation_party_buys_base,
            value_date=t.value_date,
        )
        for t in body.fx_forward_trades
    )
    try:
        run = service.run_challenger_valuation(
            run_id=body.run_id,
            netting_set_id=body.netting_set_id,
            market_snapshot_id=body.market_snapshot_id,
            as_of=body.as_of,
            now=_now(),
            portfolio_snapshot_id=body.portfolio_snapshot_id,
            irs_trades=irs_terms,
            fx_forward_trades=fx_terms,
        )
    except RiskOSNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RiskOSAuthorizationDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return challenger_run_to_payload(run)


@app.get("/v1/challenger-runs/{run_id}")
def get_challenger_run(run_id: str, service: RiskOSService = Depends(get_service)) -> dict:
    try:
        run = service.get_challenger_run(run_id)
    except RiskOSNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return challenger_run_to_payload(run)


# -- RiskOS web UI (plan Section 11.9): plain HTML/CSS/vanilla-JS static
# assets, served from this same process so one `uvicorn riskos_api.main:app`
# serves both the API and the UI. Mounted last so it never shadows a
# `/v1/...` API route registered above. Defensive about a missing directory
# (e.g. a packaged/installed deployment that does not ship the sibling
# `apps/riskos_web` source directory) so import never crashes the API.
_RISKOS_WEB_DIR = Path(__file__).resolve().parents[3] / "riskos_web"
if _RISKOS_WEB_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_RISKOS_WEB_DIR), html=True), name="riskos_web")

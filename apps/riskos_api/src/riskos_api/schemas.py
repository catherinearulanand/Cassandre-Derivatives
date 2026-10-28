"""Pydantic request schemas for the RiskOS minimal API (plan Section 11.8).
`money_types.Money`/`Quantity` are themselves Pydantic models, so request
bodies embed them directly rather than re-declaring amount/currency fields.
Responses are plain JSON-safe dicts built by `riskos.adapters.codec` /
`riskos.application.serialization` — no separate response schema
duplication.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from money_types import Money, Quantity
from pydantic import BaseModel


class LegalEntityVersionIn(BaseModel):
    entity_id: str
    display_name: str
    lei_shaped_id: str
    home_currency: str
    is_active_calculation_entity: bool
    version: str
    approved: bool = False


class NettingSetVersionIn(BaseModel):
    netting_set_id: str
    calculation_entity_id: str
    counterparty_entity_id: str
    governing_agreement_id: str
    version: str
    effective_from: dt.date
    effective_to: dt.date | None = None
    approved: bool = False


class AgreementTermsIn(BaseModel):
    agreement_id: str
    netting_set_id: str
    method_pack_id: str
    call_currency: str
    approval_reference: str | None = None
    is_supported: bool = True


class TradeMTMIn(BaseModel):
    trade_id: str
    calculation_entity_id: str
    counterparty_entity_id: str
    netting_set_id: str
    product_family: str
    lifecycle_state: str
    disposition: str
    mtm: Money


class PortfolioSnapshotIn(BaseModel):
    snapshot_id: str
    as_of: dt.datetime
    trades: list[TradeMTMIn]


class CollateralPositionIn(BaseModel):
    asset_or_cash_id: str
    currency: str
    quantity: Quantity | None = None
    adjusted_value: Money
    title_transfer_status: str
    security_interest_status: str
    segregation_status: str
    reuse_status: str


class PendingTransferIn(BaseModel):
    transfer_id: str
    amount: Money
    direction: str
    status: str


class CollateralStatePackageIn(BaseModel):
    package_id: str
    calculation_entity_id: str
    netting_set_id: str
    as_of: dt.datetime
    source_watermark: str
    collateral_held: list[CollateralPositionIn] = []
    collateral_posted: list[CollateralPositionIn] = []
    pending_inbound_transfers: list[PendingTransferIn] = []
    pending_outbound_transfers: list[PendingTransferIn] = []
    failed_transfers: list[PendingTransferIn] = []
    cancelled_transfers: list[PendingTransferIn] = []
    active_substitutions: list[str] = []
    return_legs: list[str] = []
    disputed_amount: Money
    undisputed_amount: Money | None = None
    valuation_source: str
    fx_source: str
    custodian_reconciliation_status: str
    sign_convention: str
    accrued_interest: Money | None = None
    settled_balance: Money | None = None


class SourceControlManifestIn(BaseModel):
    manifest_id: str
    expected_accepted_count: int
    expected_rejected_count: int = 0
    expected_duplicate_count: int = 0
    expected_matured_count: int = 0
    expected_cancelled_count: int = 0
    expected_amended_count: int = 0
    expected_unsupported_count: int = 0
    expected_accepted_mtm_total: Money


class PartyCallViewIn(BaseModel):
    party_label: str
    call_status: str
    call_amount: Money
    exposure: Money | None = None
    credit_support_balance: Money | None = None
    trade_population_count: int | None = None
    as_of: dt.datetime | None = None
    acknowledged_amount: Money | None = None
    responded: bool = True


class CalculationRunIn(BaseModel):
    run_id: str
    purpose: str
    calculation_entity_id: str
    counterparty_entity_id: str
    agreement_id: str
    portfolio_snapshot_id: str
    collateral_package_id: str
    manifest: SourceControlManifestIn
    counterparty_call: PartyCallViewIn


class ReconciliationIn(BaseModel):
    run_id: str
    counterparty_call: PartyCallViewIn


class ResultPackageExportIn(BaseModel):
    run_id: str


# -- RSK-X01 native valuation challenger (additive) --------------------------


class CurvePointIn(BaseModel):
    tenor_years: Decimal
    zero_rate: Decimal


class DiscountCurveIn(BaseModel):
    currency: str
    points: list[CurvePointIn]


class FxSpotRateIn(BaseModel):
    base_currency: str
    quote_currency: str
    rate: Decimal


class MarketSnapshotIn(BaseModel):
    snapshot_id: str
    as_of: dt.date
    calendars: list[str] = []
    discount_curves: list[DiscountCurveIn]
    fx_spot_rates: list[FxSpotRateIn] = []


class InterestRateSwapTermsIn(BaseModel):
    trade_id: str
    calculation_entity_id: str
    counterparty_entity_id: str
    netting_set_id: str
    currency: str
    notional: Decimal
    fixed_rate: Decimal
    calculation_party_pays_fixed: bool
    effective_date: dt.date
    maturity_date: dt.date
    payment_frequency_months: int = 12


class FxForwardTermsIn(BaseModel):
    trade_id: str
    calculation_entity_id: str
    counterparty_entity_id: str
    netting_set_id: str
    base_currency: str
    quote_currency: str
    notional_base: Decimal
    contract_rate: Decimal
    calculation_party_buys_base: bool
    value_date: dt.date


class ChallengerRunIn(BaseModel):
    run_id: str
    netting_set_id: str
    market_snapshot_id: str
    as_of: dt.date
    portfolio_snapshot_id: str | None = None
    irs_trades: list[InterestRateSwapTermsIn] = []
    fx_forward_trades: list[FxForwardTermsIn] = []

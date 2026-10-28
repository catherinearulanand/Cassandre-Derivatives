"""RiskOS domain aggregates, per plan Section 11.1. Pure Python: no
`fastapi`, no `sqlalchemy`, no clock, no I/O (enforced by
`scripts/check_import_graph.py`). Every monetary field is `money_types.Money`
— never a float or int — per plan Section 25.3.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from money_types import Money, Quantity

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


@dataclass(frozen=True)
class LegalEntityMasterVersion:
    entity_id: str
    display_name: str
    lei_shaped_id: str
    home_currency: str
    is_active_calculation_entity: bool
    version: str
    approved: bool


@dataclass(frozen=True)
class NettingSetVersion:
    netting_set_id: str
    calculation_entity_id: str
    counterparty_entity_id: str
    governing_agreement_id: str
    version: str
    effective_from: dt.date
    effective_to: dt.date | None
    approved: bool


@dataclass(frozen=True)
class AgreementTermsPackage:
    """Manually authored/approved terms (plan 3.2/11.3 step 1: "Ingest
    approved manual/JSON/CSV agreement terms"). `is_supported=False` or a
    `method_pack_id` outside the implemented pack means this package
    resolves to `UNSUPPORTED_AGREEMENT`, never a guessed formula."""

    agreement_id: str
    netting_set_id: str
    method_pack_id: str
    call_currency: str
    approved: bool
    approval_reference: str | None
    is_supported: bool


@dataclass(frozen=True)
class TradeMTM:
    """One trade-level MTM from the synthetic incumbent source, calculation-
    party perspective (positive = calculation party is owed)."""

    trade_id: str
    calculation_entity_id: str
    counterparty_entity_id: str
    netting_set_id: str
    product_family: str
    lifecycle_state: TradeLifecycleState
    disposition: TradePopulationDisposition
    mtm: Money


@dataclass(frozen=True)
class PortfolioSnapshot:
    snapshot_id: str
    as_of: dt.datetime
    trades: tuple[TradeMTM, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SourceControlManifest:
    """Plan Section 9.3/11.3: the synthetic source-control totals RiskOS must
    reconcile its resolved population against before aggregation. Authored
    independently of the calculation code under test."""

    manifest_id: str
    expected_accepted_count: int
    expected_rejected_count: int
    expected_duplicate_count: int
    expected_matured_count: int
    expected_cancelled_count: int
    expected_amended_count: int
    expected_unsupported_count: int
    expected_accepted_mtm_total: Money


@dataclass(frozen=True)
class CollateralPosition:
    asset_or_cash_id: str
    currency: str
    quantity: Quantity | None
    adjusted_value: Money
    title_transfer_status: str
    security_interest_status: str
    segregation_status: str
    reuse_status: str


@dataclass(frozen=True)
class PendingTransfer:
    transfer_id: str
    amount: Money
    direction: str
    status: str


@dataclass(frozen=True)
class CollateralStatePackage:
    """Plan Section 11.3's full component list, from one explicitly named
    calculation-party perspective. Fields that are empty/zero for this
    slice's fixture are still explicit typed fields, not omitted."""

    package_id: str
    calculation_entity_id: str
    netting_set_id: str
    as_of: dt.datetime
    source_watermark: str
    collateral_held: tuple[CollateralPosition, ...]
    collateral_posted: tuple[CollateralPosition, ...]
    pending_inbound_transfers: tuple[PendingTransfer, ...]
    pending_outbound_transfers: tuple[PendingTransfer, ...]
    failed_transfers: tuple[PendingTransfer, ...]
    cancelled_transfers: tuple[PendingTransfer, ...]
    active_substitutions: tuple[str, ...]
    return_legs: tuple[str, ...]
    disputed_amount: Money
    undisputed_amount: Money | None
    valuation_source: str
    fx_source: str
    custodian_reconciliation_status: ReconciliationStatus
    sign_convention: str
    accrued_interest: Money | None = None
    settled_balance: Money | None = None


@dataclass(frozen=True)
class MethodPack:
    """Plan Section 11.4 — a versioned, typed pack, never a magic dict.
    Encodes `docs/methods/METHOD-PACK-001-provisional.md` (DEC-RSK-01).
    PROVISIONAL: a synthetic placeholder convention, not an approved
    production margin methodology."""

    method_id: str
    version: str
    supported_agreement_family: str
    jurisdiction_label: str
    effective_from: dt.date
    threshold_calculation_party: Money
    threshold_counterparty: Money
    independent_amount_calculation_party: Money
    independent_amount_counterparty: Money
    minimum_transfer_amount: Money
    mta_rounding_order: str
    rounding_rule: str
    call_currency: str
    lower_common_amount_authorized: bool
    validation_status: str
    golden_case_reference: str
    owner: str
    maker: str
    checker: str
    approval_hash: str
    provisional: bool = True


@dataclass(frozen=True)
class MarginWaterfallStep:
    step_name: str
    value: Money
    note: str | None = None


@dataclass(frozen=True)
class MarginWaterfall:
    run_id: str
    netting_set_id: str
    exposure: Money
    threshold_calculation_party: Money
    threshold_counterparty: Money
    independent_amount_net: Money
    credit_support_balance: Money
    minimum_transfer_amount: Money
    call_status: CallStatus
    call_amount: Money
    transferor_entity_id: str | None
    transferee_entity_id: str | None
    steps: tuple[MarginWaterfallStep, ...]


@dataclass(frozen=True)
class ReconciliationBreak:
    break_id: str
    break_type: BreakType
    description: str
    calculation_party_amount: Money
    counterparty_amount: Money
    difference: Money


@dataclass(frozen=True)
class ReconciliationCase:
    case_id: str
    run_id: str
    calculation_party_call_status: CallStatus
    calculation_party_call_amount: Money
    counterparty_call_status: CallStatus
    counterparty_call_amount: Money
    dispute_case: DisputeAgreementCase
    undisputed_status: UndisputedDeterminationStatus
    undisputed_amount: Money | None
    reconciliation_status: ReconciliationStatus
    breaks: tuple[ReconciliationBreak, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CalculationRun:
    run_id: str
    purpose: CalculationPurpose
    netting_set_id: str
    method_pack_id: str
    calculation_status: CalculationStatus
    data_quality_status: DataQualityStatus
    reconciliation_status: ReconciliationStatus
    validation_status: ValidationStatus
    actionability: Actionability
    agreement_support_status: AgreementSupportStatus
    created_at: dt.datetime
    waterfall: MarginWaterfall | None
    reconciliation_case_id: str | None
    failure_reason: str | None = None
    limitations: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CurvePoint:
    """One (tenor, zero_rate) point on a single discount curve, for the
    RSK-X01 native valuation challenger (plan Section 11.6). `tenor_years`
    is a `Decimal` year fraction (e.g. `Decimal("1")` for one year);
    `zero_rate` is an annualized simple-interest rate (e.g. `Decimal("0.03")`
    for 3%) — see `riskos.core.challenger_pricing` for the discounting
    convention this feeds."""

    tenor_years: Decimal
    zero_rate: Decimal


@dataclass(frozen=True)
class DiscountCurve:
    """One flat-or-bootstrapped discount curve per currency (plan 11.6:
    "explicit single-curve limitation for IRS: no multi-curve, no OIS
    discounting complexity"). `points` must be non-empty and sorted
    ascending by `tenor_years`; a single point is a flat curve."""

    currency: str
    points: tuple[CurvePoint, ...]


@dataclass(frozen=True)
class FxSpotRate:
    """Units of `quote_currency` per one unit of `base_currency`."""

    base_currency: str
    quote_currency: str
    rate: Decimal


@dataclass(frozen=True)
class MarketSnapshot:
    """A pinned market snapshot for the native valuation challenger
    (RSK-X01, plan 11.6: "uses a pinned market snapshot and calendars").
    `calendars` are simulation-only labels; this scope does not implement
    real holiday-calendar business-day adjustment (consistent with the
    single-curve, no-OIS-discounting-complexity limitation). Every
    challenger pricing run is pinned to exactly one `MarketSnapshot` by
    `snapshot_id` — never a live/mutable market view — so a challenger
    result is always reproducible from its inputs alone."""

    snapshot_id: str
    as_of: dt.date
    calendars: tuple[str, ...]
    discount_curves: tuple[DiscountCurve, ...]
    fx_spot_rates: tuple[FxSpotRate, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class InterestRateSwapTerms:
    """A single-currency fixed-for-floating IRS — the only swap shape the
    RSK-X01 challenger prices (plan 11.6: "no options, exotic payoffs,
    credit derivatives, or cross-currency swaps"). `notional`/`fixed_rate`
    are `Decimal`, not `Money`: these are pricing inputs, not a reportable
    amount, until `riskos.core.challenger_workflow` rounds a computed PV
    into `Money` at its one explicit rounding step."""

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


@dataclass(frozen=True)
class FxForwardTerms:
    """A single deliverable FX forward — the RSK-X01 challenger's other
    supported family (plan 11.6). `calculation_party_buys_base=True` means
    the calculation party is long `base_currency` forward (receives
    `notional_base` of `base_currency`, pays `notional_base * contract_rate`
    of `quote_currency`, at `value_date`); `False` is the mirrored short
    position."""

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


@dataclass(frozen=True)
class ChallengerComparison:
    """One trade's challenger-vs-incumbent comparison. `challenger_pv` is
    `None` only when the trade could not be priced at all (e.g. no discount
    curve for its currency in the pinned snapshot) — an explicit, visible
    failure, never a silently-dropped row. `incumbent_pv`/`difference`/
    `tolerance` are `None` when no incumbent `TradeMTM` was found for this
    `trade_id` (`comparison_status=NOT_AVAILABLE`)."""

    trade_id: str
    product_family: str
    challenger_pv: Money | None
    incumbent_pv: Money | None
    difference: Money | None
    tolerance: Money | None
    comparison_status: ReconciliationStatus
    note: str | None = None


@dataclass(frozen=True)
class ChallengerRun:
    """The result of one `CalculationPurpose.CHALLENGER` run (RSK-X01, plan
    11.6). Deliberately not a `CalculationRun`/`MarginWaterfall`: this is a
    structurally separate aggregate, stored in its own repository slot
    (`RiskOSRepository.save_challenger_run`/`get_challenger_run`) under its
    own `run_id` namespace, so a CHALLENGER run can never be mistaken for,
    merged into, or overwrite the authoritative MARGIN_CALL
    `CalculationRun` it is only ever compared against. `actionability` is
    always `INDICATIVE_ONLY` (see `riskos.core.challenger_workflow`): this
    result is a comparison signal, never an actionable margin call, per
    plan 11.6's "result cannot replace external MTM automatically"."""

    run_id: str
    netting_set_id: str
    market_snapshot_id: str
    calculation_status: CalculationStatus
    data_quality_status: DataQualityStatus
    reconciliation_status: ReconciliationStatus
    validation_status: ValidationStatus
    actionability: Actionability
    comparisons: tuple[ChallengerComparison, ...]
    created_at: dt.datetime
    limitations: tuple[str, ...] = field(default_factory=tuple)
    failure_reason: str | None = None


@dataclass(frozen=True)
class ResultPackage:
    result_id: str
    run_id: str
    netting_set_id: str
    calculation_status: CalculationStatus
    data_quality_status: DataQualityStatus
    reconciliation_status: ReconciliationStatus
    validation_status: ValidationStatus
    actionability: Actionability
    method_pack_id: str
    method_pack_version: str
    waterfall: MarginWaterfall | None
    reconciliation: ReconciliationCase | None
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    generated_at: dt.datetime

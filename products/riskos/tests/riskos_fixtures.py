"""The one hand-authored 5-trade RiskOS scenario for this slice (plan
Section 25.5: "manually imported equivalent terms + five external MTMs +
collateral state"). Every identifier is `SIM-*` namespaced; the calculation
entity is the shared synthetic institution `CASSANDRE_SIM_BANK_NA`
(`simulation_factory.institution`).

Hand-derived arithmetic (also re-derived in `test_workflow.py`'s docstring):
  MTMs:      125345.67 - 40200.10 + 10000.00 - 2500.00 + 7890.43 = 100536.00
  Exposure = 100536.00 USD
  CSB      = held 60000.00 - posted 5000.00 = 55000.00 USD
  raw      = 100536.00 - 0 (threshold) - 55000.00 = 45536.00 USD
  45536.00 >= MTA 10000.00  => DELIVER, round-half-up(45536.00) = 45536.00 USD
  (counterparty delivers 45536.00 USD to the calculation party)
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from money_types import Money
from simulation_factory.institution import CASSANDRE_SIM_BANK_NA

from riskos.core.constants import METHOD_PACK_001_ROUNDING_CONTEXT, TRADE_MTM_ROUNDING_CONTEXT
from riskos.core.method_pack import build_method_pack_001
from riskos.core.reconciliation import PartyCallView
from riskos.domain.entities import (
    AgreementTermsPackage,
    CollateralPosition,
    CollateralStatePackage,
    LegalEntityMasterVersion,
    MethodPack,
    NettingSetVersion,
    PortfolioSnapshot,
    SourceControlManifest,
    TradeMTM,
)
from riskos.domain.enums import (
    CallStatus,
    ReconciliationStatus,
    TradeLifecycleState,
    TradePopulationDisposition,
)

FIXED_NOW = dt.datetime(2026, 1, 5, 12, 0, 0, tzinfo=dt.timezone.utc)

CALCULATION_ENTITY_ID = CASSANDRE_SIM_BANK_NA.entity_id
COUNTERPARTY_ENTITY_ID = "SIM-ENTITY-COUNTERPARTY-001"
NETTING_SET_ID = "SIM-NETSET-0001"
AGREEMENT_ID = "SIM-AGREEMENT-0001"
PORTFOLIO_SNAPSHOT_ID = "SIM-PORTFOLIO-0001"
COLLATERAL_PACKAGE_ID = "SIM-COLLATERAL-0001"
MANIFEST_ID = "SIM-MANIFEST-0001"


def mtm_money(amount: str) -> Money:
    return Money(amount=amount, currency="USD", rounding_context=TRADE_MTM_ROUNDING_CONTEXT)


_mtm = mtm_money


def pack_money(amount: str) -> Money:
    return Money(amount=amount, currency="USD", rounding_context=METHOD_PACK_001_ROUNDING_CONTEXT)


# Backward/internal-style alias used within this module.
_pack_money = pack_money


def build_calculation_entity() -> LegalEntityMasterVersion:
    return LegalEntityMasterVersion(
        entity_id=CASSANDRE_SIM_BANK_NA.entity_id,
        display_name=CASSANDRE_SIM_BANK_NA.display_name,
        lei_shaped_id=CASSANDRE_SIM_BANK_NA.lei_shaped_id,
        home_currency=CASSANDRE_SIM_BANK_NA.home_currency,
        is_active_calculation_entity=True,
        version="v1",
        approved=True,
    )


def build_counterparty_entity() -> LegalEntityMasterVersion:
    return LegalEntityMasterVersion(
        entity_id=COUNTERPARTY_ENTITY_ID,
        display_name="SIM Counterparty Bank (fictional, S1 synthetic entity)",
        lei_shaped_id="SIM-LEI-0000000000000CPTY1",
        home_currency="USD",
        is_active_calculation_entity=False,
        version="v1",
        approved=True,
    )


def build_netting_set() -> NettingSetVersion:
    return NettingSetVersion(
        netting_set_id=NETTING_SET_ID,
        calculation_entity_id=CALCULATION_ENTITY_ID,
        counterparty_entity_id=COUNTERPARTY_ENTITY_ID,
        governing_agreement_id=AGREEMENT_ID,
        version="v1",
        effective_from=dt.date(2026, 1, 1),
        effective_to=None,
        approved=True,
    )


def build_agreement_terms(*, method_pack: MethodPack | None = None, is_supported: bool = True) -> AgreementTermsPackage:
    pack = method_pack or build_method_pack_001()
    return AgreementTermsPackage(
        agreement_id=AGREEMENT_ID,
        netting_set_id=NETTING_SET_ID,
        method_pack_id=pack.method_id,
        call_currency=pack.call_currency,
        approved=True,
        approval_reference="SIM-APPROVAL-0001",
        is_supported=is_supported,
    )


def build_five_trades() -> tuple[TradeMTM, ...]:
    raw = [
        ("SIM-TRADE-0001", "IRS", "125345.67"),
        ("SIM-TRADE-0002", "FX_FORWARD", "-40200.10"),
        ("SIM-TRADE-0003", "IRS", "10000.00"),
        ("SIM-TRADE-0004", "FX_FORWARD", "-2500.00"),
        ("SIM-TRADE-0005", "IRS", "7890.43"),
    ]
    return tuple(
        TradeMTM(
            trade_id=trade_id,
            calculation_entity_id=CALCULATION_ENTITY_ID,
            counterparty_entity_id=COUNTERPARTY_ENTITY_ID,
            netting_set_id=NETTING_SET_ID,
            product_family=product_family,
            lifecycle_state=TradeLifecycleState.ACTIVE,
            disposition=TradePopulationDisposition.ACCEPTED,
            mtm=_mtm(amount),
        )
        for trade_id, product_family, amount in raw
    )


def build_portfolio_snapshot(trades: tuple[TradeMTM, ...] | None = None) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id=PORTFOLIO_SNAPSHOT_ID,
        as_of=FIXED_NOW,
        trades=trades if trades is not None else build_five_trades(),
    )


def build_manifest(
    *,
    expected_accepted_count: int = 5,
    expected_accepted_mtm_total: str = "100536.00",
    expected_matured_count: int = 0,
    expected_rejected_count: int = 0,
    expected_duplicate_count: int = 0,
    expected_cancelled_count: int = 0,
    expected_amended_count: int = 0,
    expected_unsupported_count: int = 0,
) -> SourceControlManifest:
    return SourceControlManifest(
        manifest_id=MANIFEST_ID,
        expected_accepted_count=expected_accepted_count,
        expected_rejected_count=expected_rejected_count,
        expected_duplicate_count=expected_duplicate_count,
        expected_matured_count=expected_matured_count,
        expected_cancelled_count=expected_cancelled_count,
        expected_amended_count=expected_amended_count,
        expected_unsupported_count=expected_unsupported_count,
        expected_accepted_mtm_total=_mtm(expected_accepted_mtm_total),
    )


def build_collateral_state() -> CollateralStatePackage:
    held = CollateralPosition(
        asset_or_cash_id="SIM-CASH-USD-HELD",
        currency="USD",
        quantity=None,
        adjusted_value=_pack_money("60000.00"),
        title_transfer_status="TITLE_TRANSFERRED",
        security_interest_status="NOT_APPLICABLE",
        segregation_status="NOT_APPLICABLE",
        reuse_status="UNRESTRICTED",
    )
    posted = CollateralPosition(
        asset_or_cash_id="SIM-CASH-USD-POSTED",
        currency="USD",
        quantity=None,
        adjusted_value=_pack_money("5000.00"),
        title_transfer_status="TITLE_TRANSFERRED",
        security_interest_status="NOT_APPLICABLE",
        segregation_status="NOT_APPLICABLE",
        reuse_status="UNRESTRICTED",
    )
    return CollateralStatePackage(
        package_id=COLLATERAL_PACKAGE_ID,
        calculation_entity_id=CALCULATION_ENTITY_ID,
        netting_set_id=NETTING_SET_ID,
        as_of=FIXED_NOW,
        source_watermark="SIM-WATERMARK-0001",
        collateral_held=(held,),
        collateral_posted=(posted,),
        pending_inbound_transfers=(),
        pending_outbound_transfers=(),
        failed_transfers=(),
        cancelled_transfers=(),
        active_substitutions=(),
        return_legs=(),
        disputed_amount=_pack_money("0"),
        undisputed_amount=None,
        valuation_source="SIM-VALUATION-SOURCE-001",
        fx_source="SIM-FX-SOURCE-001",
        custodian_reconciliation_status=ReconciliationStatus.MATCHED,
        sign_convention="positive_amount_held_by_calculation_party",
    )


def build_matching_counterparty_call() -> PartyCallView:
    """A synthetic counterparty result that agrees on direction and amount
    with the calculation party (plan 11.5's "both agree" case)."""

    return PartyCallView(
        party_label="COUNTERPARTY",
        call_status=CallStatus.DELIVER,
        call_amount=_pack_money("45536.00"),
        exposure=_pack_money("100536.00"),
        credit_support_balance=_pack_money("55000.00"),
        trade_population_count=5,
        as_of=FIXED_NOW,
        responded=True,
    )


@dataclass(frozen=True)
class GoldenScenario:
    calculation_entity: LegalEntityMasterVersion
    counterparty_entity: LegalEntityMasterVersion
    netting_set: NettingSetVersion
    agreement_terms: AgreementTermsPackage
    portfolio: PortfolioSnapshot
    manifest: SourceControlManifest
    collateral: CollateralStatePackage
    method_pack: MethodPack
    counterparty_call: PartyCallView
    now: dt.datetime


def build_golden_scenario() -> GoldenScenario:
    return GoldenScenario(
        calculation_entity=build_calculation_entity(),
        counterparty_entity=build_counterparty_entity(),
        netting_set=build_netting_set(),
        agreement_terms=build_agreement_terms(),
        portfolio=build_portfolio_snapshot(),
        manifest=build_manifest(),
        collateral=build_collateral_state(),
        method_pack=build_method_pack_001(),
        counterparty_call=build_matching_counterparty_call(),
        now=FIXED_NOW,
    )

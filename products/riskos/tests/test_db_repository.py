"""DB-backed repository/service round-trip tests, exercising the exact same
`RiskOSService` application layer that `apps/riskos_api` calls, but wired to
the real `SqlAlchemyRiskOSRepository` instead of the in-memory adapter used
by `test_service.py`. Skipped cleanly (via the `sql_session` fixture in
conftest.py) when `CASSANDRE_TEST_DATABASE_URL` is unset or unreachable.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

from audit_protocol import verify_chain
from audit_protocol.keys import generate_keypair
from cassandre_contracts import TrustBundle, verify_envelope

import riskos_fixtures as fx
from riskos.adapters.sql_repository import SqlAlchemyRiskOSRepository
from riskos.application.service import RiskOSNotFoundError, RiskOSService
from riskos.domain.entities import CurvePoint, DiscountCurve, InterestRateSwapTerms, MarketSnapshot
from riskos.domain.enums import (
    Actionability,
    CalculationPurpose,
    CalculationStatus,
    ReconciliationStatus,
)

FIXED_NOW = dt.datetime(2026, 1, 5, 12, 0, 0, tzinfo=dt.timezone.utc)


def _build_service(sql_session) -> RiskOSService:
    repo = SqlAlchemyRiskOSRepository(sql_session)
    keypair = generate_keypair("riskos-db-test-key")
    return RiskOSService(repo, keypair=keypair)


def test_full_service_workflow_against_real_postgres_produces_complete_matched_run_and_verifiable_audit_chain(
    sql_session,
):
    service = _build_service(sql_session)
    scenario = fx.build_golden_scenario()
    now = scenario.now

    service.register_legal_entity(scenario.calculation_entity, now=now)
    service.register_legal_entity(scenario.counterparty_entity, now=now)
    service.register_netting_set(scenario.netting_set, now=now)

    ok, reasons = service.validate_agreement_terms(scenario.agreement_terms)
    assert ok, reasons
    approved_terms = service.approve_agreement_terms(scenario.agreement_terms, now=now)
    assert approved_terms.approved

    service.ingest_portfolio_snapshot(scenario.portfolio, now=now)
    service.ingest_collateral_state(scenario.collateral, now=now)

    run = service.run_calculation(
        run_id="SIM-RUN-DB-SERVICE-0001",
        purpose=CalculationPurpose.MARGIN_CALL,
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        agreement_id=scenario.agreement_terms.agreement_id,
        portfolio_snapshot_id=scenario.portfolio.snapshot_id,
        collateral_package_id=scenario.collateral.package_id,
        manifest=scenario.manifest,
        counterparty_call=scenario.counterparty_call,
        now=now,
    )

    assert run.calculation_status is CalculationStatus.COMPLETE
    assert run.reconciliation_status is ReconciliationStatus.MATCHED
    assert run.actionability is Actionability.SIMULATION_ELIGIBLE
    assert str(run.waterfall.call_amount.amount) == "45536"

    # Round trip: fetch through a repository method that only reads back
    # what Postgres actually stored (not the in-process `run` object).
    fetched_run = service.get_calculation_run(run.run_id)
    assert fetched_run.run_id == run.run_id
    assert fetched_run.calculation_status is CalculationStatus.COMPLETE
    assert str(fetched_run.waterfall.call_amount.amount) == "45536"

    events = service._repo.list_audit_events()  # test-only introspection, mirrors test_service.py
    assert len(events) >= 1
    chain_result = verify_chain(list(events))
    assert chain_result.ok, chain_result.reason

    envelope = service.export_result_package(run_id=run.run_id, now=now)
    trust_bundle = TrustBundle(keys_by_id={service._keypair.key_id: service._keypair.public_key_b64})
    verification = verify_envelope(envelope, trust_bundle=trust_bundle, expected_audience="cassandre")
    assert verification.ok, verification.failures


def test_get_calculation_run_raises_for_unknown_run_id_against_real_postgres(sql_session):
    service = _build_service(sql_session)
    try:
        service.get_calculation_run("SIM-RUN-DOES-NOT-EXIST")
        assert False, "expected RiskOSNotFoundError"
    except RiskOSNotFoundError:
        pass


def test_submit_reconciliation_recomputes_against_a_new_counterparty_call_against_real_postgres(sql_session):
    service = _build_service(sql_session)
    scenario = fx.build_golden_scenario()
    now = scenario.now
    service.register_legal_entity(scenario.calculation_entity, now=now)
    service.register_legal_entity(scenario.counterparty_entity, now=now)
    service.register_netting_set(scenario.netting_set, now=now)
    service.approve_agreement_terms(scenario.agreement_terms, now=now)
    service.ingest_portfolio_snapshot(scenario.portfolio, now=now)
    service.ingest_collateral_state(scenario.collateral, now=now)
    run = service.run_calculation(
        run_id="SIM-RUN-DB-SERVICE-0002",
        purpose=CalculationPurpose.MARGIN_CALL,
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        agreement_id=scenario.agreement_terms.agreement_id,
        portfolio_snapshot_id=scenario.portfolio.snapshot_id,
        collateral_package_id=scenario.collateral.package_id,
        manifest=scenario.manifest,
        counterparty_call=scenario.counterparty_call,
        now=now,
    )
    assert run.reconciliation_status is ReconciliationStatus.MATCHED

    disagreeing_call = dataclasses.replace(fx.build_matching_counterparty_call(), call_amount=fx.pack_money("1.00"))
    case = service.submit_reconciliation(run_id=run.run_id, counterparty_call=disagreeing_call, now=now)
    assert case.reconciliation_status is ReconciliationStatus.BREAK

    fetched_case = service.get_reconciliation_case(case.case_id)
    assert fetched_case.reconciliation_status is ReconciliationStatus.BREAK


def test_challenger_run_round_trips_through_real_postgres_and_never_touches_the_margin_call_run(sql_session):
    """RSK-X01 additive path: persists to its own table, independent of the
    MARGIN_CALL run above -- mirrors `test_challenger.py`'s in-memory
    independence proof, but now proving it through real Postgres storage."""

    service = _build_service(sql_session)
    scenario = fx.build_golden_scenario()
    now = scenario.now

    service.register_legal_entity(scenario.calculation_entity, now=now)
    service.register_legal_entity(scenario.counterparty_entity, now=now)
    service.register_netting_set(scenario.netting_set, now=now)
    service.approve_agreement_terms(scenario.agreement_terms, now=now)
    service.ingest_portfolio_snapshot(scenario.portfolio, now=now)
    service.ingest_collateral_state(scenario.collateral, now=now)
    margin_call_run = service.run_calculation(
        run_id="SIM-RUN-DB-SERVICE-0003",
        purpose=CalculationPurpose.MARGIN_CALL,
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        agreement_id=scenario.agreement_terms.agreement_id,
        portfolio_snapshot_id=scenario.portfolio.snapshot_id,
        collateral_package_id=scenario.collateral.package_id,
        manifest=scenario.manifest,
        counterparty_call=scenario.counterparty_call,
        now=now,
    )
    before = service.get_calculation_run(margin_call_run.run_id)

    market_snapshot = MarketSnapshot(
        snapshot_id="SIM-MKT-DB-0001",
        as_of=dt.date(2026, 1, 1),
        calendars=("SIM.NONE",),
        discount_curves=(DiscountCurve(currency="USD", points=(CurvePoint(tenor_years=Decimal("1"), zero_rate=Decimal("0.03")),)),),
        fx_spot_rates=(),
    )
    service.ingest_market_snapshot(market_snapshot, now=now)
    fetched_snapshot = service.get_market_snapshot(market_snapshot.snapshot_id)
    assert fetched_snapshot.discount_curves[0].currency == "USD"

    irs_terms = InterestRateSwapTerms(
        trade_id="SIM-CHALLENGER-IRS-DB-0001",
        calculation_entity_id=scenario.calculation_entity.entity_id,
        counterparty_entity_id=scenario.counterparty_entity.entity_id,
        netting_set_id="SIM-NETSET-CHALLENGER-DB-0001",
        currency="USD",
        notional=Decimal("1000000"),
        fixed_rate=Decimal("0.04"),
        calculation_party_pays_fixed=False,
        effective_date=dt.date(2026, 1, 1),
        maturity_date=dt.date(2028, 1, 1),
        payment_frequency_months=12,
    )
    challenger_run = service.run_challenger_valuation(
        run_id="SIM-CHALLENGER-RUN-DB-0001",
        netting_set_id="SIM-NETSET-CHALLENGER-DB-0001",
        market_snapshot_id=market_snapshot.snapshot_id,
        as_of=dt.date(2026, 1, 1),
        now=now,
        irs_trades=(irs_terms,),
    )
    assert challenger_run.calculation_status is CalculationStatus.COMPLETE

    fetched_challenger_run = service.get_challenger_run(challenger_run.run_id)
    assert fetched_challenger_run.run_id == challenger_run.run_id
    assert len(fetched_challenger_run.comparisons) == 1
    assert fetched_challenger_run.comparisons[0].challenger_pv is not None
    assert fetched_challenger_run.comparisons[0].challenger_pv.amount.quantize(Decimal("0.01")) == Decimal("19967.03")

    after = service.get_calculation_run(margin_call_run.run_id)
    assert after == before, "CHALLENGER run must never alter an existing MARGIN_CALL run"

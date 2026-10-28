"""SIM-004: the synthetic trade corpus, per plan Section 9.3.

300 trades across IRS and FX forwards, spanning multiple maturities and
currencies, positive/negative/zero MTMs, every lifecycle state, exact
netting-set membership, and deliberate duplicate/wrong-entity/wrong-sign/
unsupported-product traps. A "missing" trade trap is represented as an
expected trade_id that the source-control manifest names but that never
appears in the delivered population (see `expected_missing_trade_ids`).

This module only *generates* the population. Expected totals are computed
independently in `simulation_factory.independent_reference.trade_population_reference`
(SIM-008) -- plan Section 9.3's explicit instruction never to derive
expected RiskOS totals "using the same implementation under test" applies
here even though this package is not `riskos`: the generator and the
reference/checker are kept in separate modules with no shared aggregation
helper between them.

Determinism: all variation (notional size, maturity spacing, MTM magnitude)
comes from `random.Random(seed)` with a fixed default seed, never bare
`random`. Calling `build_trade_corpus()` twice with the same seed produces
byte-identical output.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field
from decimal import Decimal

from money_types import Money, Quantity

from simulation_factory.corpus.agreements import build_agreement_bundles
from simulation_factory.identifiers import require_sim_identifier
from simulation_factory.institution import CASSANDRE_SIM_BANK_IFSC, CASSANDRE_SIM_BANK_NA
from simulation_factory.provenance import SimulationProvenance, make_provenance

SCENARIO_ID = "SIM-SCENARIO-CORPUS-TRADES"
DEFAULT_SEED = 20260913

TRADE_MTM_ROUNDING_CONTEXT = "SIM-TRADE-MTM-SOURCE"

PRODUCT_FAMILIES_SUPPORTED = ("IRS", "FX_FORWARD")
UNSUPPORTED_PRODUCT_FAMILY = "EQUITY_OPTION"

NOTIONAL_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "INR")

TOTAL_TRADE_COUNT = 300

# Disposition bucket sizes; must sum to TOTAL_TRADE_COUNT.
N_ACCEPTED = 250
N_MATURED = 15
N_CANCELLED = 10
N_AMENDED = 10
N_DUPLICATE = 5
N_REJECTED_WRONG_ENTITY = 3
N_REJECTED_WRONG_SIGN = 3
N_REJECTED_UNRECOGNIZED_COUNTERPARTY = 2
N_UNSUPPORTED = 2

assert (
    N_ACCEPTED + N_MATURED + N_CANCELLED + N_AMENDED + N_DUPLICATE
    + N_REJECTED_WRONG_ENTITY + N_REJECTED_WRONG_SIGN + N_REJECTED_UNRECOGNIZED_COUNTERPARTY
    + N_UNSUPPORTED
) == TOTAL_TRADE_COUNT

N_COUNTERPARTIES = 8


@dataclass(frozen=True)
class SyntheticCounterparty:
    entity_id: str
    display_name: str
    lei_shaped_id: str

    def __post_init__(self) -> None:
        require_sim_identifier(self.entity_id, field_name="entity_id")


@dataclass(frozen=True)
class SyntheticNettingSet:
    netting_set_id: str
    calculation_entity_id: str
    counterparty_entity_id: str
    governing_agreement_id: str

    def __post_init__(self) -> None:
        require_sim_identifier(self.netting_set_id, field_name="netting_set_id")


@dataclass(frozen=True)
class TradeRecord:
    """One trade-level MTM record. `disposition` is the population-control
    bucket the source-control manifest reconciles against (mirrors the shape
    of `riskos.domain.entities.TradeMTM`/`TradePopulationDisposition`
    conceptually, without importing `riskos`). `lifecycle_state` is the
    trade's own state and is independent of `disposition` -- e.g. a
    wrong-entity trap can be `lifecycle_state=ACTIVE` while its
    `disposition=REJECTED`."""

    trade_id: str
    version: int
    calculation_entity_id: str
    counterparty_entity_id: str
    netting_set_id: str
    governing_agreement_id: str
    product_family: str
    notional: Quantity
    effective_date: dt.date
    maturity_date: dt.date
    lifecycle_state: str  # ACTIVE | MATURED | CANCELLED | AMENDED
    disposition: str  # ACCEPTED | REJECTED | DUPLICATE | MATURED | CANCELLED | AMENDED | UNSUPPORTED
    rejection_reason: str | None
    trap_type: str | None
    mtm: Money
    source_watermark: str
    provenance: SimulationProvenance

    def __post_init__(self) -> None:
        require_sim_identifier(self.trade_id, field_name="trade_id")
        require_sim_identifier(self.netting_set_id, field_name="netting_set_id")


@dataclass(frozen=True)
class TradeCorpus:
    counterparties: tuple[SyntheticCounterparty, ...]
    netting_sets: tuple[SyntheticNettingSet, ...]
    trades: tuple[TradeRecord, ...]
    expected_missing_trade_ids: tuple[str, ...]
    seed: int


def _build_counterparties() -> tuple[SyntheticCounterparty, ...]:
    greek = ("ALPHA", "BETA", "GAMMA", "DELTA", "EPSILON", "ZETA", "ETA", "THETA")
    return tuple(
        SyntheticCounterparty(
            entity_id=f"SIM-CPTY-{idx:03d}",
            display_name=f"SIM Counterparty {label.title()} (fictional, S1 synthetic entity)",
            lei_shaped_id=f"SIM-LEI-0000000000000CP{idx:02d}",
        )
        for idx, label in enumerate(greek[:N_COUNTERPARTIES], start=1)
    )


def _build_netting_sets(
    counterparties: tuple[SyntheticCounterparty, ...],
    calculable_agreement_ids: tuple[str, ...],
) -> tuple[SyntheticNettingSet, ...]:
    return tuple(
        SyntheticNettingSet(
            netting_set_id=f"SIM-NETSET-TRADE-{idx:04d}",
            calculation_entity_id=CASSANDRE_SIM_BANK_NA.entity_id,
            counterparty_entity_id=cpty.entity_id,
            governing_agreement_id=calculable_agreement_ids[(idx - 1) % len(calculable_agreement_ids)],
        )
        for idx, cpty in enumerate(counterparties, start=1)
    )


def _random_date(rng: random.Random, start: dt.date, end: dt.date) -> dt.date:
    span = (end - start).days
    return start + dt.timedelta(days=rng.randint(0, span))


def _random_mtm_amount(rng: random.Random, *, allow_zero: bool = True) -> str:
    if allow_zero and rng.random() < 0.05:
        return "0.00"
    cents = rng.randint(-750_000_00, 750_000_00)
    if cents == 0:
        cents = 1
    return str(Decimal(cents).scaleb(-2).quantize(Decimal("0.01")))


def _mtm(amount: str) -> Money:
    return Money(amount=amount, currency="USD", rounding_context=TRADE_MTM_ROUNDING_CONTEXT)


def _notional(rng: random.Random, currency: str) -> Quantity:
    amount = rng.randint(1_000_000, 250_000_000)
    return Quantity(amount=str(amount), unit=currency)


def build_trade_corpus(seed: int = DEFAULT_SEED) -> TradeCorpus:
    rng = random.Random(seed)

    calculable_ids = tuple(
        b.bundle_id for b in build_agreement_bundles() if b.riskos_calculable
    )

    counterparties = _build_counterparties()
    netting_sets = _build_netting_sets(counterparties, calculable_ids)

    trade_start = dt.date(2021, 1, 1)
    trade_end = dt.date(2026, 1, 1)
    maturity_end = dt.date(2036, 1, 1)

    trades: list[TradeRecord] = []
    seq = 0

    def _next_id() -> str:
        nonlocal seq
        seq += 1
        return f"SIM-TRADE-{seq:05d}"

    def _pick_netting_set() -> SyntheticNettingSet:
        return netting_sets[rng.randrange(len(netting_sets))]

    def _base_record(
        *,
        lifecycle_state: str,
        disposition: str,
        rejection_reason: str | None = None,
        trap_type: str | None = None,
        product_family: str | None = None,
        calculation_entity_id: str | None = None,
        counterparty_entity_id: str | None = None,
        netting_set_id: str | None = None,
        governing_agreement_id: str | None = None,
        mtm_amount: str | None = None,
    ) -> TradeRecord:
        ns = _pick_netting_set()
        pf = product_family or (PRODUCT_FAMILIES_SUPPORTED[rng.randrange(2)])
        ccy = NOTIONAL_CURRENCIES[rng.randrange(len(NOTIONAL_CURRENCIES))]
        effective = _random_date(rng, trade_start, trade_end)
        maturity = _random_date(rng, effective + dt.timedelta(days=90), maturity_end)
        return TradeRecord(
            trade_id=_next_id(),
            version=1,
            calculation_entity_id=calculation_entity_id or ns.calculation_entity_id,
            counterparty_entity_id=counterparty_entity_id or ns.counterparty_entity_id,
            netting_set_id=netting_set_id or ns.netting_set_id,
            governing_agreement_id=governing_agreement_id or ns.governing_agreement_id,
            product_family=pf,
            notional=_notional(rng, ccy),
            effective_date=effective,
            maturity_date=maturity,
            lifecycle_state=lifecycle_state,
            disposition=disposition,
            rejection_reason=rejection_reason,
            trap_type=trap_type,
            mtm=_mtm(mtm_amount if mtm_amount is not None else _random_mtm_amount(rng)),
            source_watermark=f"SIM-INCUMBENT-FEED-{dt.date(2026, 1, 5).isoformat()}",
            provenance=make_provenance(SCENARIO_ID),
        )

    # -- ACCEPTED (clean, active) --
    for _ in range(N_ACCEPTED):
        trades.append(_base_record(lifecycle_state="ACTIVE", disposition="ACCEPTED"))

    # -- MATURED --
    for _ in range(N_MATURED):
        trades.append(_base_record(lifecycle_state="MATURED", disposition="MATURED"))

    # -- CANCELLED --
    for _ in range(N_CANCELLED):
        trades.append(_base_record(lifecycle_state="CANCELLED", disposition="CANCELLED"))

    # -- AMENDED --
    for _ in range(N_AMENDED):
        trades.append(_base_record(lifecycle_state="AMENDED", disposition="AMENDED"))

    # -- DUPLICATE: re-emit an already-accepted trade_id at version 2 --
    duplicate_sources = rng.sample([t for t in trades if t.disposition == "ACCEPTED"], N_DUPLICATE)
    for source in duplicate_sources:
        trades.append(
            TradeRecord(
                trade_id=source.trade_id,
                version=2,
                calculation_entity_id=source.calculation_entity_id,
                counterparty_entity_id=source.counterparty_entity_id,
                netting_set_id=source.netting_set_id,
                governing_agreement_id=source.governing_agreement_id,
                product_family=source.product_family,
                notional=source.notional,
                effective_date=source.effective_date,
                maturity_date=source.maturity_date,
                lifecycle_state=source.lifecycle_state,
                disposition="DUPLICATE",
                rejection_reason=None,
                trap_type="DUPLICATE",
                mtm=source.mtm,
                source_watermark=source.source_watermark,
                provenance=make_provenance(SCENARIO_ID),
            )
        )

    # -- REJECTED: wrong entity (references the affiliate, never a valid
    # calculation entity for these netting sets -- a segregation trap) --
    for _ in range(N_REJECTED_WRONG_ENTITY):
        trades.append(
            _base_record(
                lifecycle_state="ACTIVE",
                disposition="REJECTED",
                rejection_reason="WRONG_ENTITY",
                trap_type="WRONG_ENTITY",
                calculation_entity_id=CASSANDRE_SIM_BANK_IFSC.entity_id,
            )
        )

    # -- REJECTED: wrong sign (an MTM whose sign contradicts this trade's
    # own record of which party is owed, flagged for data-quality review) --
    for _ in range(N_REJECTED_WRONG_SIGN):
        trades.append(
            _base_record(
                lifecycle_state="ACTIVE",
                disposition="REJECTED",
                rejection_reason="WRONG_SIGN",
                trap_type="WRONG_SIGN",
            )
        )

    # -- REJECTED: unrecognized counterparty (not in the synthetic roster) --
    for i in range(N_REJECTED_UNRECOGNIZED_COUNTERPARTY):
        trades.append(
            _base_record(
                lifecycle_state="ACTIVE",
                disposition="REJECTED",
                rejection_reason="UNRECOGNIZED_COUNTERPARTY",
                trap_type="UNRECOGNIZED_COUNTERPARTY",
                counterparty_entity_id=f"SIM-CPTY-UNKNOWN-{i + 1:03d}",
            )
        )

    # -- UNSUPPORTED product family --
    for _ in range(N_UNSUPPORTED):
        trades.append(
            _base_record(
                lifecycle_state="ACTIVE",
                disposition="UNSUPPORTED",
                trap_type="UNSUPPORTED_PRODUCT",
                product_family=UNSUPPORTED_PRODUCT_FAMILY,
            )
        )

    if len(trades) != TOTAL_TRADE_COUNT:
        raise ValueError(f"expected {TOTAL_TRADE_COUNT} trades, built {len(trades)}")

    # "Missing" trap: the source-control feed's own control totals name two
    # trade_ids that the incumbent system reported as expected but that were
    # never actually delivered in this population -- deliberately absent.
    expected_missing_trade_ids = ("SIM-TRADE-99001", "SIM-TRADE-99002")

    return TradeCorpus(
        counterparties=counterparties,
        netting_sets=netting_sets,
        trades=tuple(trades),
        expected_missing_trade_ids=expected_missing_trade_ids,
        seed=seed,
    )

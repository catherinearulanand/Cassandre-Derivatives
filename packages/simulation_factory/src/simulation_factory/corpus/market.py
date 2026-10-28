"""SIM-005: the synthetic market-scenario corpus, per plan Section 9.4.

Thirty immutable daily snapshots: 10 quiet, 10 rate-shock, 5 FX-stress, 5
combined-liquidity-stress. Each carries source provenance marked
`SIMULATED`, an explicit timestamp/timezone, a freshness policy, FX quotes,
a discount-factor curve, and (for a deliberately chosen minority of
snapshots) intentional stale/missing/crossed-data negative cases.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass
from decimal import Decimal

from audit_protocol.canonical import content_hash
from money_types import FXConversion, QuoteDirection

from simulation_factory.identifiers import require_sim_identifier
from simulation_factory.provenance import SimulationProvenance, make_provenance
from simulation_factory.serialization import to_jsonable

SCENARIO_ID = "SIM-SCENARIO-CORPUS-MARKET"
DEFAULT_SEED = 20260501

N_QUIET = 10
N_RATE_SHOCK = 10
N_FX_STRESS = 5
N_COMBINED_LIQUIDITY_STRESS = 5
TOTAL_SNAPSHOTS = N_QUIET + N_RATE_SHOCK + N_FX_STRESS + N_COMBINED_LIQUIDITY_STRESS

REGIME_QUIET = "QUIET"
REGIME_RATE_SHOCK = "RATE_SHOCK"
REGIME_FX_STRESS = "FX_STRESS"
REGIME_COMBINED_LIQUIDITY_STRESS = "COMBINED_LIQUIDITY_STRESS"

FX_PAIRS = (("EUR", "USD"), ("GBP", "USD"), ("JPY", "USD"), ("INR", "USD"))
BASE_FX_RATE = {"EUR": Decimal("1.0800"), "GBP": Decimal("1.2600"), "JPY": Decimal("0.0068"), "INR": Decimal("0.0120")}
TENORS = ("1M", "3M", "6M", "1Y", "2Y", "5Y", "10Y")
BASE_DISCOUNT_RATE = Decimal("0.0500")  # 5.00% flat baseline annualized rate for USD


@dataclass(frozen=True)
class FreshnessPolicy:
    stale_after: dt.datetime
    max_age_hours: int

    def __post_init__(self) -> None:
        if self.stale_after.tzinfo is None:
            raise ValueError("stale_after must be timezone-aware")


@dataclass(frozen=True)
class DiscountFactorPoint:
    currency: str
    tenor: str
    zero_rate: str  # decimal string, annualized
    discount_factor: str  # decimal string


@dataclass(frozen=True)
class NegativeCaseFlags:
    is_stale: bool
    has_missing_quote: bool
    has_crossed_market: bool
    notes: str


@dataclass(frozen=True)
class MarketSnapshot:
    snapshot_id: str
    regime: str
    as_of: dt.datetime
    timezone: str
    source_label: str  # always "SIMULATED", plan Section 9.4
    freshness_policy: FreshnessPolicy
    fx_quotes: tuple[FXConversion, ...]
    discount_factors: tuple[DiscountFactorPoint, ...]
    negative_case: NegativeCaseFlags
    provenance: SimulationProvenance

    def __post_init__(self) -> None:
        require_sim_identifier(self.snapshot_id, field_name="snapshot_id")
        if self.source_label != "SIMULATED":
            raise ValueError("source_label must be exactly 'SIMULATED' per plan Section 9.4")
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")


def _snapshot_hash(snapshot_id: str, pair: tuple[str, str]) -> str:
    return content_hash({"snapshot_id": snapshot_id, "pair": list(pair), "source": "SIMULATED"})


def _fx_rate_for_regime(rng: random.Random, currency: str, regime: str) -> Decimal:
    base = BASE_FX_RATE[currency]
    if regime == REGIME_QUIET:
        wobble = Decimal(rng.randint(-20, 20)) / Decimal(10000)  # +/-0.20%
    elif regime == REGIME_RATE_SHOCK:
        wobble = Decimal(rng.randint(-150, 150)) / Decimal(10000)  # +/-1.50%
    elif regime == REGIME_FX_STRESS:
        wobble = Decimal(rng.randint(-800, 800)) / Decimal(10000)  # +/-8.00%
    else:  # combined liquidity stress
        wobble = Decimal(rng.randint(-500, 500)) / Decimal(10000)  # +/-5.00%
    return (base * (Decimal("1") + wobble)).quantize(Decimal("0.000001"))


def _rate_shift_for_regime(rng: random.Random, regime: str) -> Decimal:
    if regime == REGIME_QUIET:
        return Decimal(rng.randint(-5, 5)) / Decimal(10000)  # +/-5bp
    if regime == REGIME_RATE_SHOCK:
        return Decimal(rng.randint(100, 300)) / Decimal(10000)  # +100..+300bp
    if regime == REGIME_FX_STRESS:
        return Decimal(rng.randint(-20, 20)) / Decimal(10000)
    return Decimal(rng.randint(50, 150)) / Decimal(10000)  # combined liquidity stress: +50..+150bp


def _tenor_years(tenor: str) -> Decimal:
    return {
        "1M": Decimal("0.0833"), "3M": Decimal("0.25"), "6M": Decimal("0.5"),
        "1Y": Decimal("1"), "2Y": Decimal("2"), "5Y": Decimal("5"), "10Y": Decimal("10"),
    }[tenor]


def _build_fx_quotes(
    rng: random.Random, snapshot_id: str, as_of: dt.datetime, regime: str, *, crossed: bool, missing: bool,
) -> tuple[FXConversion, ...]:
    quotes = []
    pairs = FX_PAIRS[:-1] if missing else FX_PAIRS  # deliberately drop the last pair to simulate a missing quote
    for source_ccy, target_ccy in pairs:
        rate = _fx_rate_for_regime(rng, source_ccy, regime)
        if crossed and source_ccy == "EUR":
            # A deliberately crossed/nonsensical quote: rate collapses to
            # zero-adjacent, which downstream conversion must reject rather
            # than silently accept.
            rate = Decimal("0.000001")
        quotes.append(
            FXConversion(
                source_currency=source_ccy,
                target_currency=target_ccy,
                quote_direction=QuoteDirection.TARGET_PER_SOURCE,
                rate=str(rate),
                valuation_time=as_of,
                source_snapshot_hash=_snapshot_hash(snapshot_id, (source_ccy, target_ccy)),
                rounding_stage="POST_CONVERSION",
                rounding_rule="ROUND_HALF_UP",
            )
        )
    return tuple(quotes)


def _build_discount_factors(rng: random.Random, regime: str) -> tuple[DiscountFactorPoint, ...]:
    shift = _rate_shift_for_regime(rng, regime)
    points = []
    for tenor in TENORS:
        zero_rate = BASE_DISCOUNT_RATE + shift
        years = _tenor_years(tenor)
        # Simple continuous-compounding discount factor: exp(-r*t), computed
        # with Decimal-friendly fixed-point approximation to avoid floats:
        # DF ~= 1 / (1 + r*t) for this synthetic non-authoritative curve.
        df = (Decimal("1") / (Decimal("1") + zero_rate * years)).quantize(Decimal("0.000001"))
        points.append(
            DiscountFactorPoint(
                currency="USD",
                tenor=tenor,
                zero_rate=str(zero_rate.quantize(Decimal("0.0001"))),
                discount_factor=str(df),
            )
        )
    return tuple(points)


def build_market_scenarios(seed: int = DEFAULT_SEED) -> tuple[MarketSnapshot, ...]:
    rng = random.Random(seed)
    snapshots: list[MarketSnapshot] = []

    plan: list[tuple[str, int]] = (
        [(REGIME_QUIET, i) for i in range(N_QUIET)]
        + [(REGIME_RATE_SHOCK, i) for i in range(N_RATE_SHOCK)]
        + [(REGIME_FX_STRESS, i) for i in range(N_FX_STRESS)]
        + [(REGIME_COMBINED_LIQUIDITY_STRESS, i) for i in range(N_COMBINED_LIQUIDITY_STRESS)]
    )

    base_date = dt.date(2026, 1, 5)
    # Deliberate negative cases: one stale, one missing-quote, one
    # crossed-market snapshot per regime family that has at least one, plus
    # one snapshot combining stale+missing for the combined-liquidity-stress
    # regime (its very name implies a compounding data-quality problem).
    negative_case_indices = {
        (REGIME_QUIET, 0): ("stale", False, False),
        (REGIME_RATE_SHOCK, 0): ("missing", False, True),
        (REGIME_FX_STRESS, 0): ("crossed", True, False),
        (REGIME_COMBINED_LIQUIDITY_STRESS, 0): ("stale_and_missing", False, True),
    }

    for seq, (regime, idx_in_regime) in enumerate(plan, start=1):
        snapshot_id = f"SIM-MKT-SNAP-{seq:03d}"
        as_of = dt.datetime.combine(
            base_date + dt.timedelta(days=seq), dt.time(17, 0, 0), tzinfo=dt.timezone.utc
        )
        key = (regime, idx_in_regime)
        case_label, is_crossed, is_missing = negative_case_indices.get(key, (None, False, False))
        is_stale = case_label in ("stale", "stale_and_missing")

        fx_quotes = _build_fx_quotes(rng, snapshot_id, as_of, regime, crossed=is_crossed, missing=is_missing)
        discount_factors = _build_discount_factors(rng, regime)

        stale_after = as_of + dt.timedelta(hours=24)
        if is_stale:
            # The freshness window has already lapsed relative to as_of --
            # any consumer must treat this snapshot as STALE, never silently
            # accept it.
            stale_after = as_of - dt.timedelta(hours=1)

        notes_parts = []
        if is_stale:
            notes_parts.append("deliberately stale: stale_after precedes as_of")
        if is_missing:
            notes_parts.append(f"deliberately missing quote: {FX_PAIRS[-1]} omitted")
        if is_crossed:
            notes_parts.append("deliberately crossed/degenerate EUR/USD quote")
        notes = "; ".join(notes_parts) if notes_parts else "no deliberate data-quality issue"

        snapshots.append(
            MarketSnapshot(
                snapshot_id=snapshot_id,
                regime=regime,
                as_of=as_of,
                timezone="UTC",
                source_label="SIMULATED",
                freshness_policy=FreshnessPolicy(stale_after=stale_after, max_age_hours=24),
                fx_quotes=fx_quotes,
                discount_factors=discount_factors,
                negative_case=NegativeCaseFlags(
                    is_stale=is_stale, has_missing_quote=is_missing, has_crossed_market=is_crossed, notes=notes,
                ),
                provenance=make_provenance(SCENARIO_ID),
            )
        )

    if len(snapshots) != TOTAL_SNAPSHOTS:
        raise ValueError(f"expected {TOTAL_SNAPSHOTS} market snapshots, built {len(snapshots)}")
    ids = [s.snapshot_id for s in snapshots]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate snapshot_id in market scenario corpus")
    return tuple(snapshots)

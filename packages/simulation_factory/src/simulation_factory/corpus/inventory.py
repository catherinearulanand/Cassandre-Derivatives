"""SIM-006: the synthetic inventory corpus, per plan Section 9.5.

600 positions across USD/EUR/INR cash, US Treasuries, synthetic sovereign
securities, corporate bonds, equities, and gold, carrying every field plan
Section 9.5 names (account, location, owner, settled/projected/encumbered/
pending/reserved quantity, issuer group, currency, price, haircut inputs,
funding/liquidity cost, settlement route, cutoff, corporate-action flags,
source watermark, `stale_after`) plus the full set of nine deliberate trap
categories the plan lists: ineligible, wrong-way, stale-price,
wrong-location, encumbered, pending, reserved, recalled, and
minimum-lot-conflicting.

This is a standalone corpus: it defines its own `InventoryPosition`
dataclass rather than importing `collateralos.domain.inventory` (this
package is never imported by, and never imports, product code). The shape
is deliberately similar in spirit to `products/collateralos/tests/fixtures/
inventory_corpus.py`'s 20-position slice, scaled to the full 600-position
target.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass
from decimal import Decimal

from money_types import Money, Price, PriceBasis, Quantity

from simulation_factory.identifiers import require_sim_identifier
from simulation_factory.institution import CASSANDRE_SIM_BANK_IFSC, CASSANDRE_SIM_BANK_NA
from simulation_factory.provenance import SimulationProvenance, make_provenance

SCENARIO_ID = "SIM-SCENARIO-CORPUS-INVENTORY"
DEFAULT_SEED = 20260601

TOTAL_POSITION_COUNT = 600

# Asset-class distribution; must sum to TOTAL_POSITION_COUNT.
ASSET_CLASS_COUNTS: tuple[tuple[str, int], ...] = (
    ("CASH_USD", 90),
    ("CASH_EUR", 60),
    ("CASH_INR", 50),
    ("US_TREASURY", 120),
    ("SYNTHETIC_SOVEREIGN", 70),
    ("CORPORATE_BOND", 90),
    ("EQUITY", 70),
    ("GOLD", 50),
)
assert sum(n for _, n in ASSET_CLASS_COUNTS) == TOTAL_POSITION_COUNT

# Under DEC-COL-01's provisional policy (POLICY-BUNDLE-001: cash + UST only
# eligible), these asset classes are structurally ineligible regardless of
# any other trap flag.
INELIGIBLE_ASSET_CLASSES = frozenset({"SYNTHETIC_SOVEREIGN", "CORPORATE_BOND", "EQUITY", "GOLD"})

CASH_ASSET_CLASSES = frozenset({"CASH_USD", "CASH_EUR", "CASH_INR"})
_ASSET_CLASS_CURRENCY = {"CASH_USD": "USD", "CASH_EUR": "EUR", "CASH_INR": "INR"}

# Nine-slot trap cycle. Slot 0 means "no extra trap" for an otherwise
# eligible asset class, or "ineligible-by-asset-class only" for an
# ineligible one; slots 1-8 layer one specific additional trap, giving both
# single-reason and combined-reason ("ineligible AND stale", etc.) coverage.
TRAP_CYCLE: tuple[str, ...] = (
    "NONE", "WRONG_WAY", "STALE_PRICE", "WRONG_LOCATION",
    "ENCUMBERED", "PENDING", "RESERVED", "RECALLED", "MIN_LOT_CONFLICT",
)

APPROVED_LOCATIONS = ("SIM-LOC-CUSTODY-0001", "SIM-LOC-CUSTODY-0002")
UNAPPROVED_LOCATION = "SIM-LOC-UNAPPROVED-0099"

WRONG_WAY_COUNTERPARTY_IDS = tuple(f"SIM-CPTY-{i:03d}" for i in range(1, 9))

_AS_OF = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
_FRESH_PRICE_AS_OF = dt.datetime(2026, 5, 31, tzinfo=dt.timezone.utc)
_FRESH_STALE_AFTER = dt.datetime(2026, 6, 2, tzinfo=dt.timezone.utc)
_ALREADY_STALE_AFTER = dt.datetime(2026, 5, 30, tzinfo=dt.timezone.utc)


@dataclass(frozen=True)
class InventoryPosition:
    position_id: str
    owner_entity_id: str
    account_id: str
    location: str
    asset_class: str
    issuer_group_id: str | None
    currency: str
    settled_quantity: Quantity
    projected_quantity: Quantity
    encumbered_quantity: Quantity
    pending_outbound_quantity: Quantity
    reserved_quantity: Quantity
    minimum_lot: Quantity
    price: Price
    market_value: Money
    haircut_pct: str
    fx_haircut_pct: str
    funding_cost_bps: str
    liquidity_cost_bps: str
    settlement_route: str
    cutoff_time_utc: str
    corporate_action_flags: tuple[str, ...]
    source_watermark: str
    stale_after: dt.datetime
    is_recalled: bool
    is_self_issued: bool
    is_affiliate_issued: bool
    is_wrong_way: bool
    wrong_way_counterparty_id: str | None
    trap_types: tuple[str, ...]
    provenance: SimulationProvenance

    def __post_init__(self) -> None:
        require_sim_identifier(self.position_id, field_name="position_id")
        require_sim_identifier(self.owner_entity_id, field_name="owner_entity_id")
        require_sim_identifier(self.account_id, field_name="account_id")

    @property
    def is_eligible_by_asset_class(self) -> bool:
        return self.asset_class not in INELIGIBLE_ASSET_CLASSES

    @property
    def reservable_quantity(self) -> Decimal:
        """Plan Section 9.5's conservative formula:
        `reservable = settled_reconciled_quantity - encumbered -
        active_reservations - pending_outbound - blocked_or_recalled`.
        Negative is a data exception, never floored to zero here -- callers
        decide how to treat it."""

        blocked = self.settled_quantity.amount if self.is_recalled else Decimal("0")
        return (
            self.settled_quantity.amount
            - self.encumbered_quantity.amount
            - self.reserved_quantity.amount
            - self.pending_outbound_quantity.amount
            - blocked
        )


def _qty(amount: Decimal, unit: str) -> Quantity:
    return Quantity(amount=str(amount), unit=unit)


def _price_basis_for(asset_class: str) -> PriceBasis:
    if asset_class in ("US_TREASURY", "SYNTHETIC_SOVEREIGN", "CORPORATE_BOND"):
        return PriceBasis.CLEAN
    return PriceBasis.MARKET_VALUE


def _unit_for(asset_class: str) -> str:
    if asset_class in CASH_ASSET_CLASSES:
        return asset_class
    if asset_class in ("US_TREASURY", "SYNTHETIC_SOVEREIGN", "CORPORATE_BOND"):
        return "BOND_FACE"
    if asset_class == "EQUITY":
        return "SHARES"
    if asset_class == "GOLD":
        return "TROY_OUNCES"
    raise ValueError(f"unhandled asset_class {asset_class!r}")


def _currency_for(asset_class: str, rng: random.Random) -> str:
    if asset_class in _ASSET_CLASS_CURRENCY:
        return _ASSET_CLASS_CURRENCY[asset_class]
    if asset_class == "GOLD":
        return "USD"
    return rng.choice(("USD", "EUR"))


def _build_one(
    *,
    seq: int,
    asset_class: str,
    trap: str,
    rng: random.Random,
) -> InventoryPosition:
    position_id = f"SIM-INV-POS-{seq:05d}"
    currency = _currency_for(asset_class, rng)
    unit = _unit_for(asset_class)

    if trap == "WRONG_WAY" and asset_class in CASH_ASSET_CLASSES:
        # Wrong-way risk presumes an issuer correlated with the
        # counterparty; a cash position has no issuer, so this slot
        # degrades to "no extra trap" for cash asset classes rather than
        # producing an incoherent wrong-way cash position.
        trap = "NONE"

    if asset_class in CASH_ASSET_CLASSES:
        base_qty = Decimal(rng.randint(10_000, 5_000_000))
        unit_price = Decimal("1.00")
    elif asset_class == "GOLD":
        base_qty = Decimal(rng.randint(10, 5_000))
        unit_price = Decimal(rng.randint(1_800, 2_400))
    elif asset_class == "EQUITY":
        base_qty = Decimal(rng.randint(100, 50_000))
        unit_price = Decimal(rng.randint(500, 40_000)).scaleb(-2)
    else:  # bond-like: US_TREASURY, SYNTHETIC_SOVEREIGN, CORPORATE_BOND
        base_qty = Decimal(rng.randint(10_000, 5_000_000))
        unit_price = (Decimal("100") + Decimal(rng.randint(-500, 500)).scaleb(-2))

    settled = base_qty
    is_min_lot_trap = trap == "MIN_LOT_CONFLICT"
    minimum_lot = Decimal("1000") if asset_class in CASH_ASSET_CLASSES else Decimal("10000")
    if is_min_lot_trap:
        settled = (minimum_lot / Decimal(4)).quantize(Decimal("1"))  # deliberately below the minimum lot

    is_encumbered = trap == "ENCUMBERED"
    is_pending = trap == "PENDING"
    is_reserved = trap == "RESERVED"
    is_recalled = trap == "RECALLED"
    is_stale = trap == "STALE_PRICE"
    is_wrong_location = trap == "WRONG_LOCATION"
    is_wrong_way = trap == "WRONG_WAY"

    encumbered_qty = (settled * Decimal("0.10")).quantize(Decimal("1")) if is_encumbered else Decimal("0")
    pending_qty = (settled * Decimal("0.05")).quantize(Decimal("1")) if is_pending else Decimal("0")
    reserved_qty = (settled * Decimal("0.15")).quantize(Decimal("1")) if is_reserved else Decimal("0")

    trap_types: list[str] = []
    if asset_class in INELIGIBLE_ASSET_CLASSES:
        trap_types.append("INELIGIBLE_ASSET_CLASS")
    if trap != "NONE":
        trap_types.append(trap)

    issuer_group_id: str | None = None
    wrong_way_cpty: str | None = None
    is_self_issued = False
    is_affiliate_issued = False
    if asset_class not in CASH_ASSET_CLASSES:
        if is_wrong_way:
            wrong_way_cpty = WRONG_WAY_COUNTERPARTY_IDS[seq % len(WRONG_WAY_COUNTERPARTY_IDS)]
            issuer_group_id = f"SIM-ISSUER-GROUP-WRONGWAY-{wrong_way_cpty}"
            # Alternate the wrong-way rationale across self-issued /
            # affiliate-issued / counterparty-related, per plan Section
            # 12.3 ("Identify self-issued, affiliate-issued, and
            # counterparty-related securities separately.").
            variant = seq % 3
            is_self_issued = variant == 0
            is_affiliate_issued = variant == 1
        else:
            issuer_group_id = f"SIM-ISSUER-GROUP-{asset_class}-{(seq % 12) + 1:03d}"

    location = UNAPPROVED_LOCATION if is_wrong_location else APPROVED_LOCATIONS[seq % len(APPROVED_LOCATIONS)]
    stale_after = _ALREADY_STALE_AFTER if is_stale else _FRESH_STALE_AFTER
    owner_entity_id = CASSANDRE_SIM_BANK_IFSC.entity_id if seq % 37 == 0 else CASSANDRE_SIM_BANK_NA.entity_id

    is_bond_like = asset_class in ("US_TREASURY", "SYNTHETIC_SOVEREIGN", "CORPORATE_BOND")
    if is_bond_like:
        # unit_price is a clean price per 100 face value.
        market_value_amount = settled * unit_price / Decimal("100")
    else:
        market_value_amount = settled * unit_price
    market_value_amount = market_value_amount.quantize(Decimal("0.01"))

    haircut_pct = "0.00" if asset_class in CASH_ASSET_CLASSES else ("2.00" if asset_class == "US_TREASURY" else "15.00")

    corporate_action_flags: tuple[str, ...] = ()
    if asset_class == "EQUITY" and seq % 11 == 0:
        corporate_action_flags = ("PENDING_DIVIDEND",)

    return InventoryPosition(
        position_id=position_id,
        owner_entity_id=owner_entity_id,
        account_id=f"SIM-ACCT-COLLAT-{(seq % 6) + 1:03d}",
        location=location,
        asset_class=asset_class,
        issuer_group_id=issuer_group_id,
        currency=currency,
        settled_quantity=_qty(settled, unit),
        projected_quantity=_qty(settled, unit),
        encumbered_quantity=_qty(encumbered_qty, unit),
        pending_outbound_quantity=_qty(pending_qty, unit),
        reserved_quantity=_qty(reserved_qty, unit),
        minimum_lot=_qty(minimum_lot, unit),
        price=Price(amount=str(unit_price), quote_currency=currency, price_basis=_price_basis_for(asset_class), as_of=_FRESH_PRICE_AS_OF),
        market_value=Money(amount=str(market_value_amount), currency=currency, rounding_context="SIM-INVENTORY-MARKET-VALUE"),
        haircut_pct=haircut_pct,
        fx_haircut_pct="0.00" if currency == "USD" else "1.00",
        funding_cost_bps=str(Decimal(rng.randint(5, 50))),
        liquidity_cost_bps=str(Decimal(rng.randint(1, 25))),
        settlement_route=f"SIM-ROUTE-{currency}-{(seq % 6) + 1:03d}",
        cutoff_time_utc="17:00:00",
        corporate_action_flags=corporate_action_flags,
        source_watermark=f"SIM-CUSTODY-FEED-{_AS_OF.date().isoformat()}",
        stale_after=stale_after,
        is_recalled=is_recalled,
        is_self_issued=is_self_issued,
        is_affiliate_issued=is_affiliate_issued,
        is_wrong_way=is_wrong_way,
        wrong_way_counterparty_id=wrong_way_cpty,
        trap_types=tuple(trap_types),
        provenance=make_provenance(SCENARIO_ID),
    )


def build_inventory_corpus(seed: int = DEFAULT_SEED) -> tuple[InventoryPosition, ...]:
    rng = random.Random(seed)
    positions: list[InventoryPosition] = []
    seq = 0
    for asset_class, count in ASSET_CLASS_COUNTS:
        for i in range(count):
            seq += 1
            trap = TRAP_CYCLE[i % len(TRAP_CYCLE)]
            positions.append(_build_one(seq=seq, asset_class=asset_class, trap=trap, rng=rng))

    if len(positions) != TOTAL_POSITION_COUNT:
        raise ValueError(f"expected {TOTAL_POSITION_COUNT} inventory positions, built {len(positions)}")
    ids = [p.position_id for p in positions]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate position_id in inventory corpus")
    return tuple(positions)

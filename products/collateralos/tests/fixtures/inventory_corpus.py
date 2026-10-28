"""Twenty synthetic inventory positions for the plan Section 25.5 slice
("manually imported obligation + twenty inventory positions"), scaled down
from Section 9.5's 600-position corpus pattern.

Ten positions are ELIGIBLE under `SIM-POLICY-001` (cash USD/EUR, US
Treasury); ten are deliberately ineligible traps covering every category
Section 9.5 names: wrong-way, stale-priced, encumbered, self-issued,
affiliate-issued, recalled, wrong asset class (x2), a combined-reason
position, and a minimum-lot-conflicting position. Full breadth (SIM-006's
600-position corpus) is out of scope for this session -- this is the
20-position slice only.

All identifiers are `SIM-*` namespaced; nothing here is real institution,
account, or security data.
"""

from __future__ import annotations

import datetime as dt

from money_types import Money, Quantity

from collateralos.domain.inventory import InventoryPosition
from collateralos.domain.policy import AssetClass
from collateralos.domain.reservation import InventoryBalanceState, balances_from_positions

OWNER_ENTITY_ID = "SIM-ENTITY-CASSANDRE-BANK-NA"
COUNTERPARTY_ID = "SIM-CPTY-0001"  # matches wrong-way-map-001.json entry 1

AS_OF = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
_FRESH_PRICE_AS_OF = dt.datetime(2026, 5, 31, tzinfo=dt.timezone.utc)
_FRESH_STALE_AFTER = dt.datetime(2026, 6, 2, tzinfo=dt.timezone.utc)
_ALREADY_STALE_AFTER = dt.datetime(2026, 5, 30, tzinfo=dt.timezone.utc)

ISSUER_GROUP_UST = "SIM-ISSUER-GROUP-UST-SOVEREIGN"
ISSUER_GROUP_WRONG_WAY = "SIM-ISSUER-GROUP-0001"  # excluded for SIM-CPTY-0001
ISSUER_GROUP_SELF = "SIM-ISSUER-GROUP-CASNA-SELF"
ISSUER_GROUP_AFFILIATE = "SIM-ISSUER-GROUP-CASIF-AFFILIATE"
ISSUER_GROUP_CORP = "SIM-ISSUER-GROUP-CORP-0003"


def _position(
    *,
    seq: int,
    asset_class: AssetClass,
    quantity: str,
    unit: str,
    market_value: str,
    currency: str = "USD",
    issuer_group_id: str | None = None,
    is_encumbered: bool = False,
    is_recalled: bool = False,
    is_self_issued: bool = False,
    is_affiliate_issued: bool = False,
    stale_after: dt.datetime = _FRESH_STALE_AFTER,
    account_id: str = "SIM-ACCT-COLLAT-0001",
    location: str = "SIM-LOC-CUSTODY-0001",
) -> InventoryPosition:
    return InventoryPosition(
        position_id=f"SIM-POS-{seq:04d}",
        owner_entity_id=OWNER_ENTITY_ID,
        account_id=account_id,
        location=location,
        asset_class=asset_class,
        issuer_group_id=issuer_group_id,
        currency=currency,
        market_value=Money(amount=market_value, currency=currency, rounding_context="market"),
        quantity=Quantity(amount=quantity, unit=unit),
        price_as_of=_FRESH_PRICE_AS_OF,
        stale_after=stale_after,
        is_encumbered=is_encumbered,
        is_recalled=is_recalled,
        is_self_issued=is_self_issued,
        is_affiliate_issued=is_affiliate_issued,
    )


def build_twenty_position_corpus() -> tuple[InventoryPosition, ...]:
    positions = [
        # --- ten ELIGIBLE positions ---
        _position(seq=1, asset_class=AssetClass.CASH_USD, quantity="500000", unit="CASH_USD", market_value="500000.00"),
        _position(seq=2, asset_class=AssetClass.CASH_USD, quantity="300000", unit="CASH_USD", market_value="300000.00"),
        _position(seq=3, asset_class=AssetClass.CASH_EUR, quantity="200000", unit="CASH_EUR", market_value="216000.00"),
        _position(
            seq=4, asset_class=AssetClass.US_TREASURY, quantity="1000000", unit="UST_FACE",
            market_value="1010000.00", issuer_group_id=ISSUER_GROUP_UST,
        ),
        _position(
            seq=5, asset_class=AssetClass.US_TREASURY, quantity="500000", unit="UST_FACE",
            market_value="505000.00", issuer_group_id=ISSUER_GROUP_UST,
        ),
        _position(
            seq=6, asset_class=AssetClass.US_TREASURY, quantity="2000000", unit="UST_FACE",
            market_value="2020000.00", issuer_group_id=ISSUER_GROUP_UST,
        ),
        _position(seq=7, asset_class=AssetClass.CASH_USD, quantity="150000", unit="CASH_USD", market_value="150000.00"),
        _position(seq=8, asset_class=AssetClass.CASH_EUR, quantity="100000", unit="CASH_EUR", market_value="108000.00"),
        _position(
            seq=9, asset_class=AssetClass.US_TREASURY, quantity="750000", unit="UST_FACE",
            market_value="757500.00", issuer_group_id=ISSUER_GROUP_UST,
        ),
        _position(seq=10, asset_class=AssetClass.CASH_USD, quantity="50000", unit="CASH_USD", market_value="50000.00"),
        # --- ten deliberately ineligible traps ---
        _position(
            seq=11, asset_class=AssetClass.CORPORATE_BOND, quantity="100000", unit="BOND_FACE",
            market_value="101000.00", issuer_group_id=ISSUER_GROUP_CORP,
        ),  # ASSET_CLASS_NOT_ELIGIBLE
        _position(
            seq=12, asset_class=AssetClass.US_TREASURY, quantity="200000", unit="UST_FACE",
            market_value="202000.00", issuer_group_id=ISSUER_GROUP_WRONG_WAY,
        ),  # WRONG_WAY_EXCLUDED for SIM-CPTY-0001
        _position(
            seq=13, asset_class=AssetClass.US_TREASURY, quantity="300000", unit="UST_FACE",
            market_value="303000.00", issuer_group_id=ISSUER_GROUP_UST, stale_after=_ALREADY_STALE_AFTER,
        ),  # STALE_PRICE
        _position(
            seq=14, asset_class=AssetClass.CASH_USD, quantity="100000", unit="CASH_USD",
            market_value="100000.00", is_encumbered=True,
        ),  # ENCUMBERED
        _position(
            seq=15, asset_class=AssetClass.US_TREASURY, quantity="100000", unit="UST_FACE",
            market_value="101000.00", issuer_group_id=ISSUER_GROUP_SELF, is_self_issued=True,
        ),  # SELF_ISSUED
        _position(
            seq=16, asset_class=AssetClass.US_TREASURY, quantity="100000", unit="UST_FACE",
            market_value="101000.00", issuer_group_id=ISSUER_GROUP_AFFILIATE, is_affiliate_issued=True,
        ),  # AFFILIATE_ISSUED
        _position(
            seq=17, asset_class=AssetClass.CASH_USD, quantity="50000", unit="CASH_USD",
            market_value="50000.00", is_recalled=True,
        ),  # RECALLED
        _position(
            seq=18, asset_class=AssetClass.EQUITY, quantity="1000", unit="SHARES", market_value="99000.00",
            issuer_group_id=ISSUER_GROUP_CORP,
        ),  # ASSET_CLASS_NOT_ELIGIBLE (second asset-class trap)
        _position(
            seq=19, asset_class=AssetClass.US_TREASURY, quantity="100000", unit="UST_FACE",
            market_value="101000.00", issuer_group_id=ISSUER_GROUP_UST,
            is_encumbered=True, stale_after=_ALREADY_STALE_AFTER,
        ),  # combined ENCUMBERED + STALE_PRICE (ordered multi-reason trap)
        _position(
            seq=20, asset_class=AssetClass.CASH_USD, quantity="750", unit="CASH_USD", market_value="750.00",
        ),  # ELIGIBLE by policy, but below the USD 1,000 minimum lot -- minimum-lot-conflicting trap
    ]
    return tuple(positions)


def build_balances_for_corpus(
    positions: tuple[InventoryPosition, ...],
) -> dict[str, InventoryBalanceState]:
    """`gross_settled` equals the full custody quantity, with
    `encumbered`/`blocked_or_recalled` mirroring the position's own flags so
    the reservation ledger (not just the static eligibility compiler)
    independently enforces the same traps. See
    `collateralos.domain.reservation.balances_from_positions`."""
    return balances_from_positions(positions)

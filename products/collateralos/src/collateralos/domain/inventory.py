"""`InventoryPosition` / `InventorySnapshot` / `InventoryBalance` (Section 12.1,
9.5). Twenty-position corpus per plan Section 25.5 lives in
`products/collateralos/tests/fixtures/inventory_corpus.py`, not here --
this module defines the general-purpose types only.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from money_types import Money, Quantity

from collateralos.domain.identifiers import require_sim_identifier
from collateralos.domain.policy import AssetClass


@dataclass(frozen=True)
class InventoryPosition:
    position_id: str
    owner_entity_id: str
    account_id: str
    location: str
    asset_class: AssetClass
    issuer_group_id: str | None
    currency: str
    market_value: Money
    quantity: Quantity
    price_as_of: dt.datetime
    stale_after: dt.datetime
    is_encumbered: bool
    is_recalled: bool
    is_self_issued: bool
    is_affiliate_issued: bool

    def __post_init__(self) -> None:
        require_sim_identifier(self.position_id, field_name="position_id")
        require_sim_identifier(self.owner_entity_id, field_name="owner_entity_id")
        if self.issuer_group_id is not None:
            require_sim_identifier(self.issuer_group_id, field_name="issuer_group_id")
        if self.price_as_of.tzinfo is None or self.stale_after.tzinfo is None:
            raise ValueError("price_as_of and stale_after must be timezone-aware")

    def is_stale(self, as_of: dt.datetime) -> bool:
        return self.stale_after <= as_of

    def adjusted_value(self, haircut: Decimal) -> Money:
        adjusted = (self.market_value.amount * (Decimal(1) - haircut)).quantize(Decimal("0.01"))
        return Money(amount=str(adjusted), currency=self.market_value.currency, rounding_context="haircut_adjusted")


@dataclass(frozen=True)
class InventorySnapshot:
    snapshot_id: str
    version: str
    as_of: dt.datetime
    positions: tuple[InventoryPosition, ...]

    def __post_init__(self) -> None:
        require_sim_identifier(self.snapshot_id, field_name="snapshot_id")
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        ids = [p.position_id for p in self.positions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate position_id within one InventorySnapshot")

    def position_by_id(self, position_id: str) -> InventoryPosition:
        for position in self.positions:
            if position.position_id == position_id:
                return position
        raise KeyError(f"no position {position_id!r} in snapshot {self.snapshot_id}")

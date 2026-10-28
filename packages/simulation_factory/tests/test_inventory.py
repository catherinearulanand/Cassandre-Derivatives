"""SIM-006: inventory corpus asset-class coverage and adversarial trap
coverage, per plan Section 9.5."""

from __future__ import annotations

from decimal import Decimal

from simulation_factory.corpus.inventory import (
    ASSET_CLASS_COUNTS,
    INELIGIBLE_ASSET_CLASSES,
    TRAP_CYCLE,
    build_inventory_corpus,
)


def test_asset_class_distribution_matches_declared_counts():
    positions = build_inventory_corpus()
    counts: dict[str, int] = {}
    for p in positions:
        counts[p.asset_class] = counts.get(p.asset_class, 0) + 1
    assert counts == dict(ASSET_CLASS_COUNTS)


def test_every_named_trap_category_is_represented():
    positions = build_inventory_corpus()
    all_traps = {t for p in positions for t in p.trap_types}
    expected = {
        "INELIGIBLE_ASSET_CLASS", "WRONG_WAY", "STALE_PRICE", "WRONG_LOCATION",
        "ENCUMBERED", "PENDING", "RESERVED", "RECALLED", "MIN_LOT_CONFLICT",
    }
    assert expected <= all_traps


def test_ineligible_asset_classes_are_always_tagged():
    positions = build_inventory_corpus()
    for p in positions:
        if p.asset_class in INELIGIBLE_ASSET_CLASSES:
            assert "INELIGIBLE_ASSET_CLASS" in p.trap_types
            assert p.is_eligible_by_asset_class is False
        else:
            assert "INELIGIBLE_ASSET_CLASS" not in p.trap_types
            assert p.is_eligible_by_asset_class is True


def test_minimum_lot_conflicting_positions_are_actually_below_the_minimum_lot():
    positions = build_inventory_corpus()
    trapped = [p for p in positions if "MIN_LOT_CONFLICT" in p.trap_types]
    assert trapped
    for p in trapped:
        assert p.settled_quantity.amount < p.minimum_lot.amount


def test_recalled_positions_have_zero_or_negative_reservable_quantity():
    positions = build_inventory_corpus()
    for p in positions:
        if p.is_recalled:
            assert p.reservable_quantity <= Decimal("0")


def test_reservable_quantity_formula_matches_plan_section_9_5():
    positions = build_inventory_corpus()
    for p in positions:
        expected = (
            p.settled_quantity.amount
            - p.encumbered_quantity.amount
            - p.reserved_quantity.amount
            - p.pending_outbound_quantity.amount
            - (p.settled_quantity.amount if p.is_recalled else Decimal("0"))
        )
        assert p.reservable_quantity == expected


def test_wrong_way_positions_carry_a_counterparty_reference():
    positions = build_inventory_corpus()
    wrong_way = [p for p in positions if p.is_wrong_way]
    assert wrong_way
    for p in wrong_way:
        assert p.wrong_way_counterparty_id is not None
        assert p.issuer_group_id is not None


def test_position_ids_are_unique():
    positions = build_inventory_corpus()
    ids = [p.position_id for p in positions]
    assert len(ids) == len(set(ids))

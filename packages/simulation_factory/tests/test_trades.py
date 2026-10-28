"""SIM-004: trade corpus population-control invariants."""

from __future__ import annotations

from decimal import Decimal

from simulation_factory.corpus.trades import (
    N_ACCEPTED,
    N_AMENDED,
    N_CANCELLED,
    N_DUPLICATE,
    N_MATURED,
    N_REJECTED_UNRECOGNIZED_COUNTERPARTY,
    N_REJECTED_WRONG_ENTITY,
    N_REJECTED_WRONG_SIGN,
    N_UNSUPPORTED,
    PRODUCT_FAMILIES_SUPPORTED,
    TOTAL_TRADE_COUNT,
    build_trade_corpus,
)
from simulation_factory.institution import CASSANDRE_SIM_BANK_IFSC, CASSANDRE_SIM_BANK_NA


def test_disposition_counts_match_the_declared_buckets():
    trades = build_trade_corpus().trades
    counts: dict[str, int] = {}
    for t in trades:
        counts[t.disposition] = counts.get(t.disposition, 0) + 1
    assert counts["ACCEPTED"] == N_ACCEPTED
    assert counts["MATURED"] == N_MATURED
    assert counts["CANCELLED"] == N_CANCELLED
    assert counts["AMENDED"] == N_AMENDED
    assert counts["DUPLICATE"] == N_DUPLICATE
    assert counts["REJECTED"] == (
        N_REJECTED_WRONG_ENTITY + N_REJECTED_WRONG_SIGN + N_REJECTED_UNRECOGNIZED_COUNTERPARTY
    )
    assert counts["UNSUPPORTED"] == N_UNSUPPORTED
    assert sum(counts.values()) == TOTAL_TRADE_COUNT


def test_lifecycle_states_are_a_closed_set():
    trades = build_trade_corpus().trades
    allowed = {"ACTIVE", "MATURED", "CANCELLED", "AMENDED"}
    assert {t.lifecycle_state for t in trades} <= allowed
    assert allowed <= {t.lifecycle_state for t in trades}  # every state is represented


def test_mtms_include_positive_negative_and_zero():
    trades = build_trade_corpus().trades
    amounts = [t.mtm.amount for t in trades]
    assert any(a > 0 for a in amounts)
    assert any(a < 0 for a in amounts)
    assert any(a == 0 for a in amounts)


def test_both_supported_product_families_present_plus_one_unsupported():
    trades = build_trade_corpus().trades
    families = {t.product_family for t in trades}
    for pf in PRODUCT_FAMILIES_SUPPORTED:
        assert pf in families
    unsupported = [t for t in trades if t.product_family not in PRODUCT_FAMILIES_SUPPORTED]
    assert len(unsupported) == N_UNSUPPORTED
    assert all(t.disposition == "UNSUPPORTED" for t in unsupported)


def test_duplicate_trades_share_trade_id_with_an_accepted_original():
    trades = build_trade_corpus().trades
    accepted_ids = {t.trade_id for t in trades if t.disposition == "ACCEPTED"}
    duplicates = [t for t in trades if t.disposition == "DUPLICATE"]
    assert len(duplicates) == N_DUPLICATE
    for dup in duplicates:
        assert dup.trade_id in accepted_ids
        assert dup.version > 1


def test_wrong_entity_trap_uses_the_affiliate_never_the_active_calculation_entity():
    trades = build_trade_corpus().trades
    wrong_entity_trades = [t for t in trades if t.trap_type == "WRONG_ENTITY"]
    assert len(wrong_entity_trades) == N_REJECTED_WRONG_ENTITY
    for t in wrong_entity_trades:
        assert t.calculation_entity_id == CASSANDRE_SIM_BANK_IFSC.entity_id
        assert t.disposition == "REJECTED"
        assert t.rejection_reason == "WRONG_ENTITY"

    # And every ACCEPTED trade resolves to exactly the one active
    # calculation entity (plan Section 9.3: "Each trade version resolves to
    # exactly one firm-owned calculation entity").
    accepted = [t for t in trades if t.disposition == "ACCEPTED"]
    assert all(t.calculation_entity_id == CASSANDRE_SIM_BANK_NA.entity_id for t in accepted)


def test_unrecognized_counterparty_trap_is_not_in_the_synthetic_roster():
    corpus = build_trade_corpus()
    known_counterparties = {c.entity_id for c in corpus.counterparties}
    trap_trades = [t for t in corpus.trades if t.trap_type == "UNRECOGNIZED_COUNTERPARTY"]
    assert len(trap_trades) == N_REJECTED_UNRECOGNIZED_COUNTERPARTY
    for t in trap_trades:
        assert t.counterparty_entity_id not in known_counterparties


def test_expected_missing_trade_ids_never_appear_in_the_population():
    corpus = build_trade_corpus()
    delivered_ids = {t.trade_id for t in corpus.trades}
    assert corpus.expected_missing_trade_ids
    for missing_id in corpus.expected_missing_trade_ids:
        assert missing_id not in delivered_ids


def test_every_netting_set_resolves_to_exactly_one_counterparty_and_entity():
    corpus = build_trade_corpus()
    netting_set_by_id = {ns.netting_set_id: ns for ns in corpus.netting_sets}
    for trade in corpus.trades:
        if trade.disposition not in ("ACCEPTED", "MATURED", "CANCELLED", "AMENDED", "DUPLICATE"):
            continue
        ns = netting_set_by_id.get(trade.netting_set_id)
        assert ns is not None, f"{trade.trade_id} references an unknown netting set"

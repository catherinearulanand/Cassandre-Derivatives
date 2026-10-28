"""SIM-009: consolidated adversarial/negative fixture index."""

from __future__ import annotations

from simulation_factory.adversarial_index import build_adversarial_index
from simulation_factory.corpus.agreements import build_agreement_bundles
from simulation_factory.corpus.inventory import build_inventory_corpus
from simulation_factory.corpus.market import build_market_scenarios
from simulation_factory.corpus.trades import build_trade_corpus


def _build_index():
    return build_adversarial_index(
        agreement_bundles=build_agreement_bundles(),
        trade_corpus=build_trade_corpus(),
        market_snapshots=build_market_scenarios(),
        inventory_positions=build_inventory_corpus(),
    )


def test_index_covers_all_four_domains():
    index = _build_index()
    domains = {c.domain for c in index.cases}
    assert domains == {"AGREEMENT", "TRADE", "MARKET", "INVENTORY"}


def test_index_case_ids_are_unique_and_sim_namespaced():
    from simulation_factory.identifiers import is_sim_identifier

    index = _build_index()
    ids = [c.index_id for c in index.cases]
    assert len(ids) == len(set(ids))
    assert all(is_sim_identifier(i) for i in ids)


def test_index_includes_the_named_headline_cases():
    index = _build_index()
    case_types = {c.case_type for c in index.cases}
    for expected in (
        "MISSING_TRADE", "DUPLICATE", "WRONG_ENTITY", "WRONG_SIGN",
        "STALE_PRICE", "WRONG_WAY", "ENCUMBERED", "RECALLED", "MIN_LOT_CONFLICT",
        "STALE_SNAPSHOT", "MISSING_QUOTE", "CROSSED_MARKET",
    ):
        assert expected in case_types, f"missing headline case_type {expected!r}"


def test_by_domain_and_by_case_type_filters_are_consistent_with_full_list():
    index = _build_index()
    trade_cases = index.by_domain("TRADE")
    assert all(c.domain == "TRADE" for c in trade_cases)
    assert len(trade_cases) <= len(index.cases)

    duplicate_cases = index.by_case_type("DUPLICATE")
    assert all(c.case_type == "DUPLICATE" for c in duplicate_cases)
    assert len(duplicate_cases) == 5  # N_DUPLICATE in corpus.trades


def test_trade_case_count_matches_trapped_trades_plus_missing_ids():
    trade_corpus = build_trade_corpus()
    index = _build_index()
    trapped = sum(1 for t in trade_corpus.trades if t.trap_type is not None)
    expected_missing = len(trade_corpus.expected_missing_trade_ids)
    assert len(index.by_domain("TRADE")) == trapped + expected_missing

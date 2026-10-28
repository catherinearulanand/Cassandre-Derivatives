"""Every identifier in every generated corpus must be `SIM-*` namespaced
(plan Section 9.1), and no real-institution-shaped identifier may collide
with anything in the corpus."""

from __future__ import annotations

from simulation_factory.adversarial_index import build_adversarial_index
from simulation_factory.corpus.agreements import build_agreement_bundles
from simulation_factory.corpus.inventory import build_inventory_corpus
from simulation_factory.corpus.market import build_market_scenarios
from simulation_factory.corpus.trades import build_trade_corpus
from simulation_factory.corpus.users import build_static_data_corpus
from simulation_factory.identifiers import is_sim_identifier

# A short list of well-known real identifiers that must never appear
# anywhere in the generated corpus, to guard against an accidental real
# name leaking in.
REAL_IDENTIFIER_TRAPS = (
    "JPMORGAN", "GOLDMAN", "CITIBANK", "HSBC", "DEUTSCHE BANK",
    "US912828", "037833100",  # real-shaped CUSIP prefixes
)


def _assert_all_sim(ids: list[str], label: str) -> None:
    bad = [i for i in ids if not is_sim_identifier(i)]
    assert not bad, f"{label}: found non-SIM-namespaced identifiers: {bad[:10]}"


def test_static_data_identifiers_are_sim_namespaced():
    corpus = build_static_data_corpus()
    _assert_all_sim([u.user_id for u in corpus.users], "users")
    _assert_all_sim([a.account_id for a in corpus.custody_accounts], "custody_accounts")
    _assert_all_sim([a.owner_entity_id for a in corpus.custody_accounts], "custody_account owners")
    _assert_all_sim([s.ssi_id for s in corpus.ssis], "ssis")
    _assert_all_sim([c.calendar_id for c in corpus.calendars], "calendars")


def test_agreement_bundle_ids_are_sim_namespaced():
    bundles = build_agreement_bundles()
    _assert_all_sim([b.bundle_id for b in bundles], "agreement bundles")


def test_trade_corpus_identifiers_are_sim_namespaced():
    corpus = build_trade_corpus()
    _assert_all_sim([c.entity_id for c in corpus.counterparties], "trade counterparties")
    _assert_all_sim([n.netting_set_id for n in corpus.netting_sets], "netting sets")
    _assert_all_sim([t.trade_id for t in corpus.trades], "trades")
    _assert_all_sim(
        [t.calculation_entity_id for t in corpus.trades], "trade calculation entities"
    )
    _assert_all_sim(list(corpus.expected_missing_trade_ids), "expected-missing trade ids")


def test_inventory_identifiers_are_sim_namespaced():
    positions = build_inventory_corpus()
    _assert_all_sim([p.position_id for p in positions], "inventory positions")
    _assert_all_sim([p.owner_entity_id for p in positions], "inventory owners")
    _assert_all_sim([p.account_id for p in positions], "inventory accounts")


def test_market_snapshot_ids_are_sim_namespaced():
    snapshots = build_market_scenarios()
    _assert_all_sim([s.snapshot_id for s in snapshots], "market snapshots")


def test_adversarial_index_ids_are_sim_namespaced():
    index = build_adversarial_index(
        agreement_bundles=build_agreement_bundles(),
        trade_corpus=build_trade_corpus(),
        market_snapshots=build_market_scenarios(),
        inventory_positions=build_inventory_corpus(),
    )
    _assert_all_sim([c.index_id for c in index.cases], "adversarial index cases")


def test_no_real_identifier_traps_anywhere():
    from simulation_factory.serialization import to_jsonable

    corpus_blobs = [
        to_jsonable(build_static_data_corpus()),
        to_jsonable(build_agreement_bundles()),
        to_jsonable(build_trade_corpus()),
        to_jsonable(build_market_scenarios()),
        to_jsonable(build_inventory_corpus()),
    ]
    import json

    text = json.dumps(corpus_blobs).upper()
    for trap in REAL_IDENTIFIER_TRAPS:
        assert trap not in text, f"found a real-identifier trap {trap!r} in generated corpus"

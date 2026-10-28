"""Every generator must be deterministic from a fixed seed: calling it
twice must produce byte-identical serialized output."""

from __future__ import annotations

from simulation_factory.adversarial_index import build_adversarial_index
from simulation_factory.corpus.agreements import build_agreement_bundles
from simulation_factory.corpus.inventory import build_inventory_corpus
from simulation_factory.corpus.market import build_market_scenarios
from simulation_factory.corpus.trades import build_trade_corpus
from simulation_factory.corpus.users import build_static_data_corpus
from simulation_factory.independent_reference.trade_population_reference import (
    compute_source_control_manifest,
)
from simulation_factory.scenario_catalogue import SCENARIO_CATALOGUE
from simulation_factory.serialization import dump_json_bytes


def test_static_data_corpus_is_deterministic():
    a = dump_json_bytes(build_static_data_corpus())
    b = dump_json_bytes(build_static_data_corpus())
    assert a == b


def test_agreement_bundles_are_deterministic():
    a = dump_json_bytes(build_agreement_bundles())
    b = dump_json_bytes(build_agreement_bundles())
    assert a == b


def test_trade_corpus_is_deterministic_for_fixed_seed():
    a = dump_json_bytes(build_trade_corpus(seed=111))
    b = dump_json_bytes(build_trade_corpus(seed=111))
    assert a == b


def test_trade_corpus_differs_for_different_seed():
    a = dump_json_bytes(build_trade_corpus(seed=111))
    b = dump_json_bytes(build_trade_corpus(seed=222))
    assert a != b


def test_market_scenarios_are_deterministic():
    a = dump_json_bytes(build_market_scenarios(seed=555))
    b = dump_json_bytes(build_market_scenarios(seed=555))
    assert a == b


def test_inventory_corpus_is_deterministic():
    a = dump_json_bytes(build_inventory_corpus(seed=777))
    b = dump_json_bytes(build_inventory_corpus(seed=777))
    assert a == b


def test_scenario_catalogue_is_deterministic():
    assert dump_json_bytes(SCENARIO_CATALOGUE) == dump_json_bytes(SCENARIO_CATALOGUE)


def test_independent_reference_manifest_is_deterministic():
    trades = build_trade_corpus().trades
    a = compute_source_control_manifest(trades, manifest_id="SIM-MANIFEST-TEST")
    b = compute_source_control_manifest(trades, manifest_id="SIM-MANIFEST-TEST")
    assert dump_json_bytes(a) == dump_json_bytes(b)


def test_adversarial_index_is_deterministic():
    kwargs = dict(
        agreement_bundles=build_agreement_bundles(),
        trade_corpus=build_trade_corpus(),
        market_snapshots=build_market_scenarios(),
        inventory_positions=build_inventory_corpus(),
    )
    a = build_adversarial_index(**kwargs)
    b = build_adversarial_index(**kwargs)
    assert dump_json_bytes(a.cases) == dump_json_bytes(b.cases)


def test_full_generation_round_trip_is_byte_identical(tmp_path):
    from simulation_factory.corpus.generate_all import generate_all

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    generate_all(out1)
    generate_all(out2)

    files1 = sorted(p.relative_to(out1) for p in out1.rglob("*") if p.is_file())
    files2 = sorted(p.relative_to(out2) for p in out2.rglob("*") if p.is_file())
    assert files1 == files2

    for rel in files1:
        assert (out1 / rel).read_bytes() == (out2 / rel).read_bytes(), f"{rel} differs across runs"

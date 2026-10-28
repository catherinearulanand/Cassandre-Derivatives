"""Corpus size targets per plan Section 9 / the work item spec: 300 trades,
600 inventory positions, 8-12 agreement bundles with 4-6 calculable, 30
market snapshots, 6-10 users."""

from __future__ import annotations

from simulation_factory.corpus.agreements import build_agreement_bundles
from simulation_factory.corpus.inventory import build_inventory_corpus
from simulation_factory.corpus.market import build_market_scenarios
from simulation_factory.corpus.trades import build_trade_corpus
from simulation_factory.corpus.users import build_static_data_corpus


def test_trade_count_is_exactly_300():
    assert len(build_trade_corpus().trades) == 300


def test_inventory_count_is_exactly_600():
    assert len(build_inventory_corpus()) == 600


def test_agreement_bundle_count_is_8_to_12_with_4_to_6_calculable():
    bundles = build_agreement_bundles()
    assert 8 <= len(bundles) <= 12
    calculable = [b for b in bundles if b.riskos_calculable]
    assert 4 <= len(calculable) <= 6


def test_market_snapshot_count_is_30_with_correct_regime_split():
    snapshots = build_market_scenarios()
    assert len(snapshots) == 30
    counts: dict[str, int] = {}
    for s in snapshots:
        counts[s.regime] = counts.get(s.regime, 0) + 1
    assert counts == {
        "QUIET": 10,
        "RATE_SHOCK": 10,
        "FX_STRESS": 5,
        "COMBINED_LIQUIDITY_STRESS": 5,
    }


def test_user_count_is_6_to_10_and_covers_all_16_roles():
    from simulation_factory.corpus.users import ALL_ROLES

    corpus = build_static_data_corpus()
    assert 6 <= len(corpus.users) <= 10
    covered = {role for user in corpus.users for role in user.roles}
    assert covered == set(ALL_ROLES)

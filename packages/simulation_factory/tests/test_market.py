"""SIM-005: market scenario corpus invariants."""

from __future__ import annotations

from simulation_factory.corpus.market import FX_PAIRS, build_market_scenarios


def test_every_snapshot_has_freshness_policy_and_utc_timezone():
    for snapshot in build_market_scenarios():
        assert snapshot.timezone == "UTC"
        assert snapshot.source_label == "SIMULATED"
        assert snapshot.freshness_policy.max_age_hours == 24


def test_at_least_one_stale_missing_and_crossed_negative_case_each():
    snapshots = build_market_scenarios()
    assert any(s.negative_case.is_stale for s in snapshots)
    assert any(s.negative_case.has_missing_quote for s in snapshots)
    assert any(s.negative_case.has_crossed_market for s in snapshots)


def test_stale_snapshots_have_stale_after_before_as_of():
    for snapshot in build_market_scenarios():
        if snapshot.negative_case.is_stale:
            assert snapshot.freshness_policy.stale_after < snapshot.as_of


def test_missing_quote_snapshots_omit_one_declared_fx_pair():
    for snapshot in build_market_scenarios():
        if snapshot.negative_case.has_missing_quote:
            pairs_present = {(q.source_currency, q.target_currency) for q in snapshot.fx_quotes}
            assert pairs_present < set(FX_PAIRS)


def test_discount_factors_cover_every_declared_tenor():
    from simulation_factory.corpus.market import TENORS

    for snapshot in build_market_scenarios():
        tenors_present = {d.tenor for d in snapshot.discount_factors}
        assert tenors_present == set(TENORS)

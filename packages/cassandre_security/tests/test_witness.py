from __future__ import annotations

import datetime as dt

import pytest

from cassandre_security.witness import (
    WitnessForkError,
    WitnessFreshnessError,
    WitnessGapError,
    check_freshness,
    latest_checkpoint,
    submit_checkpoint,
)

NOW = dt.datetime(2026, 1, 5, 12, 0, 0, tzinfo=dt.timezone.utc)
CHAIN = "clauseos"


def test_first_checkpoint_must_start_at_sequence_zero(sqlite_session):
    submit_checkpoint(
        sqlite_session,
        product_chain_id=CHAIN,
        sequence=0,
        head_hash="hash-0",
        release_manifest_digest="rel-1",
        trust_bundle_version="tb-1",
        now=NOW,
    )
    record = latest_checkpoint(sqlite_session, product_chain_id=CHAIN)
    assert record is not None
    assert record.sequence == 0


def test_sequential_checkpoints_extend_the_chain(sqlite_session):
    submit_checkpoint(sqlite_session, product_chain_id=CHAIN, sequence=0, head_hash="h0", release_manifest_digest="r", trust_bundle_version="t", now=NOW)
    submit_checkpoint(sqlite_session, product_chain_id=CHAIN, sequence=1, head_hash="h1", release_manifest_digest="r", trust_bundle_version="t", now=NOW)
    record = latest_checkpoint(sqlite_session, product_chain_id=CHAIN)
    assert record.sequence == 1
    assert record.head_hash == "h1"


def test_gap_in_sequence_rejected(sqlite_session):
    submit_checkpoint(sqlite_session, product_chain_id=CHAIN, sequence=0, head_hash="h0", release_manifest_digest="r", trust_bundle_version="t", now=NOW)
    with pytest.raises(WitnessGapError):
        submit_checkpoint(sqlite_session, product_chain_id=CHAIN, sequence=5, head_hash="h5", release_manifest_digest="r", trust_bundle_version="t", now=NOW)


def test_fork_attempt_different_head_hash_for_same_sequence_rejected(sqlite_session):
    submit_checkpoint(sqlite_session, product_chain_id=CHAIN, sequence=0, head_hash="h0", release_manifest_digest="r", trust_bundle_version="t", now=NOW)
    with pytest.raises(WitnessForkError):
        submit_checkpoint(sqlite_session, product_chain_id=CHAIN, sequence=0, head_hash="DIFFERENT-h0", release_manifest_digest="r", trust_bundle_version="t", now=NOW)


def test_exact_replay_of_same_checkpoint_is_idempotent_not_an_error(sqlite_session):
    submit_checkpoint(sqlite_session, product_chain_id=CHAIN, sequence=0, head_hash="h0", release_manifest_digest="r", trust_bundle_version="t", now=NOW)
    record = submit_checkpoint(sqlite_session, product_chain_id=CHAIN, sequence=0, head_hash="h0", release_manifest_digest="r", trust_bundle_version="t", now=NOW)
    assert record.sequence == 0
    assert record.head_hash == "h0"


def test_freshness_check_passes_within_bound(sqlite_session):
    submit_checkpoint(sqlite_session, product_chain_id=CHAIN, sequence=0, head_hash="h0", release_manifest_digest="r", trust_bundle_version="t", now=NOW)
    check_freshness(sqlite_session, product_chain_id=CHAIN, now=NOW + dt.timedelta(seconds=30), max_lag=dt.timedelta(minutes=1))


def test_freshness_check_blocks_when_stale(sqlite_session):
    submit_checkpoint(sqlite_session, product_chain_id=CHAIN, sequence=0, head_hash="h0", release_manifest_digest="r", trust_bundle_version="t", now=NOW)
    with pytest.raises(WitnessFreshnessError):
        check_freshness(sqlite_session, product_chain_id=CHAIN, now=NOW + dt.timedelta(hours=2), max_lag=dt.timedelta(minutes=1))


def test_freshness_check_blocks_when_no_checkpoint_exists_yet(sqlite_session):
    with pytest.raises(WitnessFreshnessError):
        check_freshness(sqlite_session, product_chain_id="never-witnessed-chain", now=NOW, max_lag=dt.timedelta(minutes=1))


def test_two_different_product_chains_are_independent(sqlite_session):
    submit_checkpoint(sqlite_session, product_chain_id="clauseos", sequence=0, head_hash="h0", release_manifest_digest="r", trust_bundle_version="t", now=NOW)
    submit_checkpoint(sqlite_session, product_chain_id="riskos", sequence=0, head_hash="different-h0", release_manifest_digest="r", trust_bundle_version="t", now=NOW)
    assert latest_checkpoint(sqlite_session, product_chain_id="clauseos").head_hash == "h0"
    assert latest_checkpoint(sqlite_session, product_chain_id="riskos").head_hash == "different-h0"

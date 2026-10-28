"""SIM-003: agreement corpus coverage, calculable-family enforcement, and
ground-truth content-hash provenance."""

from __future__ import annotations

from audit_protocol.canonical import content_hash

from simulation_factory.corpus.agreements import (
    CALCULABLE_FAMILY,
    build_agreement_bundles,
)
from simulation_factory.serialization import to_jsonable


def test_every_section_9_2_coverage_item_is_present():
    expected_items = {
        "straightforward threshold/MTA",
        "zero threshold",
        "asymmetric bilateral thresholds",
        "ratings-contingent threshold with missing current state",
        "currency-specific MTA",
        "MTA-before-rounding",
        "rounding-before-MTA where expressly elected",
        "amendment overriding eligible collateral",
        "side letter changing notification deadline",
        "conflicting clauses requiring review",
        "missing incorporated document causing BUNDLE_INCOMPLETE",
        "wrong-way issuer exclusion and concentration rules",
    }
    bundles = build_agreement_bundles()
    actual_items = {b.coverage_item for b in bundles}
    assert actual_items == expected_items


def test_calculable_bundles_share_one_family():
    bundles = build_agreement_bundles()
    calculable = [b for b in bundles if b.riskos_calculable]
    assert all(b.family == CALCULABLE_FAMILY for b in calculable)
    non_calculable = [b for b in bundles if not b.riskos_calculable]
    assert all(b.family is None for b in non_calculable)


def test_ground_truth_content_hash_is_verifiable_and_tamper_evident():
    bundles = build_agreement_bundles()
    for bundle in bundles:
        recomputed = content_hash(to_jsonable(bundle.ground_truth))
        assert recomputed == bundle.ground_truth_content_hash, (
            f"{bundle.bundle_id}: ground truth content hash does not match its own payload"
        )


def test_ground_truth_authored_independently_of_any_extractor():
    """Ground truth is a plain dataclass literal in `agreements.py`; this
    test asserts the module never *imports* a ClauseOS extraction/parsing
    module (there is none in this package, but the guard documents the
    invariant and would fail loudly if that ever changed). It checks actual
    imports via the AST, not a text search, since the module's own
    docstrings legitimately discuss ClauseOS in prose."""

    import ast
    import inspect

    import simulation_factory.corpus.agreements as agreements_module

    tree = ast.parse(inspect.getsource(agreements_module))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module.split(".")[0])
    assert "clauseos" not in imported_names


def test_missing_incorporated_document_bundle_has_no_resolvable_terms():
    bundles = build_agreement_bundles()
    bundle = next(b for b in bundles if b.ground_truth.missing_incorporated_document)
    gt = bundle.ground_truth
    assert gt.threshold_calculation_party is None
    assert gt.threshold_counterparty is None
    assert gt.expected_bundle_status == "BUNDLE_INCOMPLETE"


def test_conflicting_clauses_bundle_has_no_defaulted_threshold():
    bundles = build_agreement_bundles()
    bundle = next(b for b in bundles if b.ground_truth.has_conflicting_clauses)
    gt = bundle.ground_truth
    assert gt.threshold_calculation_party is None
    assert gt.threshold_counterparty is None
    assert gt.expected_bundle_status == "REVIEW_REQUIRED"


def test_ratings_contingent_bundle_leaves_counterparty_threshold_unknown():
    bundles = build_agreement_bundles()
    bundle = next(
        b for b in bundles if b.ground_truth.rating_contingent_threshold and not b.ground_truth.rating_state_known
    )
    assert bundle.ground_truth.threshold_counterparty is None
    assert bundle.riskos_calculable is False


def test_bundle_ids_are_unique():
    bundles = build_agreement_bundles()
    ids = [b.bundle_id for b in bundles]
    assert len(ids) == len(set(ids))

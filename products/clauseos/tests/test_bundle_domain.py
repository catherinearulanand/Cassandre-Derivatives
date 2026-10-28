import datetime as dt

import pytest

from clauseos.domain.bundle import AgreementBundle, DocumentNode
from clauseos.domain.errors import BundleIncompleteError, InvalidTransitionError, ManifestNotApprovedError
from clauseos.domain.status import BundleStatus

from helpers import ANNEX_DOCUMENT_NAME, CSA_DOCUMENT_NAME, annex_bytes, build_manifest, csa_bytes
from clauseos.core.normalization import normalize_text, sha256_hex, sha256_hex_of_text


def _make_document(bundle_id: str, name: str, raw: bytes) -> DocumentNode:
    normalized = normalize_text(raw.decode("utf-8"))
    return DocumentNode(
        document_id=f"doc-{name}",
        bundle_id=bundle_id,
        document_name=name,
        raw_bytes_sha256=sha256_hex(raw),
        normalized_text=normalized,
        normalized_text_sha256=sha256_hex_of_text(normalized),
    )


def _new_bundle() -> AgreementBundle:
    return AgreementBundle(
        bundle_id="bundle-1",
        scenario_id="SIM-SCEN-CLAUSEOS-0001",
        status=BundleStatus.CREATED,
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    )


def test_full_bundle_document_and_annex_reaches_parsed():
    bundle = _new_bundle()
    bundle.add_document(_make_document(bundle.bundle_id, CSA_DOCUMENT_NAME, csa_bytes()))
    bundle.add_document(_make_document(bundle.bundle_id, ANNEX_DOCUMENT_NAME, annex_bytes()))
    assert bundle.status is BundleStatus.INGESTING

    bundle.approve_manifest(build_manifest(incorporate_annex=True))
    assert bundle.manifest_approved

    bundle.check_completeness()
    assert bundle.status is BundleStatus.PARSED

    bundle.mark_analyzed()
    assert bundle.status is BundleStatus.ANALYZED
    bundle.require_review()
    assert bundle.status is BundleStatus.REVIEW_REQUIRED


def test_missing_incorporated_document_produces_bundle_incomplete():
    """Adversarial fixture per plan Section 9.2 item 11 / Section 10.2: a
    manifest that declares an incorporated-by-reference document not present
    in the bundle must fail closed into BUNDLE_INCOMPLETE, not proceed."""
    bundle = _new_bundle()
    bundle.add_document(_make_document(bundle.bundle_id, CSA_DOCUMENT_NAME, csa_bytes()))
    # Deliberately do NOT add the "Confirmation Annex A" document, even
    # though the manifest below declares it incorporated by reference.
    bundle.approve_manifest(build_manifest(incorporate_annex=True))

    with pytest.raises(BundleIncompleteError):
        bundle.check_completeness()

    assert bundle.status is BundleStatus.BUNDLE_INCOMPLETE
    assert bundle.incompleteness_reason is not None
    assert ANNEX_DOCUMENT_NAME in bundle.incompleteness_reason


def test_bundle_incomplete_is_not_publishable_and_blocks_further_transitions():
    bundle = _new_bundle()
    bundle.add_document(_make_document(bundle.bundle_id, CSA_DOCUMENT_NAME, csa_bytes()))
    bundle.approve_manifest(build_manifest(incorporate_annex=True))
    with pytest.raises(BundleIncompleteError):
        bundle.check_completeness()

    with pytest.raises(InvalidTransitionError):
        bundle.mark_analyzed()


def test_analyze_before_manifest_approved_raises():
    bundle = _new_bundle()
    bundle.add_document(_make_document(bundle.bundle_id, CSA_DOCUMENT_NAME, csa_bytes()))
    with pytest.raises(ManifestNotApprovedError):
        bundle.check_completeness()


def test_invalid_transition_is_rejected_not_silently_ignored():
    bundle = _new_bundle()
    with pytest.raises(InvalidTransitionError):
        bundle.mark_analyzed()
    with pytest.raises(InvalidTransitionError):
        bundle.mark_published()

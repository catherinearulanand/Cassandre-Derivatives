"""Gap 1: PDF/OCR ingestion pipeline (plan Section 10.3).

Covers, using synthetic PDFs built byte-by-byte in `helpers.py` (no
external fixture file, no third-party PDF-authoring dependency):

- successful born-digital ingestion end-to-end through
  `core.pdf_ingestion` directly, AND through `BundleService.ingest_document`
  -> `analyze_bundle` (the rule proposer finds the same material terms in a
  PDF-sourced document as it does in the plain-text/Markdown fixtures);
- rejection of a corrupted/malformed PDF (`BundleStatus.FAILED`), both at
  the `core.pdf_ingestion` layer and as a raised
  `DocumentIngestionRejectedError` through the service layer;
- the OCR-unavailable graceful-degradation path for an image-only PDF:
  `shutil.which("tesseract")` genuinely returns `None` on this machine (no
  monkeypatching needed to prove the real behavior here), and the pipeline
  must return the non-publishable `OCR_UNCERTAIN` status rather than
  crashing or silently treating the page as if it had real text. A second
  test monkeypatches `shutil.which` to simulate the *other* machine state
  (tesseract present) purely to prove the code path still never treats OCR
  output as authoritative, without depending on tesseract actually being
  installed anywhere.
"""

from __future__ import annotations

import pytest

from clauseos.adapters.memory import InMemoryBundleRepository
from clauseos.application.bundle_service import BundleService
from clauseos.core.pdf_ingestion import (
    DocumentMimeType,
    detect_mime_type,
    ingest_pdf_bytes,
)
from clauseos.domain.errors import DocumentIngestionRejectedError
from clauseos.domain.status import AssertionStatus, BundleStatus, MaterialTermConcept

from helpers import (
    build_born_digital_pdf_bytes,
    build_corrupted_pdf_bytes,
    build_image_only_pdf_bytes,
    build_manifest,
)

MAKER = "maker.alice@sim.cassandre.test"
SCENARIO_ID = "SIM-SCEN-CLAUSEOS-0001"

CSA_PDF_LINES = (
    "SYNTHETIC CREDIT SUPPORT ANNEX (BORN-DIGITAL PDF)",
    "The Threshold shall be USD 500,000.00.",
    "The Minimum Transfer Amount shall be USD 50,000.00.",
    "Each party shall notify the other party within 2 Business Days.",
)


# --------------------------------------------------------- core.pdf_ingestion


def test_mime_detection_distinguishes_pdf_and_plain_text():
    pdf_bytes = build_born_digital_pdf_bytes(CSA_PDF_LINES)
    assert detect_mime_type(pdf_bytes) is DocumentMimeType.PDF
    assert detect_mime_type(b"plain old text, no magic header here") is DocumentMimeType.PLAIN_TEXT
    assert detect_mime_type(b"\xff\xfe\x00\x01not text and not a pdf") is DocumentMimeType.UNKNOWN


def test_born_digital_pdf_extracts_layout_aware_text():
    pdf_bytes = build_born_digital_pdf_bytes(CSA_PDF_LINES)
    result = ingest_pdf_bytes(pdf_bytes)
    assert result.ok, result.reason
    assert result.status is None
    assert result.page_count == 1
    for line in CSA_PDF_LINES:
        assert line in result.normalized_text
    assert len(result.page_offsets) == 1
    assert result.page_offsets[0].page_number == 1


def test_corrupted_pdf_is_rejected_as_failed_not_crashed():
    result = ingest_pdf_bytes(build_corrupted_pdf_bytes())
    assert not result.ok
    assert result.status is BundleStatus.FAILED
    assert result.normalized_text is None
    assert result.reason


def test_oversized_pdf_is_rejected_as_unsupported():
    from clauseos.core import pdf_ingestion

    huge = b"%PDF-1.4\n" + b"0" * (pdf_ingestion.MAX_PDF_SIZE_BYTES + 1)
    result = ingest_pdf_bytes(huge)
    assert not result.ok
    assert result.status is BundleStatus.UNSUPPORTED_DOCUMENT
    assert "exceeds" in result.reason


def test_image_only_pdf_is_quarantined_ocr_uncertain_when_tesseract_absent():
    # No monkeypatching: `tesseract` genuinely is not installed on this
    # machine, so this proves the real graceful-degradation behavior, not
    # a simulated one.
    import shutil

    assert shutil.which("tesseract") is None, "expected tesseract to be absent on this machine"

    result = ingest_pdf_bytes(build_image_only_pdf_bytes())
    assert not result.ok
    assert result.status is BundleStatus.OCR_UNCERTAIN
    assert result.normalized_text is None, "OCR-quarantined text must never become authoritative"
    assert result.ocr_quarantine_text is None
    assert "tesseract" in result.reason.lower()


def test_image_only_pdf_ocr_output_stays_quarantined_even_if_tesseract_present(monkeypatch):
    """Simulates a machine where tesseract *is* installed, purely to prove
    that even successful OCR text is never promoted to `normalized_text` --
    it only ever lands in `ocr_quarantine_text`, and the status is still the
    non-publishable OCR_UNCERTAIN. Does not require tesseract to actually
    be installed: pytesseract's call is monkeypatched too, since exercising
    a real OCR engine is out of scope for this machine."""
    import clauseos.core.pdf_ingestion as pdf_ingestion_module

    monkeypatch.setattr(pdf_ingestion_module.shutil, "which", lambda name: "/usr/bin/tesseract")

    class _FakePytesseract:
        @staticmethod
        def image_to_string(image):
            return "OCR'd text, never authoritative"

    import sys

    monkeypatch.setitem(sys.modules, "pytesseract", _FakePytesseract())

    result = ingest_pdf_bytes(build_image_only_pdf_bytes())
    assert not result.ok
    assert result.status is BundleStatus.OCR_UNCERTAIN
    assert result.normalized_text is None
    assert result.ocr_quarantine_text == "OCR'd text, never authoritative"
    assert "quarantined" in result.reason.lower()


# ------------------------------------------------- BundleService integration


def _service() -> BundleService:
    return BundleService(InMemoryBundleRepository())


def test_born_digital_pdf_end_to_end_into_bundle_and_evidence_pipeline():
    service = _service()
    bundle = service.create_bundle(scenario_id=SCENARIO_ID, actor_id=MAKER)
    service.ingest_document(
        bundle_id=bundle.bundle_id,
        document_name="CSA",
        raw_bytes=build_born_digital_pdf_bytes(CSA_PDF_LINES),
        actor_id=MAKER,
    )
    service.approve_manifest(
        bundle_id=bundle.bundle_id, manifest=build_manifest(incorporate_annex=False), actor_id=MAKER
    )
    assert service.get_bundle(bundle.bundle_id).status is BundleStatus.PARSED

    assertions = service.analyze_bundle(bundle_id=bundle.bundle_id, actor_id=MAKER)
    concepts = {a.concept for a in assertions}
    assert concepts == {
        MaterialTermConcept.THRESHOLD,
        MaterialTermConcept.MTA,
        MaterialTermConcept.NOTIFICATION_DEADLINE,
    }
    assert all(a.status is AssertionStatus.HUMAN_REVIEW_REQUIRED for a in assertions)

    threshold = next(a for a in assertions if a.concept is MaterialTermConcept.THRESHOLD)
    assert threshold.candidate.proposed_value.money.currency == "USD"
    assert str(threshold.candidate.proposed_value.money.amount) == "500000.00"


def test_corrupted_pdf_ingestion_raises_and_does_not_add_document():
    service = _service()
    bundle = service.create_bundle(scenario_id=SCENARIO_ID, actor_id=MAKER)
    with pytest.raises(DocumentIngestionRejectedError) as exc_info:
        service.ingest_document(
            bundle_id=bundle.bundle_id,
            document_name="CSA",
            raw_bytes=build_corrupted_pdf_bytes(),
            actor_id=MAKER,
        )
    assert exc_info.value.status is BundleStatus.FAILED
    # Fail closed: the bundle must not silently gain a document from a
    # rejected ingestion attempt.
    assert service.get_bundle(bundle.bundle_id).documents == {}


def test_image_only_pdf_ingestion_raises_ocr_uncertain_through_service():
    service = _service()
    bundle = service.create_bundle(scenario_id=SCENARIO_ID, actor_id=MAKER)
    with pytest.raises(DocumentIngestionRejectedError) as exc_info:
        service.ingest_document(
            bundle_id=bundle.bundle_id,
            document_name="Scanned Annex",
            raw_bytes=build_image_only_pdf_bytes(),
            actor_id=MAKER,
        )
    assert exc_info.value.status is BundleStatus.OCR_UNCERTAIN
    assert service.get_bundle(bundle.bundle_id).documents == {}


def test_unknown_binary_content_is_rejected_as_unsupported_document():
    service = _service()
    bundle = service.create_bundle(scenario_id=SCENARIO_ID, actor_id=MAKER)
    with pytest.raises(DocumentIngestionRejectedError) as exc_info:
        service.ingest_document(
            bundle_id=bundle.bundle_id,
            document_name="mystery.bin",
            raw_bytes=b"\xff\xfe\x00\x01 not a pdf, not utf-8 text",
            actor_id=MAKER,
        )
    assert exc_info.value.status is BundleStatus.UNSUPPORTED_DOCUMENT

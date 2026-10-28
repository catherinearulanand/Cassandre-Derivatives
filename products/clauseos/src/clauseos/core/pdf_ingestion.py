"""PDF/OCR ingestion pipeline (plan Section 10.3).

This closes one of the three explicitly-deferred gaps from the initial
slice (see product README, "Not attempted this session"). It adds:

1. MIME/magic detection (`detect_mime_type`) -- explicit checks, not a
   best-effort guess, per Section 10.3 item 1 ("Validate MIME signature,
   size, page/object limits, encryption, attachments, and malformed
   content").
2. Born-digital PDF extraction (`ingest_pdf_bytes`) using `pypdf` for
   structural validation (encryption, page count, malformed-file detection)
   and `pdfplumber` for layout-aware text extraction -- the plan is explicit
   that "`pypdf` alone is not a sufficient layout-evidence engine" (Section
   10.3), because `pypdf`'s text extraction does not reconstruct reading
   order from word/line bounding boxes the way `pdfplumber` does. The
   resulting per-page text is joined and run through the same
   `core.normalization.normalize_text` used by the plain-text/Markdown path,
   so `EvidenceSpan` offsets behave identically regardless of source format.
3. OCR quarantine: an image-only page (no extractable text layer) is never
   promoted to authoritative text (Section 10.3 item 6: "Reject image-only
   scans from authoritative processing; optional OCR output is quarantined
   and visibly marked"). This module checks `shutil.which("tesseract")`
   before ever calling `pytesseract`; if the binary is absent (true on this
   development machine), it returns the non-publishable `OCR_UNCERTAIN`
   status with an explicit reason rather than crashing or silently treating
   the (nonexistent) OCR text as authoritative. If `tesseract` *is* present,
   any OCR text obtained is still returned only in `ocr_quarantine_text`,
   never as `normalized_text` -- OCR output is never authoritative for this
   product, matching Section 10.3's "quarantined and visibly marked".

No I/O beyond parsing the bytes handed in; no clock, no network. Pure
function of `(raw_bytes,)` other than the one environment probe
(`shutil.which`), which is itself deterministic for a given machine.
"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass
from enum import Enum

from clauseos.core.normalization import normalize_text, sha256_hex, sha256_hex_of_text
from clauseos.domain.status import BundleStatus

# Plan Section 10.3 item 1: explicit size/page limits, not best-effort.
MAX_PDF_SIZE_BYTES = 25 * 1024 * 1024  # 25 MiB
MAX_PDF_PAGES = 200

_PDF_MAGIC = b"%PDF-"


class DocumentMimeType(str, Enum):
    PLAIN_TEXT = "text/plain"
    PDF = "application/pdf"
    UNKNOWN = "application/octet-stream"


def detect_mime_type(raw_bytes: bytes) -> DocumentMimeType:
    """Explicit magic-byte sniffing, not filename/extension trust.

    A PDF file, born-digital or scanned, always begins with the literal
    `%PDF-` header (optionally preceded by a small amount of junk, which we
    do not tolerate here -- a document whose first bytes are not the PDF
    magic is not treated as a PDF, matching Section 10.3's "Validate both
    MIME declaration and file magic"). Anything that decodes cleanly as
    UTF-8 text is treated as the existing plain-text/Markdown path.
    Anything else is UNKNOWN and rejected by the caller.
    """
    if raw_bytes.startswith(_PDF_MAGIC):
        return DocumentMimeType.PDF
    try:
        raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return DocumentMimeType.UNKNOWN
    return DocumentMimeType.PLAIN_TEXT


@dataclass(frozen=True)
class PageOffset:
    """Maps a half-open character range in the joined normalized text back
    to the PDF page (1-indexed) and that page's bounding box, so a future
    UI can render layout-aware evidence even though `EvidenceSpan` itself
    (plan Section 10.1) only stores character offsets."""

    page_number: int
    start_offset: int
    end_offset: int
    page_bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class PdfIngestionResult:
    """Outcome of attempting to ingest one PDF's bytes.

    `status is None` means the extraction succeeded and `normalized_text`
    is authoritative and safe to hand to the proposers. Any non-`None`
    status is one of the existing non-publishable `BundleStatus` members
    (`FAILED`, `UNSUPPORTED_DOCUMENT`, `OCR_UNCERTAIN`) -- reused rather
    than inventing new enum members, per ADR-014's closed-enum discipline
    -- and `normalized_text` is always `None` in that case: a rejected or
    quarantined document must never silently become authoritative text.
    """

    status: BundleStatus | None
    reason: str | None
    raw_bytes_sha256: str
    page_count: int | None = None
    normalized_text: str | None = None
    normalized_text_sha256: str | None = None
    page_offsets: tuple[PageOffset, ...] = ()
    ocr_quarantine_text: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is None


def ingest_pdf_bytes(raw_bytes: bytes) -> PdfIngestionResult:
    raw_hash = sha256_hex(raw_bytes)

    if len(raw_bytes) > MAX_PDF_SIZE_BYTES:
        return PdfIngestionResult(
            status=BundleStatus.UNSUPPORTED_DOCUMENT,
            reason=(
                f"PDF size {len(raw_bytes)} bytes exceeds the "
                f"{MAX_PDF_SIZE_BYTES}-byte authoritative-ingestion limit"
            ),
            raw_bytes_sha256=raw_hash,
        )

    if not raw_bytes.startswith(_PDF_MAGIC):
        return PdfIngestionResult(
            status=BundleStatus.UNSUPPORTED_DOCUMENT,
            reason="file does not begin with the %PDF- magic header",
            raw_bytes_sha256=raw_hash,
        )

    # pypdf: structural validation (malformed/corrupted file, encryption,
    # page count). This intentionally happens before any pdfplumber call so
    # a corrupted file is reported clearly rather than however pdfplumber
    # happens to fail on it.
    import pypdf
    from pypdf.errors import PdfReadError

    try:
        reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
        page_count = len(reader.pages)
        is_encrypted = reader.is_encrypted
    except (PdfReadError, ValueError, OSError) as exc:
        return PdfIngestionResult(
            status=BundleStatus.FAILED,
            reason=f"malformed or corrupted PDF: {exc}",
            raw_bytes_sha256=raw_hash,
        )

    if is_encrypted:
        return PdfIngestionResult(
            status=BundleStatus.UNSUPPORTED_DOCUMENT,
            reason="encrypted PDFs are not supported for authoritative ingestion",
            raw_bytes_sha256=raw_hash,
        )

    if page_count == 0:
        return PdfIngestionResult(
            status=BundleStatus.FAILED,
            reason="PDF contains zero pages",
            raw_bytes_sha256=raw_hash,
        )

    if page_count > MAX_PDF_PAGES:
        return PdfIngestionResult(
            status=BundleStatus.UNSUPPORTED_DOCUMENT,
            reason=f"PDF has {page_count} pages, exceeding the {MAX_PDF_PAGES}-page limit",
            raw_bytes_sha256=raw_hash,
        )

    # pdfplumber: layout-aware text extraction. Section 10.3: "pypdf alone
    # is not a sufficient layout-evidence engine. Use a pinned layout-aware
    # extractor" -- pdfplumber reconstructs reading order from word/line
    # bounding boxes rather than raw content-stream operator order.
    import pdfplumber

    try:
        page_texts: list[str] = []
        page_offsets: list[PageOffset] = []
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                page_texts.append(text)
    except Exception as exc:  # noqa: BLE001 - any pdfplumber failure is a malformed-PDF signal
        return PdfIngestionResult(
            status=BundleStatus.FAILED,
            reason=f"layout-aware text extraction failed: {exc}",
            raw_bytes_sha256=raw_hash,
        )

    combined_raw = "\n\n".join(page_texts)
    has_text_layer = combined_raw.strip() != ""

    if not has_text_layer:
        # Section 10.3 item 6: "Reject image-only scans from authoritative
        # processing; optional OCR output is quarantined and visibly
        # marked." OCR is attempted (if the binary is present) purely to
        # populate the quarantine field -- it is never treated as
        # authoritative `normalized_text` either way.
        ocr_text, ocr_reason = _attempt_quarantined_ocr(raw_bytes)
        return PdfIngestionResult(
            status=BundleStatus.OCR_UNCERTAIN,
            reason=ocr_reason,
            raw_bytes_sha256=raw_hash,
            page_count=page_count,
            ocr_quarantine_text=ocr_text,
        )

    normalized = normalize_text(combined_raw)

    # Build page offsets against the *normalized* joined text so a caller
    # that wants layout provenance can map an EvidenceSpan's offsets back to
    # a page and its bounding box.
    cursor = 0
    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            page_text = normalize_text(page_texts[index - 1])
            start = cursor
            end = start + len(page_text)
            page_offsets.append(
                PageOffset(
                    page_number=index,
                    start_offset=start,
                    end_offset=end,
                    page_bbox=(0.0, 0.0, float(page.width), float(page.height)),
                )
            )
            # +2 for the "\n\n" join separator inserted between page texts,
            # matching how normalize_text collapsed the combined string.
            cursor = end + 2

    return PdfIngestionResult(
        status=None,
        reason=None,
        raw_bytes_sha256=raw_hash,
        page_count=page_count,
        normalized_text=normalized,
        normalized_text_sha256=sha256_hex_of_text(normalized),
        page_offsets=tuple(page_offsets),
    )


def _attempt_quarantined_ocr(raw_bytes: bytes) -> tuple[str | None, str]:
    """Never returns text that becomes authoritative. If `tesseract` is not
    on PATH (true on this development machine), returns `(None, reason)`
    explaining exactly why, rather than crashing or pretending OCR ran."""
    tesseract_path = shutil.which("tesseract")
    if tesseract_path is None:
        return None, (
            "image-only PDF page(s) detected with no extractable text layer; "
            "OCR was not attempted because the 'tesseract' binary is not "
            "installed on PATH on this machine -- this document is quarantined "
            "as OCR_UNCERTAIN and is not authoritative"
        )

    try:
        import pdfplumber
        import pytesseract

        ocr_pages: list[str] = []
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            for page in pdf.pages:
                image = page.to_image(resolution=150).original
                ocr_pages.append(pytesseract.image_to_string(image))
        ocr_text = "\n\n".join(ocr_pages)
    except Exception as exc:  # noqa: BLE001 - OCR is best-effort and never authoritative
        return None, (
            "image-only PDF page(s) detected; tesseract is installed but OCR "
            f"attempt failed ({exc}); this document is quarantined as "
            "OCR_UNCERTAIN and is not authoritative"
        )

    return ocr_text, (
        "image-only PDF page(s) detected with no extractable text layer; "
        "OCR text was produced via tesseract but is quarantined, not "
        "authoritative, and requires human review before any use"
    )

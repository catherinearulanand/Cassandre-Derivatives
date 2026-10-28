from __future__ import annotations

import datetime as dt
from pathlib import Path

from clauseos.domain.bundle import BundleManifest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# --- Synthetic PDF fixtures (Gap 1: PDF/OCR ingestion pipeline) -----------
#
# These build tiny, valid PDF files byte-by-byte using the minimal object
# model the PDF spec requires (catalog -> pages -> page -> content stream),
# rather than depending on an external PDF-authoring library or a
# pre-existing binary fixture file. No dependency beyond the standard
# library is needed to *build* these; `pypdf`/`pdfplumber` (added for the
# production ingestion pipeline) are what *read* them back in the tests.


def _assemble_pdf(object_bodies: dict[int, bytes]) -> bytes:
    """Assemble a minimal, structurally valid single-revision PDF from a
    map of 1-indexed object number -> object body bytes (the part between
    `N 0 obj` and `endobj`). Produces a correct xref table and trailer."""
    buf = bytearray()
    buf += b"%PDF-1.4\n"
    n = len(object_bodies)
    offsets: dict[int, int] = {}
    for i in range(1, n + 1):
        offsets[i] = len(buf)
        buf += f"{i} 0 obj\n".encode("ascii")
        buf += object_bodies[i]
        buf += b"\nendobj\n"
    xref_offset = len(buf)
    buf += f"xref\n0 {n + 1}\n".encode("ascii")
    buf += b"0000000000 65535 f \n"
    for i in range(1, n + 1):
        buf += f"{offsets[i]:010d} 00000 n \n".encode("ascii")
    buf += f"trailer\n<< /Size {n + 1} /Root 1 0 R >>\n".encode("ascii")
    buf += f"startxref\n{xref_offset}\n%%EOF".encode("ascii")
    return bytes(buf)


def _escape_pdf_text(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return escaped.encode("latin-1", errors="replace")


def _content_stream_object(content: bytes, *, extra_dict_entries: str = "") -> bytes:
    return (
        f"<< /Length {len(content)}{extra_dict_entries} >>\nstream\n".encode("ascii")
        + content
        + b"\nendstream"
    )


def build_born_digital_pdf_bytes(lines: tuple[str, ...]) -> bytes:
    """A tiny single-page, born-digital PDF with a real text layer: each
    line of `lines` is drawn with a `Tj` text-showing operator, so
    `pdfplumber`'s layout-aware extraction recovers it as ordinary text."""
    content = bytearray(b"BT /F1 12 Tf 72 720 Td\n")
    for index, line in enumerate(lines):
        if index > 0:
            content += b"0 -18 Td\n"
        content += b"(" + _escape_pdf_text(line) + b") Tj\n"
    content += b"ET\n"

    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R "
            b"/Resources << /Font << /F1 4 0 R >> >> "
            b"/MediaBox [0 0 612 792] /Contents 5 0 R >>"
        ),
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        5: _content_stream_object(bytes(content)),
    }
    return _assemble_pdf(objects)


def build_image_only_pdf_bytes() -> bytes:
    """A tiny single-page PDF containing only a raster image XObject and no
    text-showing operators at all -- standing in for a scanned/image-only
    page, which is the case the ingestion pipeline must never treat as
    authoritative text (plan Section 10.3 item 6)."""
    width, height = 4, 4
    # Uncompressed raw RGB pixel data (all white), no /Filter needed.
    image_data = bytes([255, 255, 255]) * (width * height)

    image_object = (
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
        f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length {len(image_data)} >>\n"
        "stream\n"
    ).encode("ascii") + image_data + b"\nendstream"

    content = b"q 200 0 0 200 100 100 cm /Im0 Do Q"

    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R "
            b"/Resources << /XObject << /Im0 4 0 R >> >> "
            b"/MediaBox [0 0 612 792] /Contents 5 0 R >>"
        ),
        4: image_object,
        5: _content_stream_object(content),
    }
    return _assemble_pdf(objects)


def build_corrupted_pdf_bytes() -> bytes:
    """Bytes that pass the cheap `%PDF-` magic-header sniff but are not a
    parseable PDF at all -- no valid objects, xref, or trailer -- so the
    ingestion pipeline must reject it as malformed rather than crash or
    silently treat it as empty/valid."""
    return b"%PDF-1.4\n" + (b"this is not a valid pdf object stream at all\n" * 20)

CSA_DOCUMENT_NAME = "CSA"
ANNEX_DOCUMENT_NAME = "Confirmation Annex A"


def load_fixture_bytes(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def csa_bytes() -> bytes:
    return load_fixture_bytes("csa_synthetic_001.md")


def annex_bytes() -> bytes:
    return load_fixture_bytes("confirmation_annex_a_synthetic.md")


def build_manifest(*, incorporate_annex: bool = True) -> BundleManifest:
    return BundleManifest(
        document_type="CSA",
        parties=("Cassandre Simulation Bank N.A.", "SIM-COUNTERPARTY-ALPHA"),
        execution_date=dt.date(2026, 1, 15),
        effective_date=dt.date(2026, 1, 15),
        incorporated_by_reference=(ANNEX_DOCUMENT_NAME,) if incorporate_annex else (),
    )

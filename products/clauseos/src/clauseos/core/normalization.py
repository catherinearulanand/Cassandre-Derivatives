"""Deterministic text normalization for plain-text/Markdown ingestion
(plan Section 10.3 -- the PDF/OCR layout-aware pipeline is explicitly
deferred for this slice; see product README). Evidence-span offsets are
defined against the *output* of this function, never against raw bytes, so
normalization must be a pure, total function of the input text.
"""

from __future__ import annotations

import hashlib


def normalize_text(raw_text: str) -> str:
    """Normalize line endings to `\\n` and strip trailing whitespace on each
    line. Leading/trailing blank lines are preserved so offsets computed
    against this output remain stable if the caller re-normalizes."""
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_hex_of_text(text: str) -> str:
    return sha256_hex(text.encode("utf-8"))

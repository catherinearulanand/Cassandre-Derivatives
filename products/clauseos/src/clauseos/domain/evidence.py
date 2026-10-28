"""EvidenceSpan: character-offset evidence into a normalized source
document, per plan Section 10.1/10.4. No PDF page/bounding-box geometry is
implemented in this slice (that requires the deferred layout-aware
extractor of Section 10.3) -- offsets are into the normalized plain-text/
Markdown extraction only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class EvidenceSpan:
    span_id: str
    document_id: str
    start_offset: int
    end_offset: int
    snippet: str
    source_content_hash: str

    def __post_init__(self) -> None:
        if self.start_offset < 0:
            raise ValueError("start_offset must be >= 0")
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        if len(self.snippet) != (self.end_offset - self.start_offset):
            raise ValueError("snippet length must equal end_offset - start_offset")
        if not _SHA256_HEX_RE.match(self.source_content_hash):
            raise ValueError("source_content_hash must be a 64-hex-char sha256 digest")

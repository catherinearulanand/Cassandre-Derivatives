"""Structural verifier (plan Section 10.4): "Deterministic checks verify
exact text/offsets." Pure function, no I/O, no clock -- takes the current
normalized document text and an evidence span and confirms the span's
claimed offsets and content hash actually match that text exactly.
"""

from __future__ import annotations

from dataclasses import dataclass

from clauseos.core.normalization import sha256_hex_of_text
from clauseos.domain.evidence import EvidenceSpan


@dataclass(frozen=True)
class VerificationOutcome:
    ok: bool
    reason: str | None = None


def verify_evidence_span(document_text: str, span: EvidenceSpan) -> VerificationOutcome:
    current_hash = sha256_hex_of_text(document_text)
    if current_hash != span.source_content_hash:
        return VerificationOutcome(
            ok=False,
            reason=(
                f"source_content_hash mismatch: span claims {span.source_content_hash}, "
                f"current document text hashes to {current_hash}"
            ),
        )
    if span.end_offset > len(document_text):
        return VerificationOutcome(
            ok=False,
            reason=(
                f"end_offset {span.end_offset} exceeds document length {len(document_text)}"
            ),
        )
    actual = document_text[span.start_offset : span.end_offset]
    if actual != span.snippet:
        return VerificationOutcome(
            ok=False,
            reason=(
                f"snippet does not match document text at offsets "
                f"[{span.start_offset}:{span.end_offset}]: expected {span.snippet!r}, "
                f"found {actual!r}"
            ),
        )
    return VerificationOutcome(ok=True, reason=None)

from clauseos.core.normalization import normalize_text, sha256_hex_of_text
from clauseos.core.rule_proposer import propose_candidates
from clauseos.core.verification import verify_evidence_span
from clauseos.domain.status import MaterialTermConcept
from clauseos.domain.values import DayCountBasis, MoneyTermValue, NotificationDeadlineValue

from helpers import csa_bytes


def _normalized_csa_text() -> str:
    return normalize_text(csa_bytes().decode("utf-8"))


def test_rule_proposer_extracts_threshold_mta_and_notification_deadline():
    text = _normalized_csa_text()
    candidates = propose_candidates(
        document_id="doc-csa",
        bundle_id="bundle-1",
        normalized_text=text,
        normalized_text_sha256=sha256_hex_of_text(text),
    )
    by_concept = {c.concept: c for c in candidates}
    assert set(by_concept) == {
        MaterialTermConcept.THRESHOLD,
        MaterialTermConcept.MTA,
        MaterialTermConcept.NOTIFICATION_DEADLINE,
    }

    threshold = by_concept[MaterialTermConcept.THRESHOLD]
    assert isinstance(threshold.proposed_value, MoneyTermValue)
    assert str(threshold.proposed_value.money.amount) == "500000.00"
    assert threshold.proposed_value.money.currency == "USD"

    mta = by_concept[MaterialTermConcept.MTA]
    assert isinstance(mta.proposed_value, MoneyTermValue)
    assert str(mta.proposed_value.money.amount) == "50000.00"

    notification = by_concept[MaterialTermConcept.NOTIFICATION_DEADLINE]
    assert isinstance(notification.proposed_value, NotificationDeadlineValue)
    assert notification.proposed_value.days == 2
    assert notification.proposed_value.basis is DayCountBasis.BUSINESS_DAYS


def test_evidence_span_offsets_exactly_match_source_text():
    text = _normalized_csa_text()
    candidates = propose_candidates(
        document_id="doc-csa",
        bundle_id="bundle-1",
        normalized_text=text,
        normalized_text_sha256=sha256_hex_of_text(text),
    )
    for candidate in candidates:
        span = candidate.evidence
        assert text[span.start_offset : span.end_offset] == span.snippet
        outcome = verify_evidence_span(text, span)
        assert outcome.ok, outcome.reason


def test_structural_verifier_detects_tampered_source_text():
    text = _normalized_csa_text()
    candidates = propose_candidates(
        document_id="doc-csa",
        bundle_id="bundle-1",
        normalized_text=text,
        normalized_text_sha256=sha256_hex_of_text(text),
    )
    threshold_span = next(
        c.evidence for c in candidates if c.concept is MaterialTermConcept.THRESHOLD
    )

    tampered_text = text.replace("500,000.00", "999,999.00")
    outcome = verify_evidence_span(tampered_text, threshold_span)
    assert not outcome.ok
    assert "source_content_hash mismatch" in outcome.reason


def test_structural_verifier_detects_offset_snippet_mismatch():
    text = _normalized_csa_text()
    candidates = propose_candidates(
        document_id="doc-csa",
        bundle_id="bundle-1",
        normalized_text=text,
        normalized_text_sha256=sha256_hex_of_text(text),
    )
    threshold_span = next(
        c.evidence for c in candidates if c.concept is MaterialTermConcept.THRESHOLD
    )
    # Same document text (same hash) but a span shifted by one character must
    # be caught as a snippet/offset mismatch, not silently accepted.
    from dataclasses import replace

    shifted = replace(
        threshold_span,
        start_offset=threshold_span.start_offset + 1,
        end_offset=threshold_span.end_offset + 1,
    )
    outcome = verify_evidence_span(text, shifted)
    assert not outcome.ok
    assert "snippet does not match" in outcome.reason

"""Deterministic rule/template proposer (plan Section 10.4, ADR-006): regex
extractors over normalized text for the material terms this slice covers
(Threshold, MTA, Notification Deadline). No model/LLM call anywhere in this
module or this codebase (plan Section 25.3, Decision Register DEC-CLO-01).

Pure function of `(document_id, normalized_text, normalized_text_sha256)` --
no I/O, no clock, no randomness. Candidate IDs are derived deterministically
from the document id, concept, and match offsets so the same input always
produces the same candidate id.
"""

from __future__ import annotations

import re
from decimal import Decimal

from money_types import Money

from clauseos.domain.assertion import AssertionCandidate
from clauseos.domain.evidence import EvidenceSpan
from clauseos.domain.status import MaterialTermConcept
from clauseos.domain.values import DayCountBasis, MoneyTermValue, NotificationDeadlineValue

_THRESHOLD_RE = re.compile(
    r"\bThreshold\b[^.\n]{0,80}?\bshall be\b\s+(?P<ccy>[A-Z]{3})\s+(?P<amt>[\d,]+\.\d{2})",
    re.IGNORECASE,
)
_MTA_RE = re.compile(
    r"\bMinimum Transfer Amount\b[^.\n]{0,80}?\bshall be\b\s+(?P<ccy>[A-Z]{3})\s+(?P<amt>[\d,]+\.\d{2})",
    re.IGNORECASE,
)
_NOTIFICATION_RE = re.compile(
    r"notify the other party within\s+(?P<days>\d+)\s+(?P<basis>Business Days|calendar days)",
    re.IGNORECASE,
)


def _candidate_id(document_id: str, concept: MaterialTermConcept, start: int, end: int) -> str:
    return f"cand-{document_id}-{concept.value}-{start}-{end}"


def propose_candidates(
    *,
    document_id: str,
    bundle_id: str,
    normalized_text: str,
    normalized_text_sha256: str,
) -> list[AssertionCandidate]:
    candidates: list[AssertionCandidate] = []

    threshold_match = _THRESHOLD_RE.search(normalized_text)
    if threshold_match is not None:
        candidates.append(
            _build_money_candidate(
                match=threshold_match,
                concept=MaterialTermConcept.THRESHOLD,
                document_id=document_id,
                bundle_id=bundle_id,
                normalized_text_sha256=normalized_text_sha256,
            )
        )

    mta_match = _MTA_RE.search(normalized_text)
    if mta_match is not None:
        candidates.append(
            _build_money_candidate(
                match=mta_match,
                concept=MaterialTermConcept.MTA,
                document_id=document_id,
                bundle_id=bundle_id,
                normalized_text_sha256=normalized_text_sha256,
            )
        )

    notification_match = _NOTIFICATION_RE.search(normalized_text)
    if notification_match is not None:
        start, end = notification_match.span()
        basis_text = notification_match.group("basis").lower()
        basis = DayCountBasis.BUSINESS_DAYS if "business" in basis_text else DayCountBasis.CALENDAR_DAYS
        candidates.append(
            AssertionCandidate(
                candidate_id=_candidate_id(document_id, MaterialTermConcept.NOTIFICATION_DEADLINE, start, end),
                bundle_id=bundle_id,
                document_id=document_id,
                concept=MaterialTermConcept.NOTIFICATION_DEADLINE,
                proposed_value=NotificationDeadlineValue(
                    days=int(notification_match.group("days")), basis=basis
                ),
                evidence=EvidenceSpan(
                    span_id=f"span-{document_id}-{start}-{end}",
                    document_id=document_id,
                    start_offset=start,
                    end_offset=end,
                    snippet=normalized_text[start:end],
                    source_content_hash=normalized_text_sha256,
                ),
            )
        )

    return candidates


def _build_money_candidate(
    *,
    match: re.Match,
    concept: MaterialTermConcept,
    document_id: str,
    bundle_id: str,
    normalized_text_sha256: str,
) -> AssertionCandidate:
    start, end = match.span()
    amount = str(Decimal(match.group("amt").replace(",", "")))
    return AssertionCandidate(
        candidate_id=_candidate_id(document_id, concept, start, end),
        bundle_id=bundle_id,
        document_id=document_id,
        concept=concept,
        proposed_value=MoneyTermValue(
            money=Money(
                amount=amount,
                currency=match.group("ccy").upper(),
                rounding_context="AS_STATED_IN_AGREEMENT_TEXT",
            )
        ),
        evidence=EvidenceSpan(
            span_id=f"span-{document_id}-{start}-{end}",
            document_id=document_id,
            start_offset=start,
            end_offset=end,
            snippet=match.group(0),
            source_content_hash=normalized_text_sha256,
        ),
    )

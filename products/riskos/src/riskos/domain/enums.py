"""Closed status/classification vocabularies for RiskOS, per plan Section 11
and ADR-014 (fail-closed: every status is an explicit enum member, never an
implicit/default `else` branch standing in for an unhandled case).
"""

from __future__ import annotations

from enum import Enum


class CalculationPurpose(str, Enum):
    """Plan Section 11.2. Each purpose has its own support matrix; this slice
    implements only MARGIN_CALL. The other four are real enum members so an
    unsupported purpose is an explicit rejection, not a silently-ignored
    value."""

    MARGIN_CALL = "MARGIN_CALL"
    VALUATION_CONTROL = "VALUATION_CONTROL"
    CHALLENGER = "CHALLENGER"
    STRESS = "STRESS"
    SENSITIVITY = "SENSITIVITY"


class TradeLifecycleState(str, Enum):
    ACTIVE = "ACTIVE"
    MATURED = "MATURED"
    CANCELLED = "CANCELLED"
    AMENDED = "AMENDED"


class TradePopulationDisposition(str, Enum):
    """Plan Section 11.3 / 9.3: the population-control categories a source
    manifest and RiskOS's own resolution must reconcile before aggregation."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"
    MATURED = "MATURED"
    CANCELLED = "CANCELLED"
    AMENDED = "AMENDED"
    UNSUPPORTED = "UNSUPPORTED"


class BreakType(str, Enum):
    """Plan Section 11.3: "Never force an unexplained difference into a
    plausible category." UNCLASSIFIED is a first-class, honest outcome."""

    TRADE_POPULATION = "TRADE_POPULATION"
    MTM = "MTM"
    MARKET_DATA = "MARKET_DATA"
    TERMS = "TERMS"
    COLLATERAL_STATE = "COLLATERAL_STATE"
    CALL_LOGIC = "CALL_LOGIC"
    TIMING = "TIMING"
    ROUNDING = "ROUNDING"
    UNCLASSIFIED = "UNCLASSIFIED"


class CalculationStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class DataQualityStatus(str, Enum):
    APPROVED = "APPROVED"
    STALE = "STALE"
    INCOMPLETE = "INCOMPLETE"
    OVERRIDDEN = "OVERRIDDEN"


class ReconciliationStatus(str, Enum):
    MATCHED = "MATCHED"
    WITHIN_TOLERANCE = "WITHIN_TOLERANCE"
    BREAK = "BREAK"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class ValidationStatus(str, Enum):
    VALIDATED = "VALIDATED"
    RESTRICTED_USE = "RESTRICTED_USE"
    UNVALIDATED = "UNVALIDATED"


class Actionability(str, Enum):
    SIMULATION_ELIGIBLE = "SIMULATION_ELIGIBLE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INDICATIVE_ONLY = "INDICATIVE_ONLY"
    REJECTED = "REJECTED"


class CallStatus(str, Enum):
    """Direction is always paired with explicit transferor/transferee entity
    ids in `MarginWaterfall` (plan 11.3: "Direction is derived from explicit
    ... identities—not from the sign of a naked exposure."). This enum alone
    is never used to infer who pays whom."""

    DELIVER = "DELIVER"
    RETURN = "RETURN"
    NO_TRANSFER = "NO_TRANSFER"


class AgreementSupportStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED_AGREEMENT = "UNSUPPORTED_AGREEMENT"
    METHOD_UNRESOLVED = "METHOD_UNRESOLVED"


class UndisputedDeterminationStatus(str, Enum):
    DETERMINED = "DETERMINED"
    UNDISPUTED_NOT_DETERMINED = "UNDISPUTED_NOT_DETERMINED"


class DisputeAgreementCase(str, Enum):
    """Plan Section 11.5's six bulleted disagreement cases."""

    BOTH_AGREE_DIRECTION_AND_AMOUNT = "BOTH_AGREE_DIRECTION_AND_AMOUNT"
    AGREE_DIRECTION_DIFFER_AMOUNT = "AGREE_DIRECTION_DIFFER_AMOUNT"
    OPPOSITE_DIRECTION = "OPPOSITE_DIRECTION"
    ONE_PARTY_NOT_RESPONDED = "ONE_PARTY_NOT_RESPONDED"
    PARTIAL_ACKNOWLEDGEMENT = "PARTIAL_ACKNOWLEDGEMENT"
    COLLATERAL_OR_POPULATION_DISAGREEMENT = "COLLATERAL_OR_POPULATION_DISAGREEMENT"


class MarginCallCaseState(str, Enum):
    """Plan Section 11.5 lifecycle."""

    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    RECEIVED = "RECEIVED"
    AGREED = "AGREED"
    PARTIALLY_AGREED = "PARTIALLY_AGREED"
    DISPUTED = "DISPUTED"
    TRANSFER_PENDING = "TRANSFER_PENDING"
    CLOSED = "CLOSED"

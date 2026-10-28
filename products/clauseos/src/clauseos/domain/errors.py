"""Domain error taxonomy for ClauseOS (plan Section 25.3: fail closed, no
silent fallback). Every raised error here corresponds to an explicit,
named failure mode a caller must handle -- never a bare ValueError standing
in for "something about this transition/approval was wrong"."""

from __future__ import annotations

from clauseos.domain.status import BundleStatus


class ClauseOsDomainError(Exception):
    """Base class for every ClauseOS domain-layer error."""


class InvalidTransitionError(ClauseOsDomainError):
    """Raised when a status transition is attempted from a state that does
    not permit it. There is no silent no-op or default-state fallthrough."""


class ManifestNotApprovedError(ClauseOsDomainError):
    """Raised when analysis/publication is attempted before the bundle's
    manifest has been explicitly user-approved (plan Section 10.1)."""


class BundleIncompleteError(ClauseOsDomainError):
    """Raised when a manifest declares an incorporated-by-reference document
    that is not present in the bundle (plan Section 9.2 item 11)."""


class SameActorError(ClauseOsDomainError):
    """Raised when a maker and checker identity are the same for one review
    case (plan Section 10.5: maker and checker must be distinct identities)."""


class StaleReviewError(ClauseOsDomainError):
    """Raised when a review decision is bound to evidence/candidate/release
    state that no longer matches the current state -- compare-and-swap
    failure per plan Section 10.5."""


class IncompletePolicyError(ClauseOsDomainError):
    """Raised when a policy release is attempted without every required
    material term concept in an APPROVED review state."""


class EvidenceVerificationError(ClauseOsDomainError):
    """Raised when an evidence span's offsets/snippet/content hash do not
    match the source document it claims to describe."""


class AuthorizationDeniedError(ClauseOsDomainError):
    """Raised when `cassandre_security.authorization.DenyByDefaultPolicyEngine`
    (wired in `clauseos.application.security`, SEC-04) denies a sensitive
    action before it executes. The API layer maps this to HTTP 403 -- a
    DENY here must actually block the action, never merely be logged."""

    def __init__(self, *, action: str, purpose: str, reason_code: str) -> None:
        self.action = action
        self.purpose = purpose
        self.reason_code = reason_code
        super().__init__(
            f"authorization denied for action={action!r} purpose={purpose!r}: reason_code={reason_code!r}"
        )


class RevocationFenceBlockedError(ClauseOsDomainError):
    """Raised when `cassandre_security.revocation` (SEC-14, wired in
    `clauseos.adapters.db.fence_gateway`) blocks a critical mutation --
    unknown/stale/revoked security-fence state, or a stale presented
    revocation epoch. Wraps the underlying `cassandre_security.revocation.
    FenceError` subtype so ClauseOS's API layer only ever switches on its
    own domain error taxonomy."""

    def __init__(self, *, scope_key: str, reason: str) -> None:
        self.scope_key = scope_key
        super().__init__(f"revocation fence blocked mutation for scope {scope_key!r}: {reason}")


class DocumentIngestionRejectedError(ClauseOsDomainError):
    """Raised when a document cannot be admitted to a bundle as authoritative
    text (plan Section 10.3): malformed/corrupted file, unsupported MIME
    type, size/page limits exceeded, or an image-only scan with no
    extractable text layer. Carries the specific non-publishable
    `BundleStatus` member (`FAILED`, `UNSUPPORTED_DOCUMENT`, or
    `OCR_UNCERTAIN`) the caller should surface, rather than a bare string --
    fail closed, never silently proceed as if ingestion succeeded."""

    def __init__(self, *, status: BundleStatus, reason: str) -> None:
        self.status = status
        self.reason = reason
        super().__init__(f"{status.value}: {reason}")

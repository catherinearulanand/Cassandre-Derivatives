"""Deny-by-default product-local authorization engine (SEC-04).

Implements the `cassandre.authorization.v1` decision contract from the
security architecture spec section 7 / Appendix A. This is a *local*
decision engine each product embeds — it is not a shared online service,
per section 5's "not a mandatory global service that holds every tenant's
keys and can override every product."

An ALLOW returned by `PolicyEngine.decide` is an internal decision record,
not a transferable bearer capability (spec section 7): callers must bind
any resulting executable capability to the receiving workload, exact
operation, resource versions, short expiry, and one-time consumption for
mutations. This module does not implement that capability-issuance step —
that is product-specific and happens at the call site that actually
performs the mutation (see `revocation.py` for the fencing that guards it).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class ActorKind(str, Enum):
    HUMAN = "HUMAN"
    WORKLOAD = "WORKLOAD"


class Outcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class ReasonCode(str, Enum):
    OK = "OK"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    UNKNOWN_PURPOSE = "UNKNOWN_PURPOSE"
    MISSING_SCOPE = "MISSING_SCOPE"
    STALE_POLICY = "STALE_POLICY"
    UNKNOWN_POLICY_DIGEST = "UNKNOWN_POLICY_DIGEST"
    INVALID_PROVENANCE = "INVALID_PROVENANCE"
    NO_GRANT = "NO_GRANT"
    REVOKED = "REVOKED"
    EXPIRED_SESSION = "EXPIRED_SESSION"
    OBJECT_VERSION_MISMATCH = "OBJECT_VERSION_MISMATCH"


@dataclass(frozen=True)
class AuthorizationRequest:
    """Field-for-field the section 7 YAML schema. Every server-resolved
    field must actually be resolved server-side by the caller before this
    object is constructed -- this dataclass does not itself authenticate
    anything; `tenant_id`/`legal_entity_id` must never come from a
    client-supplied header (spec: "Client-supplied tenant IDs never set the
    trusted session scope")."""

    request_id: str
    actor_subject: str  # verified issuer+subject, e.g. "https://idp/realm#sub"
    actor_kind: ActorKind
    tenant_id: str
    legal_entity_id: str | None
    product_id: str
    object_id: str
    object_version: str
    # "sha256:<hex>" or similar algorithm-tagged digest. NOTE: `decide()`
    # binds this into `request_digest` (so a decision cannot be replayed
    # against a request with a different digest) but does NOT itself
    # verify this value against any live object store -- this engine has
    # no object-store dependency by design (spec section 7's decision
    # engine is a typed, deterministic function of its inputs). A caller
    # that needs "the object I'm about to act on is actually the object
    # this digest names" must check that itself (e.g. `envelope.py`'s AAD
    # binding already does this for encrypted objects). Found as a real
    # gap during CollateralOS's integration -- see `COMPONENTS.md`.
    content_digest: str
    action: str
    purpose: str
    policy_release_digest: str
    revocation_epoch: int
    session_assurance: str
    expires_at: dt.datetime


@dataclass(frozen=True)
class AuthorizationDecision:
    outcome: Outcome
    reason_code: ReasonCode
    decision_id: str
    expires_at: dt.datetime
    request_digest: str

    @property
    def allowed(self) -> bool:
        return self.outcome is Outcome.ALLOW


@dataclass(frozen=True)
class PolicyRule:
    """One allowlisted (product_id, action, purpose) combination this
    engine will ever grant. Everything not explicitly listed is denied --
    the deny-by-default core of SEC-04."""

    product_id: str
    action: str
    purpose: str
    required_roles: frozenset[str] = field(default_factory=frozenset)


class RevocationChecker(Protocol):
    def is_current(self, *, tenant_id: str, actor_subject: str, revocation_epoch: int) -> bool:
        """Return True only if `revocation_epoch` is the actor's current,
        non-revoked epoch as of a sufficiently fresh trusted read. Return
        False for unknown, stale, or revoked state -- never default True."""
        ...


class GrantResolver(Protocol):
    def roles_for(self, *, tenant_id: str, actor_subject: str) -> frozenset[str]:
        ...


@dataclass(frozen=True)
class DenyByDefaultPolicyEngine:
    """Deterministic reference implementation. Given the same request,
    policy set, grants, and revocation state, always returns the same
    decision -- no randomness, no wall-clock-only side effects beyond the
    explicit `now` parameter."""

    policy_release_digest: str
    rules: tuple[PolicyRule, ...]
    revocation_checker: RevocationChecker
    grant_resolver: GrantResolver

    def _find_rule(self, request: AuthorizationRequest) -> PolicyRule | None:
        for rule in self.rules:
            if rule.product_id == request.product_id and rule.action == request.action and rule.purpose == request.purpose:
                return rule
        return None

    def decide(self, request: AuthorizationRequest, *, now: dt.datetime, decision_id: str) -> AuthorizationDecision:
        from audit_protocol import content_hash

        request_digest = content_hash(
            {
                "request_id": request.request_id,
                "actor_subject": request.actor_subject,
                "tenant_id": request.tenant_id,
                "product_id": request.product_id,
                "object_id": request.object_id,
                "object_version": request.object_version,
                "action": request.action,
                "purpose": request.purpose,
            }
        )

        def deny(reason: ReasonCode) -> AuthorizationDecision:
            return AuthorizationDecision(
                outcome=Outcome.DENY,
                reason_code=reason,
                decision_id=decision_id,
                expires_at=now,
                request_digest=request_digest,
            )

        if request.policy_release_digest != self.policy_release_digest:
            return deny(ReasonCode.UNKNOWN_POLICY_DIGEST)

        if now >= request.expires_at:
            return deny(ReasonCode.EXPIRED_SESSION)

        rule = self._find_rule(request)
        if rule is None:
            return deny(ReasonCode.UNKNOWN_ACTION)

        if not request.tenant_id or not request.object_id or not request.object_version:
            return deny(ReasonCode.MISSING_SCOPE)

        if not self.revocation_checker.is_current(
            tenant_id=request.tenant_id,
            actor_subject=request.actor_subject,
            revocation_epoch=request.revocation_epoch,
        ):
            return deny(ReasonCode.REVOKED)

        if rule.required_roles:
            actor_roles = self.grant_resolver.roles_for(tenant_id=request.tenant_id, actor_subject=request.actor_subject)
            if not rule.required_roles.issubset(actor_roles):
                return deny(ReasonCode.NO_GRANT)

        # Short-lived: an ALLOW is valid for at most the request's own
        # expiry or a bounded default, whichever is sooner -- never a
        # long-lived reusable grant.
        decision_expiry = min(request.expires_at, now + dt.timedelta(minutes=5))
        return AuthorizationDecision(
            outcome=Outcome.ALLOW,
            reason_code=ReasonCode.OK,
            decision_id=decision_id,
            expires_at=decision_expiry,
            request_digest=request_digest,
        )

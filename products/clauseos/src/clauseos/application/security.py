"""ClauseOS's product-local wiring of `cassandre_security.authorization`
(SEC-04) -- the deny-by-default gate this product's service layer calls
before every sensitive action (ingest document, propose assertion via
analyze, maker/checker review decisions, publish policy release, export
evidence).

This is deliberately a thin, ClauseOS-specific adapter over the shared
`cassandre_security` package, not a fork of its logic: the deny-by-default
engine, request/decision schema, and fail-closed behavior all come from
`cassandre_security.authorization.DenyByDefaultPolicyEngine` unmodified.

Known S1 limitations of this wiring (disclosed, not hidden):

- ClauseOS has no OIDC/Keycloak identity/session integration yet (product
  README's already-disclosed ADR-009 gap). There is therefore no real
  actor-session-revocation source of truth for
  `cassandre_security.authorization.RevocationChecker` to consult, and no
  role/entitlement directory for `GrantResolver` to consult. Both defaults
  below are documented, honest placeholders for that missing
  infrastructure -- NOT a silent weakening of the engine itself, which
  still enforces deny-by-default action allowlisting, exact scope
  completeness, policy-digest freshness, and session expiry against every
  request. A real deployment plugs its IdP in here without touching any
  call site in `bundle_service.py`.
- This is distinct from `cassandre_security.revocation`'s per-object
  security-fence epoch, which ClauseOS *does* enforce against real
  Postgres state for critical mutations (see
  `clauseos.adapters.db.fence_gateway`) -- that mechanism does not depend
  on IdP wiring and is not weakened by the above.
- ClauseOS has no multi-tenant concept yet; `tenant_id` is set to the
  bundle's `scenario_id`, the closest existing scoping concept in this
  synthetic single-tenant deployment.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from cassandre_security.authorization import (
    ActorKind,
    AuthorizationDecision,
    AuthorizationRequest,
    DenyByDefaultPolicyEngine,
    GrantResolver,
    Outcome,
    PolicyRule,
    RevocationChecker,
)

from clauseos.domain.errors import AuthorizationDeniedError

PRODUCT_ID = "clauseos"

# Fixed per-deployment policy-release digest this engine is configured
# against. Never client-supplied -- `authorization.py`'s own engine already
# refuses any request whose `policy_release_digest` does not match this
# value (`ReasonCode.UNKNOWN_POLICY_DIGEST`).
POLICY_RELEASE_DIGEST = "sha256:clauseos-authz-policy-v1"

ACTION_INGEST_DOCUMENT = "INGEST_DOCUMENT"
ACTION_ANALYZE_BUNDLE = "ANALYZE_BUNDLE"
ACTION_RECORD_MAKER_DECISION = "RECORD_MAKER_DECISION"
ACTION_RECORD_CHECKER_DECISION = "RECORD_CHECKER_DECISION"
ACTION_PUBLISH_POLICY_RELEASE = "PUBLISH_POLICY_RELEASE"
ACTION_EXPORT_EVIDENCE_BUNDLE = "EXPORT_EVIDENCE_BUNDLE"

PURPOSE_DOCUMENT_INGESTION = "DOCUMENT_INGESTION"
PURPOSE_PROPOSE_ASSERTION = "PROPOSE_ASSERTION"
PURPOSE_MAKER_REVIEW_DECISION = "MAKER_REVIEW_DECISION"
PURPOSE_CHECKER_REVIEW_DECISION = "CHECKER_REVIEW_DECISION"
PURPOSE_POLICY_RELEASE_PUBLICATION = "POLICY_RELEASE_PUBLICATION"
PURPOSE_EVIDENCE_EXPORT = "EVIDENCE_EXPORT"


class _AlwaysCurrentRevocationChecker:
    """S1 default -- see module docstring: no actor-session-revocation
    source of truth exists yet for ClauseOS (no IdP wiring). Always
    reports the presented epoch as current. Tests that need to prove a
    DENY genuinely blocks an action inject a real (denying) checker
    instead of this default -- see `test_security_integration.py`."""

    def is_current(self, *, tenant_id: str, actor_subject: str, revocation_epoch: int) -> bool:
        return True


class _NoRequiredRolesGrantResolver:
    """S1 default -- see module docstring: no role/entitlement directory
    exists yet for ClauseOS actors. Every `PolicyRule` below is registered
    with an empty `required_roles`, so this resolver is never actually
    consulted for a role-gated decision; it exists only to satisfy the
    `GrantResolver` protocol until a real entitlement source is wired in."""

    def roles_for(self, *, tenant_id: str, actor_subject: str) -> frozenset[str]:
        return frozenset()


def default_rules() -> tuple[PolicyRule, ...]:
    """The complete allowlist this engine will ever grant for ClauseOS.
    Everything else -- any other (action, purpose) pair -- is denied by
    the engine's own deny-by-default core, with no code path in this
    product to bypass that."""
    return tuple(
        PolicyRule(product_id=PRODUCT_ID, action=action, purpose=purpose)
        for action, purpose in (
            (ACTION_INGEST_DOCUMENT, PURPOSE_DOCUMENT_INGESTION),
            (ACTION_ANALYZE_BUNDLE, PURPOSE_PROPOSE_ASSERTION),
            (ACTION_RECORD_MAKER_DECISION, PURPOSE_MAKER_REVIEW_DECISION),
            (ACTION_RECORD_CHECKER_DECISION, PURPOSE_CHECKER_REVIEW_DECISION),
            (ACTION_PUBLISH_POLICY_RELEASE, PURPOSE_POLICY_RELEASE_PUBLICATION),
            (ACTION_EXPORT_EVIDENCE_BUNDLE, PURPOSE_EVIDENCE_EXPORT),
        )
    )


@dataclass
class ClauseOsAuthorizationService:
    """ClauseOS's embedded, product-local `DenyByDefaultPolicyEngine`
    instance (spec section 5: each product embeds its own local decision
    engine; this is not a shared network authorization service). One
    instance is owned by `BundleService` (and, by extension, the FastAPI
    app that constructs it)."""

    engine: DenyByDefaultPolicyEngine

    @classmethod
    def build_default(
        cls,
        *,
        revocation_checker: RevocationChecker | None = None,
        grant_resolver: GrantResolver | None = None,
        policy_release_digest: str = POLICY_RELEASE_DIGEST,
        rules: tuple[PolicyRule, ...] | None = None,
    ) -> "ClauseOsAuthorizationService":
        return cls(
            engine=DenyByDefaultPolicyEngine(
                policy_release_digest=policy_release_digest,
                rules=rules if rules is not None else default_rules(),
                revocation_checker=revocation_checker or _AlwaysCurrentRevocationChecker(),
                grant_resolver=grant_resolver or _NoRequiredRolesGrantResolver(),
            )
        )

    def authorize(
        self,
        *,
        action: str,
        purpose: str,
        actor_id: str,
        tenant_id: str,
        object_id: str,
        object_version: str,
        content_digest: str,
        legal_entity_id: str | None = None,
        revocation_epoch: int = 0,
        now: dt.datetime,
    ) -> AuthorizationDecision:
        """Build the request, call `.decide(...)`, and raise
        `AuthorizationDeniedError` on anything but ALLOW. Callers in
        `bundle_service.py` call this BEFORE the corresponding mutation
        executes -- a DENY here must actually prevent the action, not just
        be logged."""
        request = AuthorizationRequest(
            request_id=str(uuid.uuid4()),
            actor_subject=actor_id,
            actor_kind=ActorKind.HUMAN,
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            product_id=PRODUCT_ID,
            object_id=object_id,
            object_version=object_version,
            content_digest=content_digest,
            action=action,
            purpose=purpose,
            policy_release_digest=self.engine.policy_release_digest,
            revocation_epoch=revocation_epoch,
            session_assurance="sim-actor-assurance",  # no OIDC/MFA wiring yet (ADR-009 gap)
            expires_at=now + dt.timedelta(minutes=10),
        )
        decision = self.engine.decide(request, now=now, decision_id=str(uuid.uuid4()))
        if decision.outcome is not Outcome.ALLOW:
            raise AuthorizationDeniedError(action=action, purpose=purpose, reason_code=decision.reason_code.value)
        return decision

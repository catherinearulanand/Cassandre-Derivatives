"""RiskOS security integration ("plug it in" pass, this session).

Wires the shared `cassandre_security` package's foundational P0 slice
(SEC-04 authorization, SEC-14 revocation fencing, SEC-10 canonical evidence
v2, SEC-15 witness) into RiskOS's own runtime. This module owns every
RiskOS-local naming/allowlist/actor-shape decision; `cassandre_security`
itself is a read-only shared dependency this pass never modifies (other
agents are integrating it into ClauseOS/CollateralOS concurrently this
session -- see `packages/cassandre_security/COMPONENTS.md`).

SEC-04 (authorization): `build_default_policy_engine` returns a
deny-by-default `DenyByDefaultPolicyEngine` allowlisting exactly the six
`RiskOSService` actions this pass gates with a `.decide(...)` call before
the action executes: register_legal_entity, approve_agreement_terms,
run_calculation, submit_reconciliation, export_result_package,
run_challenger_valuation. RiskOS has no OIDC/session-identity wiring yet
(product README's "Explicitly deferred" section, unchanged by this pass);
`RiskOSActorContext` is the same kind of fixed simulation-constant identity
`RiskOSService._actor_id` already uses for audit events, not a real
authenticated session. `DEFAULT_ACTOR` is pre-granted every role this pass
defines, so every existing caller that does not pass an explicit `actor=`
(the API, every `riskos_fixtures`-based test) keeps behaving exactly as
before -- a caller that wants to *prove* a DENY constructs a different
`RiskOSActorContext` explicitly (e.g. one with no granted roles, or one
`StaticRevocationChecker.revoke()`-marked as revoked).

Note `run_calculation`'s authorization purpose is the caller's own
`CalculationPurpose` value (MARGIN_CALL/CHALLENGER/etc), not hard-coded to
MARGIN_CALL: authorization ("is this actor allowed to attempt this action
for this purpose") is deliberately orthogonal to "is this purpose
implemented" (that remains `riskos.core.gates`'s job -- see
`apps/riskos_api/tests/test_api.py::
test_challenger_purpose_is_still_rejected_by_the_margin_call_endpoint`,
unchanged by this pass). Hard-coding the PolicyRule to MARGIN_CALL alone
would turn that already-handled "unsupported purpose" business outcome
into an authorization DENY instead, changing that test's observed
behavior.

SEC-14 (revocation fencing): `fence_scope_key` is this product's
`scope_key` encoding for `cassandre_security.revocation`'s fence rows:
`product=riskos;tenant=<tenant_id>;netting_set=<netting_set_id>`. Only
`riskos.adapters.sql_repository.SqlAlchemyRiskOSRepository` implements the
`lock_fences_for_mutation`/`ensure_fence_exists` hooks `RiskOSService`
looks for via `getattr` before calling them -- the in-memory repository
used by the API and most tests has no fence table at all, so fencing is
simply not exercised on that path, consistent with this codebase's
existing rule that domain/core/application tests never require a live
database (see `products/riskos/tests/conftest.py`).

SEC-10 (canonical evidence v2): the constants below name the new, purely
additive `ChallengerRun` evidence capability (see
`RiskOSService.seal_challenger_run_evidence`/`verify_challenger_run_evidence`
in `riskos.application.service`) -- this never touches the existing V1
`audit_protocol`/`cassandre_contracts` signed `Envelope` path
`export_result_package` still uses for the authoritative MARGIN_CALL
result package.

SEC-15 (witness): `WITNESS_PRODUCT_CHAIN_ID` etc name the checkpoint chain
`RiskOSService.run_calculation` submits to after saving each
`CalculationRun`, again only when the repository is fence/witness-capable
(SQL-backed).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from cassandre_security.authorization import (
    ActorKind,
    AuthorizationDecision,
    DenyByDefaultPolicyEngine,
    GrantResolver,
    PolicyRule,
    RevocationChecker,
)

PRODUCT_ID = "riskos"

# RiskOS has no real multi-tenant identity model in this slice (see product
# README: "actor_id/issuer are fixed simulation constants, not derived from
# a request identity"). One fixed simulation tenant is used everywhere a
# tenant_id is required -- by both the authorization default actor and the
# revocation-fence scope key.
DEFAULT_TENANT_ID = "SIM-TENANT-RISKOS-DEFAULT"

POLICY_RELEASE_DIGEST = "sha256:riskos-authorization-policy-v1-provisional"

# -- SEC-04: actions/purposes this pass gates -------------------------------
ACTION_REGISTER_LEGAL_ENTITY = "REGISTER_LEGAL_ENTITY"
PURPOSE_MASTER_DATA_MANAGEMENT = "MASTER_DATA_MANAGEMENT"

ACTION_APPROVE_AGREEMENT_TERMS = "APPROVE_AGREEMENT_TERMS"
PURPOSE_AGREEMENT_APPROVAL = "AGREEMENT_APPROVAL"

ACTION_RUN_CALCULATION = "RUN_CALCULATION"
# One PolicyRule per `CalculationPurpose` member -- see module docstring for
# why this is not narrowed to MARGIN_CALL alone.
RUN_CALCULATION_PURPOSES: tuple[str, ...] = (
    "MARGIN_CALL",
    "VALUATION_CONTROL",
    "CHALLENGER",
    "STRESS",
    "SENSITIVITY",
)

ACTION_SUBMIT_RECONCILIATION = "SUBMIT_RECONCILIATION"
PURPOSE_RECONCILIATION = "RECONCILIATION"

ACTION_EXPORT_RESULT_PACKAGE = "EXPORT_RESULT_PACKAGE"
PURPOSE_EVIDENCE_EXPORT = "EVIDENCE_EXPORT"

ACTION_RUN_CHALLENGER_VALUATION = "RUN_CHALLENGER_VALUATION"
PURPOSE_CHALLENGER = "CHALLENGER"

ROLE_MASTER_DATA_WRITE = "riskos:master_data:write"
ROLE_AGREEMENT_APPROVE = "riskos:agreement:approve"
ROLE_CALCULATION_RUN = "riskos:calculation:run"
ROLE_RECONCILIATION_SUBMIT = "riskos:reconciliation:submit"
ROLE_EXPORT_RESULT_PACKAGE = "riskos:export:result_package"
ROLE_CHALLENGER_RUN = "riskos:challenger:run"

ALL_ROLES: frozenset[str] = frozenset(
    {
        ROLE_MASTER_DATA_WRITE,
        ROLE_AGREEMENT_APPROVE,
        ROLE_CALCULATION_RUN,
        ROLE_RECONCILIATION_SUBMIT,
        ROLE_EXPORT_RESULT_PACKAGE,
        ROLE_CHALLENGER_RUN,
    }
)


class RiskOSAuthorizationDeniedError(PermissionError):
    """Raised by `RiskOSService` when
    `cassandre_security.authorization.DenyByDefaultPolicyEngine.decide`
    returns a DENY. A real, blocking exception -- never a logged warning --
    per this pass's brief: SEC-04 must not be weakened to make a caller's
    life easier. `apps/riskos_api`'s routes catch this and map it to HTTP
    403."""

    def __init__(self, *, action: str, purpose: str, decision: AuthorizationDecision):
        self.action = action
        self.purpose = purpose
        self.decision = decision
        super().__init__(
            f"authorization denied for action={action!r} purpose={purpose!r}: "
            f"reason_code={decision.reason_code.value}"
        )


class RiskOSRevocationFenceError(RuntimeError):
    """Wraps a `cassandre_security.revocation.FenceError` (SEC-14) raised
    while guarding a critical mutation. `apps/riskos_api` does not itself
    depend on `cassandre_security` (only `products/riskos` does, per this
    pass's brief); wrapping here lets that app catch one RiskOS-local
    exception type and map it to an HTTP status without importing the
    shared package directly. The original `cassandre_security` exception is
    preserved as `__cause__` (via `raise ... from cause`) -- never
    swallowed."""

    def __init__(self, *, scope_key: str, cause: Exception):
        self.scope_key = scope_key
        super().__init__(f"revocation fence blocked a critical mutation for scope {scope_key!r}: {cause}")


@dataclass(frozen=True)
class RiskOSActorContext:
    """The RiskOS-local stand-in for a verified request identity. Spec:
    `AuthorizationRequest.tenant_id`/`actor_subject` must be server-resolved,
    never a client-supplied header -- RiskOS has no real session/identity
    layer yet (see module docstring), so this is constructed directly by a
    caller (a test, or a future request-identity adapter), never parsed
    from request input."""

    actor_subject: str
    tenant_id: str = DEFAULT_TENANT_ID
    legal_entity_id: str | None = None
    actor_kind: ActorKind = ActorKind.WORKLOAD
    revocation_epoch: int = 0
    session_assurance: str = "workload-identity-riskos-service"
    session_ttl: dt.timedelta = dt.timedelta(minutes=15)


DEFAULT_ACTOR = RiskOSActorContext(actor_subject="SIM-ACTOR-RISKOS-SERVICE")


@dataclass
class StaticGrantResolver:
    """In-memory `cassandre_security.authorization.GrantResolver`. RiskOS
    has no identity-provider role integration yet (product README's
    "Explicitly deferred" section) -- this is a simulation-only stand-in a
    product or test registers grants against directly."""

    _grants: dict[tuple[str, str], frozenset[str]] = field(default_factory=dict)

    def grant(self, *, tenant_id: str, actor_subject: str, roles: frozenset[str]) -> None:
        self._grants[(tenant_id, actor_subject)] = roles

    def roles_for(self, *, tenant_id: str, actor_subject: str) -> frozenset[str]:
        return self._grants.get((tenant_id, actor_subject), frozenset())


@dataclass
class StaticRevocationChecker:
    """In-memory `cassandre_security.authorization.RevocationChecker`.
    Fails closed for any subject explicitly marked revoked; otherwise
    treats a session as current -- RiskOS has no real revocation-event
    source yet (see module docstring). Never defaults an *unknown* subject
    to revoked either way this checker is used only for actors this same
    process granted roles to."""

    _revoked: set[tuple[str, str]] = field(default_factory=set)

    def revoke(self, *, tenant_id: str, actor_subject: str) -> None:
        self._revoked.add((tenant_id, actor_subject))

    def is_current(self, *, tenant_id: str, actor_subject: str, revocation_epoch: int) -> bool:
        return (tenant_id, actor_subject) not in self._revoked


def build_default_policy_engine(
    *, grant_resolver: GrantResolver, revocation_checker: RevocationChecker
) -> DenyByDefaultPolicyEngine:
    rules: list[PolicyRule] = [
        PolicyRule(
            product_id=PRODUCT_ID,
            action=ACTION_REGISTER_LEGAL_ENTITY,
            purpose=PURPOSE_MASTER_DATA_MANAGEMENT,
            required_roles=frozenset({ROLE_MASTER_DATA_WRITE}),
        ),
        PolicyRule(
            product_id=PRODUCT_ID,
            action=ACTION_APPROVE_AGREEMENT_TERMS,
            purpose=PURPOSE_AGREEMENT_APPROVAL,
            required_roles=frozenset({ROLE_AGREEMENT_APPROVE}),
        ),
        PolicyRule(
            product_id=PRODUCT_ID,
            action=ACTION_SUBMIT_RECONCILIATION,
            purpose=PURPOSE_RECONCILIATION,
            required_roles=frozenset({ROLE_RECONCILIATION_SUBMIT}),
        ),
        PolicyRule(
            product_id=PRODUCT_ID,
            action=ACTION_EXPORT_RESULT_PACKAGE,
            purpose=PURPOSE_EVIDENCE_EXPORT,
            required_roles=frozenset({ROLE_EXPORT_RESULT_PACKAGE}),
        ),
        PolicyRule(
            product_id=PRODUCT_ID,
            action=ACTION_RUN_CHALLENGER_VALUATION,
            purpose=PURPOSE_CHALLENGER,
            required_roles=frozenset({ROLE_CHALLENGER_RUN}),
        ),
    ]
    rules.extend(
        PolicyRule(
            product_id=PRODUCT_ID,
            action=ACTION_RUN_CALCULATION,
            purpose=purpose,
            required_roles=frozenset({ROLE_CALCULATION_RUN}),
        )
        for purpose in RUN_CALCULATION_PURPOSES
    )
    return DenyByDefaultPolicyEngine(
        policy_release_digest=POLICY_RELEASE_DIGEST,
        rules=tuple(rules),
        revocation_checker=revocation_checker,
        grant_resolver=grant_resolver,
    )


def build_default_grant_resolver() -> StaticGrantResolver:
    """The default actor is pre-granted every role this pass defines, so
    every existing caller that never passes `actor=` sees no behavior
    change from before this integration."""

    resolver = StaticGrantResolver()
    resolver.grant(tenant_id=DEFAULT_ACTOR.tenant_id, actor_subject=DEFAULT_ACTOR.actor_subject, roles=ALL_ROLES)
    return resolver


def fence_scope_key(netting_set_id: str, *, tenant_id: str = DEFAULT_TENANT_ID) -> str:
    """SEC-14 fence scope key for a RiskOS netting set, per this pass's
    brief: `product=riskos;tenant=<tenant_id>;netting_set=<netting_set_id>`."""

    return f"product={PRODUCT_ID};tenant={tenant_id};netting_set={netting_set_id}"


# -- SEC-10: canonical evidence v2 for the additive RSK-X01 challenger ------
CHALLENGER_EVIDENCE_TENANT_ID = DEFAULT_TENANT_ID
CHALLENGER_EVIDENCE_PURPOSE = "CHALLENGER_RUN_COMPARISON_EVIDENCE"
CHALLENGER_EVIDENCE_OBJECT_VERSION = "v1"
CHALLENGER_EVIDENCE_DEFAULT_VALIDITY = dt.timedelta(days=3650)  # simulation-only; no real retention policy this pass

# -- SEC-15: independent witness chain id -----------------------------------
WITNESS_PRODUCT_CHAIN_ID = "riskos"
WITNESS_RELEASE_MANIFEST_DIGEST = "sha256:riskos-release-manifest-provisional"
WITNESS_TRUST_BUNDLE_VERSION = "riskos-trust-bundle-v1-provisional"

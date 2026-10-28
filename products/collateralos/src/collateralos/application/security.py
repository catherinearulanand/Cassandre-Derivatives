"""CollateralOS wiring of `cassandre_security` into this product's own
application layer -- the "plug it in" integration work, not new security
primitives (those live in `packages/cassandre_security`, which this module
only calls).

Three `cassandre_security` components are wired here:

* SEC-04 (`cassandre_security.authorization`): a deny-by-default
  `DenyByDefaultPolicyEngine` with one `PolicyRule` per CollateralOS
  mutating action. `authorize(...)` is called by
  `application/store.py::CollateralOSStore` and
  `adapters/sql_store.py::SqlAlchemyCollateralOSStore` at the top of every
  mutating method (obligation/policy/snapshot ingest, plan creation,
  reservation, maker/checker approval, export) and raises
  `AuthorizationDeniedError` -- never just logs -- on any DENY. The API
  layer (`apps/collateralos_api`) maps that to HTTP 403.

* SEC-14 (`cassandre_security.revocation`): `fence_scope_key` and
  `peek_fence_epoch` are the CollateralOS-specific helpers
  `adapters/sql_store.py` uses around `ensure_fence_exists` /
  `lock_fences_for_mutation` for `reserve_plan` and `export_internal`. See
  that module for why this is wired only into the real Postgres-backed
  store, not the in-memory one (revocation fencing is fundamentally a
  transactional-row-locking guarantee the in-memory store cannot honestly
  provide).

CollateralOS has no multi-tenant or RBAC model of its own yet (single
product, single simulated deployment, plain `SIM-USER-*`/`SIM-*` actor
IDs -- see the product README): `TENANT_ID` below is a fixed constant
rather than something resolved per request, and every `PolicyRule` declares
`required_roles=frozenset()` (deny-by-default is enforced purely on
action/purpose/policy-digest/expiry/revocation, not roles, this pass). This
is a recorded simplification, not a silent weakening -- see the product
README's "Security integration" section.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from audit_protocol import content_hash
from cassandre_security.authorization import (
    ActorKind,
    AuthorizationRequest,
    DenyByDefaultPolicyEngine,
    GrantResolver,
    PolicyRule,
    ReasonCode,
    RevocationChecker,
)
from cassandre_security.canonical_evidence import (
    EvidenceContainer,
    build_protected_body,
    seal_evidence,
)
from cassandre_security.revocation import SecurityFenceRow

TENANT_ID = "SIM-TENANT-COLLATERALOS"  # single-tenant product this pass (see module docstring)
PRODUCT_ID = "collateralos"
POLICY_RELEASE_DIGEST = "collateralos-security-policy-v1"

# --- action/purpose vocabulary (one PolicyRule per store mutation) --------

ACTION_ADD_OBLIGATION = "ADD_OBLIGATION"
ACTION_ACTIVATE_POLICY = "ACTIVATE_POLICY"
ACTION_ADD_SNAPSHOT = "ADD_SNAPSHOT"
ACTION_CREATE_PLAN = "CREATE_PLAN"
ACTION_RESERVE_PLAN = "RESERVE_PLAN"
ACTION_MAKER_APPROVE = "MAKER_APPROVE"
ACTION_CHECKER_APPROVE = "CHECKER_APPROVE"
ACTION_EXPORT_INTERNAL = "EXPORT_INTERNAL"

_PURPOSE_BY_ACTION: dict[str, str] = {
    ACTION_ADD_OBLIGATION: "OBLIGATION_INTAKE",
    ACTION_ACTIVATE_POLICY: "POLICY_ACTIVATION",
    ACTION_ADD_SNAPSHOT: "INVENTORY_INGEST",
    ACTION_CREATE_PLAN: "PLAN_CREATION",
    ACTION_RESERVE_PLAN: "RESERVATION_COMMIT",
    ACTION_MAKER_APPROVE: "MAKER_APPROVAL",
    ACTION_CHECKER_APPROVE: "CHECKER_APPROVAL",
    # Reuses the exact purpose string `store.py`/`sql_store.py` already put
    # in the exported envelope's own `purpose_constraints` -- one name for
    # the same real-world purpose, not two.
    ACTION_EXPORT_INTERNAL: "COLLATERAL_INSTRUCTION_EXPORT",
}


class AuthorizationDeniedError(Exception):
    """Raised by `authorize()` on any DENY outcome from the policy engine.
    This is the exception the API layer (`apps/collateralos_api`) maps to
    HTTP 403 -- a DENY must actually stop the mutation, never just log a
    warning and continue."""

    def __init__(self, action: str, reason_code: ReasonCode):
        self.action = action
        self.reason_code = reason_code
        super().__init__(f"authorization denied for action {action!r}: {reason_code.value}")


@dataclass
class InMemoryRevocationRegistry:
    """Reference `RevocationChecker` (SEC-04's pluggable revocation check).

    CollateralOS has no identity-provider integration yet (the Keycloak
    realm exists per this session's Docker work, but nothing in this
    product is wired to it -- an explicit residual `THREAT_REGISTER.md`
    already records for the whole `cassandre_security` package). This is a
    simple in-process revoked-actor set an operator or test can populate;
    it never defaults an unknown actor to "revoked" (that would be its own
    kind of fail-open bug) and never defaults a revoked actor back to
    "current" except via an explicit `unrevoke` call.
    """

    _revoked: set[str] = field(default_factory=set)

    def revoke(self, actor_subject: str) -> None:
        self._revoked.add(actor_subject)

    def unrevoke(self, actor_subject: str) -> None:
        self._revoked.discard(actor_subject)

    def is_current(self, *, tenant_id: str, actor_subject: str, revocation_epoch: int) -> bool:
        return actor_subject not in self._revoked


@dataclass(frozen=True)
class _NoRolesGrantResolver:
    """CollateralOS has no RBAC/role-grant system of its own yet, so every
    `PolicyRule` below declares `required_roles=frozenset()` and the engine
    never actually calls `roles_for` (it only does so when a rule declares
    required roles). Supplied only to satisfy the `GrantResolver` protocol,
    so a future product change adding real roles only has to change the
    rules below, not this wiring module."""

    def roles_for(self, *, tenant_id: str, actor_subject: str) -> frozenset[str]:
        return frozenset()


def build_policy_engine(
    *,
    revocation_checker: RevocationChecker | None = None,
    grant_resolver: GrantResolver | None = None,
) -> DenyByDefaultPolicyEngine:
    """One `DenyByDefaultPolicyEngine` for CollateralOS: exactly the eight
    actions named in the security-integration brief are allowlisted;
    everything else (an unknown action, a mismatched purpose, a stale
    policy digest, an expired session, a revoked actor) is denied by the
    engine's own deny-by-default core -- this function does not add any
    additional allow path."""
    rules = tuple(
        PolicyRule(product_id=PRODUCT_ID, action=action, purpose=purpose, required_roles=frozenset())
        for action, purpose in _PURPOSE_BY_ACTION.items()
    )
    return DenyByDefaultPolicyEngine(
        policy_release_digest=POLICY_RELEASE_DIGEST,
        rules=rules,
        revocation_checker=revocation_checker or InMemoryRevocationRegistry(),
        grant_resolver=grant_resolver or _NoRolesGrantResolver(),
    )


def _actor_kind(actor_id: str) -> ActorKind:
    # Mirrors `CollateralOSStore._record_audit`'s own actor_type inference.
    return ActorKind.HUMAN if actor_id.startswith("SIM-USER-") else ActorKind.WORKLOAD


def authorize(
    engine: DenyByDefaultPolicyEngine,
    *,
    action: str,
    actor_id: str,
    object_id: str,
    object_version: str,
    now: dt.datetime,
    legal_entity_id: str | None = None,
    revocation_epoch: int = 0,
) -> None:
    """Build the `AuthorizationRequest` for one CollateralOS mutation and
    call `engine.decide(...)`. Raises `AuthorizationDeniedError` on any
    DENY -- callers must not catch-and-ignore this; it is what makes SEC-04
    an actual gate rather than an audit-only observation.
    """
    purpose = _PURPOSE_BY_ACTION[action]
    request = AuthorizationRequest(
        request_id=str(uuid.uuid4()),
        actor_subject=actor_id,
        actor_kind=_actor_kind(actor_id),
        tenant_id=TENANT_ID,
        legal_entity_id=legal_entity_id,
        product_id=PRODUCT_ID,
        object_id=object_id,
        object_version=object_version,
        content_digest=f"sha256:{content_hash({'action': action, 'object_id': object_id, 'object_version': object_version})}",
        action=action,
        purpose=purpose,
        policy_release_digest=engine.policy_release_digest,
        revocation_epoch=revocation_epoch,
        session_assurance="collateralos-internal-actor",
        expires_at=now + dt.timedelta(minutes=5),
    )
    decision = engine.decide(request, now=now, decision_id=str(uuid.uuid4()))
    if not decision.allowed:
        raise AuthorizationDeniedError(action, decision.reason_code)


# --- SEC-14 revocation-fence helpers (SQL store only; see module docstring) -


def fence_scope_key(*, obligation_id: str, tenant_id: str = TENANT_ID) -> str:
    """One fence scope per obligation -- the same granularity
    `reserve_plan`/`export_internal` operate at. `cassandre_security.
    revocation` treats this string as opaque; this is CollateralOS's own
    encoding of it."""
    return f"product=collateralos;tenant={tenant_id};obligation={obligation_id}"


def peek_fence_epoch(session: Session, scope_key: str) -> int | None:
    """A plain (non-locking) read of a fence's current epoch, used as the
    "presented epoch" a mutation carries into
    `cassandre_security.revocation.lock_fences_for_mutation`'s own locked
    re-check. Returns `None` if the scope has never been provisioned
    (`lock_fences_for_mutation` itself raises `UnknownFenceScopeError` in
    that case, regardless of what presented-epoch value is supplied, so a
    `None` here never needs special-casing by the caller beyond picking any
    placeholder int)."""
    stmt = select(SecurityFenceRow.current_epoch).where(SecurityFenceRow.scope_key == scope_key)
    return session.execute(stmt).scalar_one_or_none()


# --- SEC-10 canonical evidence v2 (optional/additive; see module docstring) -

EXPORT_EVIDENCE_VALIDITY = dt.timedelta(days=3650)  # long-lived evidentiary record, not a short-lived decision


def build_export_evidence_v2(
    *,
    keypair,  # audit_protocol.Ed25519KeyPair -- the same signing identity `cassandre_contracts.sign_envelope` already uses
    instruction_id: str,
    instruction_hash: str,
    now: dt.datetime,
    tenant_id: str = TENANT_ID,
) -> EvidenceContainer:
    """Seals a `cassandre.evidence.v2` (SHA-384, Ed25519) container for one
    export -- ADDITIVE alongside the existing `cassandre_contracts`-signed
    `Envelope` `export_internal` already produces, never a replacement for
    it. Reuses the store's own Ed25519 signing key (the same identity that
    already signs the envelope) rather than provisioning a second key,
    since this pass has no separate evidence-signing key ceremony (SEC-09
    is out of scope, see `COMPONENTS.md`).
    """
    body = build_protected_body(
        payload={"instruction_id": instruction_id, "instruction_hash": instruction_hash},
        tenant_id=tenant_id,
        signer_key_set_id=keypair.key_id,
        object_id=instruction_id,
        object_version=instruction_hash,
        purpose=_PURPOSE_BY_ACTION[ACTION_EXPORT_INTERNAL],
        policy_epoch=0,
        valid_from=now,
        valid_until=now + EXPORT_EVIDENCE_VALIDITY,
    )
    return seal_evidence(body, ed25519_keypair=keypair)

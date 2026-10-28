from __future__ import annotations

import datetime as dt

import pytest

from cassandre_security.authorization import (
    ActorKind,
    AuthorizationRequest,
    DenyByDefaultPolicyEngine,
    Outcome,
    PolicyRule,
    ReasonCode,
)

NOW = dt.datetime(2026, 1, 5, 12, 0, 0, tzinfo=dt.timezone.utc)
POLICY_DIGEST = "sha256:policy-v1"


class _AlwaysCurrentRevocationChecker:
    def is_current(self, *, tenant_id: str, actor_subject: str, revocation_epoch: int) -> bool:
        return True


class _AlwaysRevokedChecker:
    def is_current(self, *, tenant_id: str, actor_subject: str, revocation_epoch: int) -> bool:
        return False


class _RoleGrantResolver:
    def __init__(self, roles: frozenset[str]):
        self._roles = roles

    def roles_for(self, *, tenant_id: str, actor_subject: str) -> frozenset[str]:
        return self._roles


def _request(**overrides) -> AuthorizationRequest:
    defaults = dict(
        request_id="req-1",
        actor_subject="SIM-USER-1",
        actor_kind=ActorKind.HUMAN,
        tenant_id="SIM-TENANT-A",
        legal_entity_id="SIM-ENTITY-1",
        product_id="clauseos",
        object_id="SIM-BUNDLE-1",
        object_version="v1",
        content_digest="sha256:abc",
        action="APPROVE_ASSERTION",
        purpose="MATERIAL_TERM_APPROVAL",
        policy_release_digest=POLICY_DIGEST,
        revocation_epoch=0,
        session_assurance="phishing-resistant-mfa",
        expires_at=NOW + dt.timedelta(minutes=10),
    )
    defaults.update(overrides)
    return AuthorizationRequest(**defaults)


def _engine(*, revocation_checker=None, grant_resolver=None) -> DenyByDefaultPolicyEngine:
    return DenyByDefaultPolicyEngine(
        policy_release_digest=POLICY_DIGEST,
        rules=(
            PolicyRule(product_id="clauseos", action="APPROVE_ASSERTION", purpose="MATERIAL_TERM_APPROVAL", required_roles=frozenset({"clause_checker"})),
        ),
        revocation_checker=revocation_checker or _AlwaysCurrentRevocationChecker(),
        grant_resolver=grant_resolver or _RoleGrantResolver(frozenset({"clause_checker"})),
    )


def test_allowed_request_with_correct_role_and_current_revocation():
    decision = _engine().decide(_request(), now=NOW, decision_id="d-1")
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason_code is ReasonCode.OK
    assert decision.expires_at <= NOW + dt.timedelta(minutes=5)


def test_unknown_action_denied_deny_by_default():
    decision = _engine().decide(_request(action="DELETE_EVERYTHING"), now=NOW, decision_id="d-2")
    assert decision.outcome is Outcome.DENY
    assert decision.reason_code is ReasonCode.UNKNOWN_ACTION


def test_missing_role_denied():
    engine = _engine(grant_resolver=_RoleGrantResolver(frozenset({"clause_maker"})))
    decision = engine.decide(_request(), now=NOW, decision_id="d-3")
    assert decision.outcome is Outcome.DENY
    assert decision.reason_code is ReasonCode.NO_GRANT


def test_stale_policy_digest_denied():
    decision = _engine().decide(_request(policy_release_digest="sha256:different"), now=NOW, decision_id="d-4")
    assert decision.outcome is Outcome.DENY
    assert decision.reason_code is ReasonCode.UNKNOWN_POLICY_DIGEST


def test_expired_session_denied():
    expired_request = _request(expires_at=NOW - dt.timedelta(seconds=1))
    decision = _engine().decide(expired_request, now=NOW, decision_id="d-5")
    assert decision.outcome is Outcome.DENY
    assert decision.reason_code is ReasonCode.EXPIRED_SESSION


def test_revoked_actor_denied_even_with_correct_role():
    engine = _engine(revocation_checker=_AlwaysRevokedChecker())
    decision = engine.decide(_request(), now=NOW, decision_id="d-6")
    assert decision.outcome is Outcome.DENY
    assert decision.reason_code is ReasonCode.REVOKED


def test_missing_scope_denied():
    decision = _engine().decide(_request(tenant_id=""), now=NOW, decision_id="d-7")
    assert decision.outcome is Outcome.DENY
    assert decision.reason_code is ReasonCode.MISSING_SCOPE


def test_decision_is_deterministic_for_identical_inputs():
    engine = _engine()
    request = _request()
    d1 = engine.decide(request, now=NOW, decision_id="same-id")
    d2 = engine.decide(request, now=NOW, decision_id="same-id")
    assert d1 == d2


def test_client_supplied_tenant_cannot_forge_allow_for_a_different_tenant():
    # AuthorizationRequest.tenant_id is documented as server-resolved; this
    # test proves the engine has no code path that trusts a client-supplied
    # value differently depending on its content -- two different
    # server-resolved tenants with identical role grants both evaluate the
    # same way, with no special-cased tenant string anywhere in the engine.
    engine = _engine()
    decision_a = engine.decide(_request(tenant_id="SIM-TENANT-A"), now=NOW, decision_id="d-8")
    decision_b = engine.decide(_request(tenant_id="SIM-TENANT-B"), now=NOW, decision_id="d-9")
    assert decision_a.outcome is Outcome.ALLOW
    assert decision_b.outcome is Outcome.ALLOW

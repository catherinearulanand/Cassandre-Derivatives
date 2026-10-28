"""ClauseOS's DB-backed wiring of `cassandre_security.revocation` (SEC-14)
-- the security-fence/epoch mechanism resolving Appendix A blocker
JPMC-FINAL-01 -- into ClauseOS's critical-mutation paths (publish policy
release, checker approval).

Reuses `cassandre_security.revocation`'s `SecurityFenceRow` model and
`ensure_fence_exists`/`lock_fences_for_mutation`/`advance_revocation`
functions unmodified; this module only adds ClauseOS's own scope-key
convention and a thin gateway that opens a session against ClauseOS's own
Postgres database (the same one `clauseos_db_engine`/`CASSANDRE_TEST_DATABASE_URL`
already points at in tests -- no separate database or connection is
introduced).

Known limitation (disclosed, not hidden): `cassandre_security.revocation`'s
own docstring specifies that a caller should hold the fence row lock
through its OWN atomic domain/audit/outbox commit, in one transaction.
ClauseOS's existing `SqlAlchemyBundleRepository` does not expose a
cross-call unit of work -- each of its `save_*` methods opens and commits
its own transaction (see that module's docstring) -- so this gateway
follows the same per-call-commit style: it checks and (when advancing)
mutates the fence in its own short transaction, immediately before the
caller's separate domain-mutation transaction(s). This still correctly
detects and blocks a stale/unknown/revoked fence state before the mutation
proceeds, but does not hold one lock across both; closing that gap fully
would require a broader unit-of-work refactor of the repository layer,
which is out of scope for this integration pass.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from cassandre_security.revocation import (
    EpochMismatchError,
    FenceError,
    LockedFence,
    RevocationState,
    RevokedScopeError,
    StaleFenceStateError,
    UnknownFenceScopeError,
    advance_revocation,
    ensure_fence_exists,
    lock_fences_for_mutation,
)

from clauseos.domain.errors import RevocationFenceBlockedError

PRODUCT_ID = "clauseos"


def clauseos_fence_scope_key(*, tenant_id: str, object_id: str) -> str:
    """ClauseOS's scope-key convention for `cassandre_security.revocation`,
    per the integration brief: `product=clauseos;tenant=<tenant>;object=<id>`.
    The security package treats this as an opaque string; only this
    function's callers need to agree on the format."""
    return f"product={PRODUCT_ID};tenant={tenant_id};object={object_id}"


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


class SqlAlchemyRevocationFenceGateway:
    """Owned by `BundleService` (optionally -- see its `fence_gateway`
    constructor parameter) when the caller wants ClauseOS's critical
    mutations fenced against real Postgres revocation state. `None` (the
    default everywhere existing tests construct `BundleService`) disables
    fencing entirely, preserving every pre-existing test's behavior
    unchanged -- this is an additive capability, not a breaking change.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def ensure_scope_provisioned(self, scope_key: str, *, now: dt.datetime) -> None:
        with self._session_factory() as session:
            ensure_fence_exists(session, scope_key, now=now)
            session.commit()

    def lock_for_mutation(
        self,
        *,
        scope_key: str,
        presented_epoch: int,
        now: dt.datetime,
        max_staleness: dt.timedelta | None = None,
    ) -> LockedFence:
        """Raises `RevocationFenceBlockedError` (never the raw
        `cassandre_security.revocation.FenceError`) so ClauseOS's API
        layer only ever needs to switch on its own domain error taxonomy.
        A caller must treat any raised error as "the mutation did not
        happen" -- this function never partially applies a mutation."""
        kwargs: dict = {}
        if max_staleness is not None:
            kwargs["max_staleness"] = max_staleness
        with self._session_factory() as session:
            try:
                locked = lock_fences_for_mutation(
                    session,
                    scope_keys=(scope_key,),
                    presented_epoch_by_scope={scope_key: presented_epoch},
                    now=now,
                    **kwargs,
                )
            except (UnknownFenceScopeError, StaleFenceStateError, EpochMismatchError, RevokedScopeError) as exc:
                session.rollback()
                raise RevocationFenceBlockedError(scope_key=scope_key, reason=str(exc)) from exc
            session.commit()
            return locked[0]

    def advance(self, *, scope_key: str, new_state: RevocationState, now: dt.datetime) -> LockedFence:
        """Simulates/records an effective local revocation event for this
        scope (e.g. an actor's access being revoked) -- advances the
        epoch, so any mutation still presenting the prior epoch is
        subsequently blocked by `lock_for_mutation` above."""
        with self._session_factory() as session:
            try:
                result = advance_revocation(session, scope_key=scope_key, new_state=new_state, now=now)
            except FenceError as exc:
                session.rollback()
                raise RevocationFenceBlockedError(scope_key=scope_key, reason=str(exc)) from exc
            session.commit()
            return result

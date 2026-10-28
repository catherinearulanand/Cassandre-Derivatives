"""Revocation and minimum-version state: the security-fence/epoch
mechanism (SEC-14), resolving Appendix A's blocker JPMC-FINAL-01.

Spec section 7 / Appendix A blocker JPMC-FINAL-01 specifies exactly this
protocol:

1. Each product maintains a durable security epoch/fence for the relevant
   subject and policy scope. Critical mutations acquire the necessary
   fence-row locks in a fixed order, verify fresh trusted revocation state
   and required epochs, verify exact object versions, and retain the locks
   through the atomic domain/audit/outbox commit.
2. Effective local revocation acquires incompatible locks on the same
   fence rows and advances the epoch atomically. New mutations
   subsequently reject old sessions/approvals.
3. Ordering is explicit and honest: a mutation that commits first precedes
   effective local revocation; a revocation that commits first rejects
   the mutation. Revocation cannot retroactively prevent an already
   committed action.
4. `REVOCATION_REQUESTED`, upstream identity-provider revocation, and
   product-local `REVOCATION_EFFECTIVE` are separate recorded states.
5. Unknown, stale, rolled-back, or unacknowledged revocation state blocks
   new critical mutations.

This module provides the fence table model and the two competing
operations (`lock_fences_for_mutation` / `advance_revocation`) that race
against each other exactly the way CollateralOS's real
`SELECT ... FOR UPDATE` reservation test already proved works against
live Postgres in this repository (see
`products/collateralos/tests/test_sql_store.py`) -- this module reuses
that same proven pattern, generalized to revocation fencing.

Both functions take an already-open SQLAlchemy `Session`; the CALLER is
responsible for holding that session open (and its locks with it) through
its own atomic domain/audit/outbox commit, and for calling `session.commit()`
or `session.rollback()` itself. This module never commits on the caller's
behalf, because the fence lock must be held through the caller's full
transaction, not just this function call.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


def _ensure_utc(value: dt.datetime) -> dt.datetime:
    """SQLite does not preserve tzinfo on a `DateTime(timezone=True)`
    column round-trip (a well-known SQLAlchemy+SQLite limitation; real
    Postgres does not have this problem) -- treat a naive value read back
    from SQLite as UTC, since every writer in this module always writes
    UTC-aware datetimes."""
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


class RevocationState(str, Enum):
    CURRENT = "CURRENT"
    REVOCATION_REQUESTED = "REVOCATION_REQUESTED"
    REVOCATION_EFFECTIVE = "REVOCATION_EFFECTIVE"


class Base(DeclarativeBase):
    pass


class SecurityFenceRow(Base):
    """One row per (product, tenant, subject-or-policy) scope. `scope_key`
    is the caller's own encoding, e.g. "product=clauseos;tenant=SIM-A;
    subject=SIM-USER-07" -- this module does not interpret it, only locks
    and versions it."""

    __tablename__ = "cassandre_security_fence"

    scope_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    current_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revocation_state: Mapped[str] = mapped_column(String(32), nullable=False, default=RevocationState.CURRENT.value)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FenceError(ValueError):
    pass


class UnknownFenceScopeError(FenceError):
    """Spec: unknown revocation state blocks new critical mutations. A
    scope with no fence row yet is "unknown", not "implicitly current" --
    callers must explicitly initialize a scope (`ensure_fence_exists`)
    before it can authorize anything."""


class StaleFenceStateError(FenceError):
    pass


class EpochMismatchError(FenceError):
    """The mutation's presented `revocation_epoch` (from
    `authorization.AuthorizationRequest.revocation_epoch`) does not match
    the fence's current epoch -- either the caller used stale/cached
    authorization state, or a revocation already advanced the epoch."""


class RevokedScopeError(FenceError):
    pass


DEFAULT_MAX_STALENESS = dt.timedelta(seconds=60)  # spec Appendix A: "sixty-second maximum revocation-cache age
# for ordinary sensitive reads" is the suggested S1 parameter; critical
# mutations should use a tighter bound via the `max_staleness` argument.


def ensure_fence_exists(session: Session, scope_key: str, *, now: dt.datetime) -> SecurityFenceRow:
    """Idempotently create a fence row at epoch 0 / CURRENT if this scope
    has never been seen before. Not used by the mutation path itself
    (which must treat a missing row as UNKNOWN and block) -- this is the
    explicit provisioning step a product calls when a new subject/policy
    scope first comes into existence (e.g. on user/role creation)."""

    existing = session.get(SecurityFenceRow, scope_key)
    if existing is not None:
        return existing
    row = SecurityFenceRow(scope_key=scope_key, current_epoch=0, revocation_state=RevocationState.CURRENT.value, updated_at=now)
    session.add(row)
    session.flush()
    return row


@dataclass(frozen=True)
class LockedFence:
    scope_key: str
    epoch: int
    revocation_state: RevocationState


def lock_fences_for_mutation(
    session: Session,
    *,
    scope_keys: tuple[str, ...],
    presented_epoch_by_scope: dict[str, int],
    now: dt.datetime,
    max_staleness: dt.timedelta = DEFAULT_MAX_STALENESS,
) -> tuple[LockedFence, ...]:
    """Step 1 of JPMC-FINAL-01's protocol: fixed lock order (sorted scope
    keys, never caller-supplied order, to prevent deadlock), fresh
    trusted-state check, exact-epoch check. Raises and leaves the
    transaction rollback-ready on any failure; on success, returns the
    locked fence snapshot and the caller proceeds to its own atomic
    domain/audit/outbox writes IN THE SAME transaction before committing.
    """

    if not scope_keys:
        raise FenceError("at least one scope_key is required")

    locked: list[LockedFence] = []
    for scope_key in sorted(set(scope_keys)):  # fixed order across all callers -- prevents deadlock
        stmt = select(SecurityFenceRow).where(SecurityFenceRow.scope_key == scope_key).with_for_update()
        row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            raise UnknownFenceScopeError(f"no fence exists for scope {scope_key!r}; blocking critical mutation")

        if now - _ensure_utc(row.updated_at) > max_staleness:
            raise StaleFenceStateError(
                f"fence for scope {scope_key!r} was last updated at {row.updated_at.isoformat()}, "
                f"exceeding max_staleness={max_staleness}; blocking critical mutation"
            )

        if row.revocation_state == RevocationState.REVOCATION_EFFECTIVE.value:
            raise RevokedScopeError(f"scope {scope_key!r} has REVOCATION_EFFECTIVE state; mutation blocked")

        presented = presented_epoch_by_scope.get(scope_key)
        if presented is None:
            raise EpochMismatchError(f"no presented epoch supplied for scope {scope_key!r}")
        if presented != row.current_epoch:
            raise EpochMismatchError(
                f"scope {scope_key!r}: presented epoch {presented} does not match current epoch {row.current_epoch}"
            )

        locked.append(
            LockedFence(scope_key=scope_key, epoch=row.current_epoch, revocation_state=RevocationState(row.revocation_state))
        )

    return tuple(locked)


def advance_revocation(
    session: Session,
    *,
    scope_key: str,
    new_state: RevocationState,
    now: dt.datetime,
) -> LockedFence:
    """Step 2: effective local revocation. Locks the SAME row
    (`with_for_update`), so this genuinely races against
    `lock_fences_for_mutation` on Postgres: whichever transaction commits
    first wins, exactly per JPMC-FINAL-01 point 3 -- this function does not
    special-case that ordering, it just relies on the database's real row
    lock, the same mechanism CollateralOS's reservation concurrency test
    already proved works in this repository.
    """

    stmt = select(SecurityFenceRow).where(SecurityFenceRow.scope_key == scope_key).with_for_update()
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        raise UnknownFenceScopeError(f"no fence exists for scope {scope_key!r}; cannot revoke an unprovisioned scope")

    row.current_epoch += 1
    row.revocation_state = new_state.value
    row.updated_at = now
    session.flush()
    return LockedFence(scope_key=scope_key, epoch=row.current_epoch, revocation_state=new_state)

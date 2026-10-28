"""Incident runbooks and synthetic exercises (SEC-20).

Spec section 12: "Incident sequence: declare and time-stamp -> contain
identities and network paths -> suspend decrypt/sign/export capabilities
as appropriate -> preserve evidence without destroying keys blindly ->
assess affected plaintext and keys -> establish clean rebuild and current
trust state -> reconcile financial state -> restore in quarantine ->
independent approval to reopen -> lessons and regression tests. Local
compromised systems cannot be the sole authority declaring themselves
clean."

Spec Appendix A: degraded states are `NORMAL`, `READ_RESTRICTED`,
`AUTHORIZATION_STALE`, `KEY_SERVICE_UNAVAILABLE`, `TRUST_REVOKED`,
`INCIDENT_LOCKDOWN`, `RECOVERY_VALIDATING`. "New publication, grant
changes, decryption grants, approval, and export fail closed when their
dependencies cannot be validated... There is no automatic fail-open
switch."

This module wires the containment ACTIONS to primitives this package
already built (`revocation.advance_revocation` for identity containment,
`key_lifecycle.transition` for suspending a key) rather than reinventing
them, and provides the incident record schema plus a synthetic exercise
that actually calls those primitives end-to-end and asserts the system
responds correctly. Runbook prose for each incident type lives in
`RUNBOOKS.md` alongside this module -- narrative procedures are not code.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum


class DegradedState(str, Enum):
    NORMAL = "NORMAL"
    READ_RESTRICTED = "READ_RESTRICTED"
    AUTHORIZATION_STALE = "AUTHORIZATION_STALE"
    KEY_SERVICE_UNAVAILABLE = "KEY_SERVICE_UNAVAILABLE"
    TRUST_REVOKED = "TRUST_REVOKED"
    INCIDENT_LOCKDOWN = "INCIDENT_LOCKDOWN"
    RECOVERY_VALIDATING = "RECOVERY_VALIDATING"


# Which capabilities are permitted in each degraded state. Every state
# other than NORMAL denies at least new publication/export/decrypt grants
# -- there is no state in this table where "new_publish" or "new_export"
# is True except NORMAL, matching the spec's "no automatic fail-open
# switch."
_STATE_CAPABILITIES: dict[DegradedState, frozenset[str]] = {
    DegradedState.NORMAL: frozenset({"read", "new_publish", "new_export", "new_decrypt_grant", "new_approval"}),
    DegradedState.READ_RESTRICTED: frozenset({"read_already_authorized"}),
    DegradedState.AUTHORIZATION_STALE: frozenset({"read_already_authorized"}),
    DegradedState.KEY_SERVICE_UNAVAILABLE: frozenset({"read_already_authorized"}),
    DegradedState.TRUST_REVOKED: frozenset(),
    DegradedState.INCIDENT_LOCKDOWN: frozenset(),
    DegradedState.RECOVERY_VALIDATING: frozenset({"read_already_authorized"}),
}


def is_capability_permitted(state: DegradedState, capability: str) -> bool:
    return capability in _STATE_CAPABILITIES[state]


def assert_capability_permitted(state: DegradedState, capability: str) -> None:
    if not is_capability_permitted(state, capability):
        raise IncidentContainmentError(f"capability {capability!r} is not permitted in degraded state {state.value}")


class IncidentContainmentError(ValueError):
    pass


class IncidentType(str, Enum):
    RANSOMWARE = "RANSOMWARE"
    INVENTORY_OR_APPROVAL_BREACH = "INVENTORY_OR_APPROVAL_BREACH"
    AUDIT_FORK = "AUDIT_FORK"
    ONLINE_KEY_COMPROMISE = "ONLINE_KEY_COMPROMISE"
    ROOT_COMPROMISE = "ROOT_COMPROMISE"


class IncidentPhase(str, Enum):
    DECLARED = "DECLARED"
    CONTAINED = "CONTAINED"
    CAPABILITIES_SUSPENDED = "CAPABILITIES_SUSPENDED"
    EVIDENCE_PRESERVED = "EVIDENCE_PRESERVED"
    ASSESSED = "ASSESSED"
    REBUILT = "REBUILT"
    RECONCILED = "RECONCILED"
    RESTORED_IN_QUARANTINE = "RESTORED_IN_QUARANTINE"
    INDEPENDENTLY_APPROVED_TO_REOPEN = "INDEPENDENTLY_APPROVED_TO_REOPEN"
    CLOSED = "CLOSED"


# Phases must advance in this exact order -- spec: "declare... -> contain
# -> suspend... -> preserve evidence... -> assess... -> rebuild... ->
# reconcile... -> restore in quarantine -> independent approval to reopen
# -> lessons." No phase may be skipped.
_PHASE_ORDER: tuple[IncidentPhase, ...] = (
    IncidentPhase.DECLARED,
    IncidentPhase.CONTAINED,
    IncidentPhase.CAPABILITIES_SUSPENDED,
    IncidentPhase.EVIDENCE_PRESERVED,
    IncidentPhase.ASSESSED,
    IncidentPhase.REBUILT,
    IncidentPhase.RECONCILED,
    IncidentPhase.RESTORED_IN_QUARANTINE,
    IncidentPhase.INDEPENDENTLY_APPROVED_TO_REOPEN,
    IncidentPhase.CLOSED,
)


@dataclass(frozen=True)
class IncidentRoles:
    """Spec: 'Runbooks name the incident commander, containment operator,
    domain/custody owner, recovery operator, evidence custodian, and
    independent resumption checker, with alternates and recorded role
    overlaps.' Every role is a distinct field; `independent_resumption_checker`
    must differ from `incident_commander` (enforced below) -- the person
    who declared/ran the incident cannot be the sole authority declaring
    it resolved (spec: 'Local compromised systems cannot be the sole
    authority declaring themselves clean.')."""

    incident_commander: str
    containment_operator: str
    domain_custody_owner: str
    recovery_operator: str
    evidence_custodian: str
    independent_resumption_checker: str
    alternates: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.independent_resumption_checker == self.incident_commander:
            raise IncidentContainmentError(
                "independent_resumption_checker must not be the same person as incident_commander"
            )


@dataclass
class IncidentRecord:
    incident_id: str
    incident_type: IncidentType
    roles: IncidentRoles
    phase: IncidentPhase
    degraded_state: DegradedState
    declared_at: dt.datetime
    phase_history: tuple[tuple[IncidentPhase, dt.datetime], ...]
    notes: tuple[str, ...] = ()


def declare_incident(
    *, incident_id: str, incident_type: IncidentType, roles: IncidentRoles, now: dt.datetime
) -> IncidentRecord:
    return IncidentRecord(
        incident_id=incident_id,
        incident_type=incident_type,
        roles=roles,
        phase=IncidentPhase.DECLARED,
        degraded_state=DegradedState.INCIDENT_LOCKDOWN,
        declared_at=now,
        phase_history=((IncidentPhase.DECLARED, now),),
    )


def advance_phase(record: IncidentRecord, *, to: IncidentPhase, now: dt.datetime, note: str = "") -> IncidentRecord:
    current_index = _PHASE_ORDER.index(record.phase)
    target_index = _PHASE_ORDER.index(to)
    if target_index != current_index + 1:
        raise IncidentContainmentError(
            f"cannot advance incident {record.incident_id!r} from {record.phase.value} directly to {to.value}; "
            f"the next phase must be {_PHASE_ORDER[current_index + 1].value}"
        )

    from dataclasses import replace

    new_degraded_state = record.degraded_state
    if to is IncidentPhase.RESTORED_IN_QUARANTINE:
        new_degraded_state = DegradedState.RECOVERY_VALIDATING
    elif to is IncidentPhase.CLOSED:
        new_degraded_state = DegradedState.NORMAL

    return replace(
        record,
        phase=to,
        degraded_state=new_degraded_state,
        phase_history=record.phase_history + ((to, now),),
        notes=record.notes + ((note,) if note else ()),
    )


def close_incident_requires_independent_checker(
    record: IncidentRecord, *, closer_person_id: str
) -> None:
    """Spec: the incident commander cannot be the sole authority declaring
    resolution. Call this before the CLOSED transition to enforce it (kept
    separate from `advance_phase` so a caller can check it at whatever
    point makes sense for their own approval workflow)."""
    if closer_person_id != record.roles.independent_resumption_checker:
        raise IncidentContainmentError(
            f"only the designated independent_resumption_checker ({record.roles.independent_resumption_checker!r}) "
            f"may close an incident, not {closer_person_id!r}"
        )

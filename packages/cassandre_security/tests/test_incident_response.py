from __future__ import annotations

import datetime as dt

import pytest

from cassandre_security.incident_response import (
    DegradedState,
    IncidentContainmentError,
    IncidentPhase,
    IncidentRoles,
    IncidentType,
    advance_phase,
    assert_capability_permitted,
    close_incident_requires_independent_checker,
    declare_incident,
    is_capability_permitted,
)

NOW = dt.datetime(2026, 1, 5, 12, 0, 0, tzinfo=dt.timezone.utc)


def _roles(**overrides) -> IncidentRoles:
    defaults = dict(
        incident_commander="SIM-COMMANDER",
        containment_operator="SIM-CONTAINMENT",
        domain_custody_owner="SIM-CUSTODY-OWNER",
        recovery_operator="SIM-RECOVERY",
        evidence_custodian="SIM-EVIDENCE",
        independent_resumption_checker="SIM-CHECKER",
    )
    defaults.update(overrides)
    return IncidentRoles(**defaults)


def test_roles_reject_commander_as_own_resumption_checker():
    with pytest.raises(IncidentContainmentError):
        _roles(independent_resumption_checker="SIM-COMMANDER")


def test_declare_incident_starts_in_lockdown():
    record = declare_incident(incident_id="SIM-INC-1", incident_type=IncidentType.RANSOMWARE, roles=_roles(), now=NOW)
    assert record.phase is IncidentPhase.DECLARED
    assert record.degraded_state is DegradedState.INCIDENT_LOCKDOWN


def test_no_capability_is_permitted_during_lockdown():
    for capability in ("read", "new_publish", "new_export", "new_decrypt_grant", "new_approval", "read_already_authorized"):
        assert not is_capability_permitted(DegradedState.INCIDENT_LOCKDOWN, capability)


def test_normal_state_permits_everything():
    for capability in ("read", "new_publish", "new_export", "new_decrypt_grant", "new_approval"):
        assert is_capability_permitted(DegradedState.NORMAL, capability)


def test_no_degraded_state_other_than_normal_permits_new_publish():
    """The spec's 'no automatic fail-open switch' as a structural
    invariant, not just a sentence in a docstring."""
    for state in DegradedState:
        if state is DegradedState.NORMAL:
            continue
        assert not is_capability_permitted(state, "new_publish")
        assert not is_capability_permitted(state, "new_export")


def test_assert_capability_permitted_raises_when_denied():
    with pytest.raises(IncidentContainmentError):
        assert_capability_permitted(DegradedState.TRUST_REVOKED, "new_export")


def test_phases_must_advance_in_order_no_skipping():
    record = declare_incident(incident_id="SIM-INC-1", incident_type=IncidentType.RANSOMWARE, roles=_roles(), now=NOW)
    with pytest.raises(IncidentContainmentError):
        advance_phase(record, to=IncidentPhase.EVIDENCE_PRESERVED, now=NOW)  # skips CONTAINED, CAPABILITIES_SUSPENDED


def test_full_phase_sequence_walks_through_in_order():
    record = declare_incident(incident_id="SIM-INC-1", incident_type=IncidentType.ONLINE_KEY_COMPROMISE, roles=_roles(), now=NOW)
    for phase in (
        IncidentPhase.CONTAINED,
        IncidentPhase.CAPABILITIES_SUSPENDED,
        IncidentPhase.EVIDENCE_PRESERVED,
        IncidentPhase.ASSESSED,
        IncidentPhase.REBUILT,
        IncidentPhase.RECONCILED,
        IncidentPhase.RESTORED_IN_QUARANTINE,
        IncidentPhase.INDEPENDENTLY_APPROVED_TO_REOPEN,
    ):
        record = advance_phase(record, to=phase, now=NOW)
    assert record.phase is IncidentPhase.INDEPENDENTLY_APPROVED_TO_REOPEN
    assert record.degraded_state is DegradedState.RECOVERY_VALIDATING

    record = advance_phase(record, to=IncidentPhase.CLOSED, now=NOW)
    assert record.degraded_state is DegradedState.NORMAL


def test_close_incident_requires_independent_checker_not_commander():
    record = declare_incident(incident_id="SIM-INC-1", incident_type=IncidentType.AUDIT_FORK, roles=_roles(), now=NOW)
    with pytest.raises(IncidentContainmentError):
        close_incident_requires_independent_checker(record, closer_person_id="SIM-COMMANDER")
    close_incident_requires_independent_checker(record, closer_person_id="SIM-CHECKER")  # does not raise


def test_all_five_incident_types_are_distinct_enum_members():
    assert len({t for t in IncidentType}) == 5

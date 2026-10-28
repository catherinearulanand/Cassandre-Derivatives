from __future__ import annotations

import datetime as dt

import pytest
from cassandre_contracts import SimulationProvenance

from cassandre_security.classification import (
    DataClassification,
    DeclassificationApproval,
    DeclassificationError,
    SyntheticProvenanceError,
    declassify,
    highest,
    require_synthetic_provenance,
)


def test_highest_picks_most_sensitive():
    assert highest(DataClassification.PUBLIC, DataClassification.RESTRICTED) is DataClassification.RESTRICTED
    assert highest(DataClassification.KEY_MATERIAL) is DataClassification.KEY_MATERIAL


def test_require_synthetic_provenance_accepts_valid():
    prov = SimulationProvenance(is_simulated=True, scenario_id="SIM-SCEN-1", generator_version="0.1.0")
    assert require_synthetic_provenance(prov) is prov


def test_require_synthetic_provenance_rejects_missing():
    with pytest.raises(SyntheticProvenanceError):
        require_synthetic_provenance(None)


def test_require_synthetic_provenance_rejects_empty_scenario_id():
    # Pydantic model requires is_simulated Literal[True], so construct a
    # minimal stand-in object with the same duck-typed shape to exercise
    # this function's own empty-string check.
    class _Stub:
        is_simulated = True
        scenario_id = ""
        generator_version = "0.1.0"

    with pytest.raises(SyntheticProvenanceError):
        require_synthetic_provenance(_Stub())  # type: ignore[arg-type]


def test_declassify_without_approval_fails_closed_to_highest_input():
    result = declassify(
        source_classification=DataClassification.RESTRICTED,
        target_classification=DataClassification.INTERNAL,
        approval=None,
    )
    assert result is DataClassification.RESTRICTED


def test_declassify_with_valid_distinct_approvers_succeeds():
    approval = DeclassificationApproval(
        data_owner_person_id="SIM-PERSON-OWNER",
        security_approver_person_id="SIM-PERSON-SECURITY",
        source_object_id="SIM-OBJ-1",
        source_object_version="v1",
        transformation_digest="deadbeef",
        approved_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    )
    result = declassify(
        source_classification=DataClassification.RESTRICTED,
        target_classification=DataClassification.INTERNAL,
        approval=approval,
    )
    assert result is DataClassification.INTERNAL


def test_declassify_rejects_same_person_as_both_approvers():
    approval = DeclassificationApproval(
        data_owner_person_id="SIM-PERSON-SAME",
        security_approver_person_id="SIM-PERSON-SAME",
        source_object_id="SIM-OBJ-1",
        source_object_version="v1",
        transformation_digest="deadbeef",
        approved_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    )
    with pytest.raises(DeclassificationError):
        declassify(
            source_classification=DataClassification.RESTRICTED,
            target_classification=DataClassification.PUBLIC,
            approval=approval,
        )


def test_declassify_raising_sensitivity_needs_no_approval():
    result = declassify(
        source_classification=DataClassification.PUBLIC,
        target_classification=DataClassification.RESTRICTED,
        approval=None,
    )
    assert result is DataClassification.RESTRICTED

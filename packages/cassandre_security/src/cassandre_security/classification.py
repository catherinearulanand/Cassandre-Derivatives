"""Data classification and synthetic-only import gate (SEC-02).

Maps to the security architecture spec section 6's five-class table
(PUBLIC/SYNTHETIC/INTERNAL/RESTRICTED/KEY_MATERIAL) and the "only a named
customer data owner plus an independent security approver may authorize
declassification" rule. This module is the classification *contract* every
product's own ingestion path should call; it does not itself scan storage
or enforce filesystem/network egress (that is SEC-06/07, out of scope this
pass).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

from cassandre_contracts import SimulationProvenance


class DataClassification(str, Enum):
    PUBLIC = "PUBLIC"
    SYNTHETIC = "SYNTHETIC"
    INTERNAL = "INTERNAL"
    RESTRICTED = "RESTRICTED"
    KEY_MATERIAL = "KEY_MATERIAL"


# Total order for "output retains the highest input classification" (spec
# section 6: a failed/absent declassification approval must not silently
# downgrade). Higher index = more sensitive.
_RANK: dict[DataClassification, int] = {
    DataClassification.PUBLIC: 0,
    DataClassification.SYNTHETIC: 1,
    DataClassification.INTERNAL: 2,
    DataClassification.RESTRICTED: 3,
    DataClassification.KEY_MATERIAL: 4,
}


def highest(*classes: DataClassification) -> DataClassification:
    if not classes:
        raise ValueError("at least one classification is required")
    return max(classes, key=lambda c: _RANK[c])


class SyntheticProvenanceError(ValueError):
    """Raised when content lacking verified SYNTHETIC provenance is
    presented to an S1 ingestion path. This is the "cross-class rejection"
    gate the spec's SEC-02 evidence column requires — it fails closed."""


def require_synthetic_provenance(provenance: SimulationProvenance | None) -> SimulationProvenance:
    """S1 ingestion gate: every object entering this deployment profile
    must carry verified `is_simulated=True` provenance with a non-empty
    scenario/generator identity. A missing or falsified-shape provenance
    object is rejected outright; this is necessarily a shape check, not
    proof that the bytes are truly fictional (spec section 3.2's "a label
    is necessary but insufficient" caveat) — real-content review remains a
    human/process control outside this function's reach.
    """
    if provenance is None:
        raise SyntheticProvenanceError("no simulation provenance supplied; S1 ingestion requires SYNTHETIC-only data")
    if provenance.is_simulated is not True:
        raise SyntheticProvenanceError("provenance.is_simulated must be True in the S1 deployment profile")
    if not provenance.scenario_id or not provenance.generator_version:
        raise SyntheticProvenanceError("provenance must carry a non-empty scenario_id and generator_version")
    return provenance


@dataclass(frozen=True)
class PlaintextInventoryRecord:
    """One process's declared plaintext-handling contract (spec section 6:
    "For every process record plaintext inputs, intermediate memory, temp
    paths, subprocesses, crash behavior, swap, GPU allocation, output
    destination, cleanup, and maximum retention."). This is a registration
    artifact products fill in and this package can audit for completeness —
    it does not itself observe runtime memory/swap/GPU behavior.
    """

    process_name: str
    plaintext_inputs: tuple[str, ...]
    intermediate_memory_notes: str
    temp_paths: tuple[str, ...]
    subprocesses: tuple[str, ...]
    crash_behavior: str
    swap_exposure: str
    gpu_allocation: str
    output_destination: str
    cleanup_procedure: str
    max_retention: dt.timedelta | None  # None means "no retention" (immediately discarded)


class DeclassificationError(ValueError):
    pass


@dataclass(frozen=True)
class DeclassificationApproval:
    data_owner_person_id: str
    security_approver_person_id: str
    source_object_id: str
    source_object_version: str
    transformation_digest: str
    approved_at: dt.datetime


def declassify(
    *,
    source_classification: DataClassification,
    target_classification: DataClassification,
    approval: DeclassificationApproval | None,
) -> DataClassification:
    """Spec section 6: "Only a named customer data owner plus an
    independent security approver may authorize declassification of an
    exact source/version and deterministic transformation digest... The
    transformation... fails closed; absent approval, output retains the
    highest input classification."

    Models, parsers, service identities, and application administrators
    cannot call this with a synthetic/forged approval and have it accepted
    silently -- the two person IDs must be distinct, matching
    `maker_checker.enforce_distinct_natural_persons`.
    """
    if _RANK[target_classification] >= _RANK[source_classification]:
        # Not actually a downgrade; no approval needed to keep or raise
        # sensitivity.
        return highest(source_classification, target_classification)

    if approval is None:
        return source_classification  # fail closed: retain highest input classification

    if approval.data_owner_person_id == approval.security_approver_person_id:
        raise DeclassificationError(
            "declassification requires two distinct natural persons (data owner and independent security approver)"
        )
    if not approval.data_owner_person_id or not approval.security_approver_person_id:
        raise DeclassificationError("declassification approval requires both named approvers")

    return target_classification

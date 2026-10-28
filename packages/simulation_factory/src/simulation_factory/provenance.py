"""Simulation provenance carried by every generated fixture object, per plan
Section 9 / ADR-013 and the envelope's `simulation_provenance` shape
(`cassandre_contracts.envelope.SimulationProvenance`: `is_simulated`,
`scenario_id`, `generator_version`).

This module deliberately does **not** import `cassandre_contracts` — the
instructions for this work item are explicit that a plain dataclass with the
same three fields is sufficient and keeps this package's dependency
footprint small. `is_simulated` cannot be constructed as anything other than
`True`; that is not a convention, it is enforced in `__post_init__` so a
generator bug can never accidentally mint fixture data that reads as real.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

GENERATOR_VERSION = "1.0.0"


class ProvenanceError(ValueError):
    pass


@dataclass(frozen=True)
class SimulationProvenance:
    """Mirrors `cassandre_contracts.envelope.SimulationProvenance`'s three
    fields exactly (`is_simulated`, `scenario_id`, `generator_version`)
    without importing that package."""

    is_simulated: bool
    scenario_id: str
    generator_version: str

    def __post_init__(self) -> None:
        if self.is_simulated is not True:
            raise ProvenanceError(
                "SimulationProvenance.is_simulated must always be True for "
                "every simulation_factory fixture object"
            )
        if not self.scenario_id:
            raise ProvenanceError("SimulationProvenance.scenario_id must be non-empty")
        if not self.generator_version:
            raise ProvenanceError("SimulationProvenance.generator_version must be non-empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_simulated": self.is_simulated,
            "scenario_id": self.scenario_id,
            "generator_version": self.generator_version,
        }


def make_provenance(scenario_id: str, *, generator_version: str = GENERATOR_VERSION) -> SimulationProvenance:
    return SimulationProvenance(
        is_simulated=True,
        scenario_id=scenario_id,
        generator_version=generator_version,
    )

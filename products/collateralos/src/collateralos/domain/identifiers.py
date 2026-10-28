"""Re-export of the reserved `SIM-*` namespace guard (plan Section 9.1).

CollateralOS domain code depends on `simulation_factory` only for this pure,
dependency-free identifier check -- never for fixture generation, and never
imported the other way around.
"""

from __future__ import annotations

from simulation_factory.identifiers import IdentifierNamespaceError, require_sim_identifier

__all__ = ["IdentifierNamespaceError", "require_sim_identifier"]

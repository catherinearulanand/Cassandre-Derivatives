"""The fictional design-partner institution, per plan Section 9.1 (work item
SIM-001). `CASSANDRE_SIM_BANK_NA` is the active USD calculation entity;
`CASSANDRE_SIM_BANK_IFSC` is the affiliate that exists solely to prove
legal-entity/netting-set/account/inventory segregation cannot be crossed.
"""

from __future__ import annotations

from dataclasses import dataclass

from simulation_factory.identifiers import require_sim_identifier


@dataclass(frozen=True)
class SyntheticEntity:
    entity_id: str
    display_name: str
    lei_shaped_id: str
    home_currency: str
    is_active_calculation_entity: bool

    def __post_init__(self) -> None:
        require_sim_identifier(self.entity_id, field_name="entity_id")
        require_sim_identifier(self.lei_shaped_id, field_name="lei_shaped_id")
        if not self.display_name.startswith("Cassandre Simulation Bank"):
            raise ValueError(
                "display_name must clearly identify this as the fictional "
                "Cassandre Simulation Bank institution, never a real bank name"
            )


CASSANDRE_SIM_BANK_NA = SyntheticEntity(
    entity_id="SIM-ENTITY-CASSANDRE-BANK-NA",
    display_name="Cassandre Simulation Bank N.A. (fictional, S1 synthetic entity)",
    lei_shaped_id="SIM-LEI-0000000000000CASNA",
    home_currency="USD",
    is_active_calculation_entity=True,
)

CASSANDRE_SIM_BANK_IFSC = SyntheticEntity(
    entity_id="SIM-ENTITY-CASSANDRE-BANK-IFSC",
    display_name="Cassandre Simulation Bank IFSC Branch (fictional, segregation-test affiliate)",
    lei_shaped_id="SIM-LEI-0000000000000CASIF",
    home_currency="USD",
    is_active_calculation_entity=False,
)

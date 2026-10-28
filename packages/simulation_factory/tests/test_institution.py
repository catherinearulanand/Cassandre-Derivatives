import pytest

from simulation_factory import CASSANDRE_SIM_BANK_IFSC, CASSANDRE_SIM_BANK_NA, SyntheticEntity
from simulation_factory.identifiers import IdentifierNamespaceError, is_sim_identifier


def test_active_entity_is_the_na_bank():
    assert CASSANDRE_SIM_BANK_NA.is_active_calculation_entity is True
    assert CASSANDRE_SIM_BANK_IFSC.is_active_calculation_entity is False


def test_entities_are_in_reserved_namespace():
    assert is_sim_identifier(CASSANDRE_SIM_BANK_NA.entity_id)
    assert is_sim_identifier(CASSANDRE_SIM_BANK_IFSC.entity_id)


def test_entities_are_distinct():
    assert CASSANDRE_SIM_BANK_NA.entity_id != CASSANDRE_SIM_BANK_IFSC.entity_id


def test_rejects_non_sim_identifier():
    with pytest.raises(IdentifierNamespaceError):
        SyntheticEntity(
            entity_id="REAL-BANK-0001",
            display_name="Cassandre Simulation Bank Rogue",
            lei_shaped_id="SIM-LEI-X",
            home_currency="USD",
            is_active_calculation_entity=True,
        )


def test_rejects_display_name_not_clearly_fictional():
    with pytest.raises(ValueError):
        SyntheticEntity(
            entity_id="SIM-ENTITY-X",
            display_name="JPMorgan Chase Bank N.A.",
            lei_shaped_id="SIM-LEI-X",
            home_currency="USD",
            is_active_calculation_entity=True,
        )

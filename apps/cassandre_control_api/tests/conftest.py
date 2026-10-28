import datetime as dt

import pytest

from audit_protocol.keys import generate_keypair
from cassandre_contracts import ProducerProduct, SimulationProvenance, build_envelope, sign_envelope


@pytest.fixture
def signed_envelope_factory():
    def _make(producer_product: ProducerProduct, scenario_id: str, object_id: str, key_id: str):
        kp = generate_keypair(key_id)
        env = build_envelope(
            schema_name=f"{producer_product.value}.test_object",
            schema_version="1.0.0",
            producer_product=producer_product,
            producer_version="0.1.0",
            issuer=f"{producer_product.value}_api",
            audience=["cassandre"],
            object_id=object_id,
            object_version="v1",
            created_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            effective_as_of=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            simulation_provenance=SimulationProvenance(
                is_simulated=True, scenario_id=scenario_id, generator_version="0.1.0"
            ),
            authority_status="SIMULATION_ELIGIBLE",
            purpose_constraints=[],
            lineage=[],
            payload={"note": "test fixture payload"},
        )
        signed = sign_envelope(env, kp)
        return signed, kp

    return _make

"""Wrap a `ResultPackage` as a signed `cassandre_contracts.Envelope`, per
plan Section 8.1 and the Section 25.5 slice requirement that RiskOS export
"a signed calculation and dispute-evidence bundle."
"""

from __future__ import annotations

import datetime as dt

from audit_protocol.keys import Ed25519KeyPair
from cassandre_contracts import (
    ContentReference,
    Envelope,
    ProducerProduct,
    SimulationProvenance,
    build_envelope,
    sign_envelope,
)

from riskos.application.serialization import result_package_to_payload
from riskos.domain.entities import ResultPackage

SCHEMA_NAME = "riskos.result_package"
SCHEMA_VERSION = "1.0.0"
PRODUCER_VERSION = "0.1.0"


def build_signed_result_envelope(
    result_package: ResultPackage,
    *,
    keypair: Ed25519KeyPair,
    issuer: str = "riskos_api",
    audience: list[str] | None = None,
    scenario_id: str = "SIM-SCEN-RISKOS-0001",
    generator_version: str = "0.1.0",
    now: dt.datetime | None = None,
) -> Envelope:
    now = now or dt.datetime.now(dt.timezone.utc)
    envelope = build_envelope(
        schema_name=SCHEMA_NAME,
        schema_version=SCHEMA_VERSION,
        producer_product=ProducerProduct.RISKOS,
        producer_version=PRODUCER_VERSION,
        issuer=issuer,
        audience=audience or ["cassandre"],
        object_id=result_package.result_id,
        object_version="v1",
        created_at=now,
        effective_as_of=result_package.generated_at,
        simulation_provenance=SimulationProvenance(
            is_simulated=True, scenario_id=scenario_id, generator_version=generator_version
        ),
        authority_status=result_package.actionability.value,
        purpose_constraints=["MARGIN_CALL"],
        lineage=[
            ContentReference(
                object_id=result_package.run_id,
                schema_name="riskos.calculation_run",
                content_hash="0" * 64,
            )
        ],
        payload=result_package_to_payload(result_package),
    )
    return sign_envelope(envelope, keypair)

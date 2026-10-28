"""Wraps a published `PolicyRelease` as a signed `cassandre_contracts.Envelope`
(plan Section 25.5 item 8 / Section 8.1). This is the only ClauseOS-internal
object every other product or Cassandre may consume -- never the raw domain
object or an internal table row.
"""

from __future__ import annotations

import datetime as dt

from audit_protocol.keys import Ed25519KeyPair
from cassandre_contracts import Envelope, ProducerProduct, SimulationProvenance, build_envelope, sign_envelope
from clauseos.domain.policy import PolicyRelease

SCHEMA_NAME = "clauseos.policy_release"
SCHEMA_VERSION = "1.0.0"
PRODUCER_VERSION = "0.1.0"


def build_policy_release_envelope(
    *,
    release: PolicyRelease,
    issuer: str,
    keypair: Ed25519KeyPair,
    created_at: dt.datetime,
    audience: list[str] | None = None,
) -> Envelope:
    envelope = build_envelope(
        schema_name=SCHEMA_NAME,
        schema_version=SCHEMA_VERSION,
        producer_product=ProducerProduct.CLAUSEOS,
        producer_version=PRODUCER_VERSION,
        issuer=issuer,
        audience=audience or ["cassandre"],
        object_id=release.release_id,
        object_version="v1",
        created_at=created_at,
        effective_as_of=created_at,
        simulation_provenance=SimulationProvenance(
            is_simulated=True,
            scenario_id=release.scenario_id,
            generator_version=PRODUCER_VERSION,
        ),
        authority_status="SIMULATION_ELIGIBLE",
        purpose_constraints=["POLICY_RELEASE"],
        lineage=[],
        payload=release.as_canonical_dict(),
    )
    return sign_envelope(envelope, keypair)

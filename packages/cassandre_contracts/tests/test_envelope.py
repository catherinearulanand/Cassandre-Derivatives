import datetime as dt

import pytest
from pydantic import ValidationError

from audit_protocol.keys import generate_keypair
from cassandre_contracts import (
    Envelope,
    ProducerProduct,
    SimulationProvenance,
    TrustBundle,
    build_envelope,
    sign_envelope,
    verify_envelope,
)


def _make_envelope(**overrides):
    fields = dict(
        schema_name="riskos.margin_waterfall",
        schema_version="1.0.0",
        producer_product=ProducerProduct.RISKOS,
        producer_version="0.1.0",
        issuer="riskos_api",
        audience=["cassandre"],
        object_id="run-0001",
        object_version="v1",
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        effective_as_of=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        simulation_provenance=SimulationProvenance(
            is_simulated=True, scenario_id="SIM-SCEN-0001", generator_version="0.1.0"
        ),
        authority_status="SIMULATION_ELIGIBLE",
        purpose_constraints=["MARGIN_CALL"],
        lineage=[],
        payload={"call_amount": "10000.00"},
    )
    fields.update(overrides)
    return build_envelope(**fields)


def test_simulation_provenance_is_mandatory():
    with pytest.raises(ValidationError):
        Envelope(
            schema_name="x",
            schema_version="1.0.0",
            producer_product=ProducerProduct.RISKOS,
            producer_version="1.0.0",
            issuer="x",
            audience=["cassandre"],
            object_id="1",
            object_version="v1",
            content_hash="a" * 64,
            created_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            effective_as_of=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            authority_status="X",
            purpose_constraints=[],
            lineage=[],
            payload={},
        )


def test_build_envelope_produces_consistent_hash():
    env = _make_envelope()
    assert env.compute_content_hash() == env.content_hash


def test_sign_and_verify_success():
    kp = generate_keypair("riskos-key-1")
    env = sign_envelope(_make_envelope(), kp)
    bundle = TrustBundle(keys_by_id={kp.key_id: kp.public_key_b64})
    result = verify_envelope(env, trust_bundle=bundle, expected_audience="cassandre")
    assert result.ok, result.failures


def test_verify_fails_on_unknown_key():
    kp = generate_keypair("riskos-key-1")
    env = sign_envelope(_make_envelope(), kp)
    empty_bundle = TrustBundle(keys_by_id={})
    result = verify_envelope(env, trust_bundle=empty_bundle, expected_audience="cassandre")
    assert not result.ok
    assert "UNKNOWN_OR_REVOKED_KEY_ID" in result.failures


def test_verify_fails_on_revoked_key():
    kp = generate_keypair("riskos-key-1")
    env = sign_envelope(_make_envelope(), kp)
    bundle = TrustBundle(keys_by_id={kp.key_id: kp.public_key_b64}, revoked_key_ids=frozenset({kp.key_id}))
    result = verify_envelope(env, trust_bundle=bundle, expected_audience="cassandre")
    assert not result.ok
    assert "UNKNOWN_OR_REVOKED_KEY_ID" in result.failures


def test_verify_fails_on_tampered_payload():
    kp = generate_keypair("riskos-key-1")
    env = sign_envelope(_make_envelope(), kp)
    tampered = env.model_copy(update={"payload": {"call_amount": "999999.00"}})
    bundle = TrustBundle(keys_by_id={kp.key_id: kp.public_key_b64})
    result = verify_envelope(tampered, trust_bundle=bundle, expected_audience="cassandre")
    assert not result.ok
    assert "CONTENT_HASH_MISMATCH" in result.failures


def test_verify_fails_on_wrong_audience():
    kp = generate_keypair("riskos-key-1")
    env = sign_envelope(_make_envelope(), kp)
    bundle = TrustBundle(keys_by_id={kp.key_id: kp.public_key_b64})
    result = verify_envelope(env, trust_bundle=bundle, expected_audience="clauseos")
    assert not result.ok
    assert "AUDIENCE_MISMATCH" in result.failures


def test_verify_fails_on_stale_envelope():
    kp = generate_keypair("riskos-key-1")
    env = sign_envelope(_make_envelope(), kp)
    bundle = TrustBundle(keys_by_id={kp.key_id: kp.public_key_b64})
    result = verify_envelope(
        env,
        trust_bundle=bundle,
        expected_audience="cassandre",
        max_age=dt.timedelta(hours=1),
        now=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
    )
    assert not result.ok
    assert "STALE" in result.failures


def test_verify_fails_without_signature():
    env = _make_envelope()
    bundle = TrustBundle(keys_by_id={})
    result = verify_envelope(env, trust_bundle=bundle, expected_audience="cassandre")
    assert not result.ok
    assert "MISSING_SIGNATURE" in result.failures

import dataclasses
import datetime as dt

from audit_protocol.chain import GENESIS_HASH, append_event, verify_chain


def _now(offset_seconds: int = 0) -> dt.datetime:
    return dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=offset_seconds)


def _prov() -> dict:
    return {"is_simulated": True, "scenario_id": "SIM-SCEN-0001", "generator_version": "0.1.0"}


def test_first_event_chains_from_genesis():
    e0 = append_event(
        previous_event=None, event_id="evt-0", product="clauseos", aggregate_id="bundle-1",
        actor_id="user-1", actor_type="human", action="BUNDLE_CREATED", reason_code=None,
        object_version="v1", object_content_hash="a" * 64, event_timestamp=_now(),
        simulation_provenance=_prov(), trace_id="trace-1",
    )
    assert e0.sequence == 0
    assert e0.previous_event_hash == GENESIS_HASH


def test_chain_of_three_verifies():
    e0 = append_event(
        previous_event=None, event_id="evt-0", product="clauseos", aggregate_id="bundle-1",
        actor_id="user-1", actor_type="human", action="BUNDLE_CREATED", reason_code=None,
        object_version="v1", object_content_hash="a" * 64, event_timestamp=_now(0),
        simulation_provenance=_prov(), trace_id="trace-1",
    )
    e1 = append_event(
        previous_event=e0, event_id="evt-1", product="clauseos", aggregate_id="bundle-1",
        actor_id="user-1", actor_type="human", action="BUNDLE_ANALYZED", reason_code=None,
        object_version="v2", object_content_hash="b" * 64, event_timestamp=_now(1),
        simulation_provenance=_prov(), trace_id="trace-1",
    )
    e2 = append_event(
        previous_event=e1, event_id="evt-2", product="clauseos", aggregate_id="bundle-1",
        actor_id="user-2", actor_type="human", action="BUNDLE_APPROVED", reason_code=None,
        object_version="v3", object_content_hash="c" * 64, event_timestamp=_now(2),
        simulation_provenance=_prov(), trace_id="trace-1",
    )
    result = verify_chain([e0, e1, e2])
    assert result.ok is True
    assert result.checked == 3


def test_tampered_middle_event_detected():
    e0 = append_event(
        previous_event=None, event_id="evt-0", product="clauseos", aggregate_id="bundle-1",
        actor_id="user-1", actor_type="human", action="BUNDLE_CREATED", reason_code=None,
        object_version="v1", object_content_hash="a" * 64, event_timestamp=_now(0),
        simulation_provenance=_prov(), trace_id="trace-1",
    )
    e1 = append_event(
        previous_event=e0, event_id="evt-1", product="clauseos", aggregate_id="bundle-1",
        actor_id="user-1", actor_type="human", action="BUNDLE_ANALYZED", reason_code=None,
        object_version="v2", object_content_hash="b" * 64, event_timestamp=_now(1),
        simulation_provenance=_prov(), trace_id="trace-1",
    )
    tampered_e1 = dataclasses.replace(e1, action="BUNDLE_APPROVED")  # mutate without recomputing hash
    result = verify_chain([e0, tampered_e1])
    assert result.ok is False
    assert result.first_failure_sequence == 1


def test_forked_chain_detected():
    e0 = append_event(
        previous_event=None, event_id="evt-0", product="clauseos", aggregate_id="bundle-1",
        actor_id="user-1", actor_type="human", action="BUNDLE_CREATED", reason_code=None,
        object_version="v1", object_content_hash="a" * 64, event_timestamp=_now(0),
        simulation_provenance=_prov(), trace_id="trace-1",
    )
    branch_a = append_event(
        previous_event=e0, event_id="evt-1a", product="clauseos", aggregate_id="bundle-1",
        actor_id="user-1", actor_type="human", action="BUNDLE_ANALYZED", reason_code=None,
        object_version="v2a", object_content_hash="b" * 64, event_timestamp=_now(1),
        simulation_provenance=_prov(), trace_id="trace-1",
    )
    branch_b = append_event(
        previous_event=e0, event_id="evt-1b", product="clauseos", aggregate_id="bundle-1",
        actor_id="user-1", actor_type="human", action="BUNDLE_ANALYZED", reason_code=None,
        object_version="v2b", object_content_hash="c" * 64, event_timestamp=_now(1),
        simulation_provenance=_prov(), trace_id="trace-1",
    )
    result = verify_chain([e0, branch_a, branch_b])
    assert result.ok is False

from __future__ import annotations

from cassandre_security.acceptance import (
    HARD_BLOCKERS,
    SEC_CONTROL_MAP,
    AcceptanceReport,
    TestOutcome,
    check_hard_blocker_coverage,
)


def _report(outcomes: tuple[TestOutcome, ...]) -> AcceptanceReport:
    return AcceptanceReport(
        schema="cassandre.security-evidence.v1",
        release_digest="sha256:test",
        source_revision="abc123",
        environment_profile="S1_SYNTHETIC",
        scope=("packages/cassandre_security",),
        test_results=outcomes,
        open_findings=(),
        exceptions=(),
    )


def test_blocker_covered_when_all_its_controls_have_a_passing_test():
    outcomes = (TestOutcome(test_id="t1", control_ids=("SEC-05",), observed_result="PASS", observed_at="now", duration_seconds=0.1),)
    coverage = check_hard_blocker_coverage(_report(outcomes))
    assert coverage["self-approval"] is True


def test_blocker_not_covered_if_its_test_failed():
    outcomes = (TestOutcome(test_id="t1", control_ids=("SEC-05",), observed_result="FAIL", observed_at="now", duration_seconds=0.1),)
    coverage = check_hard_blocker_coverage(_report(outcomes))
    assert coverage["self-approval"] is False


def test_blocker_not_covered_if_no_test_at_all():
    coverage = check_hard_blocker_coverage(_report(()))
    assert coverage["self-approval"] is False


def test_blockers_with_no_mapped_controls_are_always_reported_false_not_omitted():
    coverage = check_hard_blocker_coverage(_report(()))
    assert "any settlement transmission route in S1" in coverage
    assert coverage["any settlement transmission route in S1"] is False
    assert "required hybrid downgrade" in coverage
    assert coverage["required hybrid downgrade"] is False


def test_every_hard_blocker_from_the_spec_is_present_in_the_table():
    # Spot-check a representative sample of spec section 15's hard blockers.
    for expected in (
        "self-approval",
        "wrong-hash approval acceptance",
        "altered signature/envelope accepted",
        "revocation resurrected by restore",
        "inventory over-reservation",
        "compromised release admission",
    ):
        assert expected in HARD_BLOCKERS


def test_control_map_only_maps_to_known_sec_ids():
    known_sec_ids = {f"SEC-{i:02d}" for i in range(1, 25)}
    for test_name, control_ids in SEC_CONTROL_MAP.items():
        for control_id in control_ids:
            assert control_id in known_sec_ids, f"{test_name} maps to unknown control {control_id!r}"


def test_report_as_dict_summary_counts_are_correct():
    outcomes = (
        TestOutcome(test_id="t1", control_ids=("SEC-05",), observed_result="PASS", observed_at="now", duration_seconds=0.1),
        TestOutcome(test_id="t2", control_ids=(), observed_result="FAIL", observed_at="now", duration_seconds=0.1),
        TestOutcome(test_id="t3", control_ids=(), observed_result="NOT_RUN", observed_at="now", duration_seconds=0.0),
    )
    d = _report(outcomes).as_dict()
    assert d["summary"]["total"] == 3
    assert d["summary"]["pass"] == 1
    assert d["summary"]["fail"] == 1
    assert d["summary"]["not_run"] == 1
    assert d["summary"]["unmapped_to_a_control"] == 2

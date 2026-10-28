"""Adversarial S1 acceptance evidence report (SEC-21).

Spec section 14.1 defines `cassandre.security-evidence.v1`: a report
binding release/source/environment identity to a list of executed test
results, each with `observed_result: PASS_FAIL_OR_NOT_RUN` -- explicitly
never "PASS" for a test that did not run (spec section 15: "Never mark
NOT_RUN as PASS").

This module runs the REAL test suite via `pytest --junitxml`, parses the
REAL results (pass/fail/skip), and maps each test to the SEC control(s) it
covers via `SEC_CONTROL_MAP` below -- a curated table, not a heuristic
guess, covering the tests this package's own author (this session) wrote
and therefore knows the exact intent of. Tests not in the map appear in
the report with an empty `control_ids` tuple, explicitly marked
unmapped -- never silently omitted and never credited to a control they
don't verify.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# Curated mapping: pytest nodeid test function name -> SEC control IDs it
# verifies. Deliberately partial and explicit rather than a fuzzy
# name-matching heuristic -- every entry here reflects a test this
# package's author actually wrote to prove a specific spec requirement.
SEC_CONTROL_MAP: dict[str, tuple[str, ...]] = {
    # SEC-02 classification
    "test_require_synthetic_provenance_rejects_missing": ("SEC-02",),
    "test_declassify_without_approval_fails_closed_to_highest_input": ("SEC-02",),
    "test_declassify_rejects_same_person_as_both_approvers": ("SEC-02", "SEC-05"),
    # SEC-04 authorization
    "test_unknown_action_denied_deny_by_default": ("SEC-04",),
    "test_missing_role_denied": ("SEC-04",),
    "test_revoked_actor_denied_even_with_correct_role": ("SEC-04", "SEC-14"),
    "test_expired_session_denied": ("SEC-04",),
    "test_decision_is_deterministic_for_identical_inputs": ("SEC-04",),
    # SEC-05 maker-checker
    "test_same_actor_as_maker_and_checker_rejected": ("SEC-05",),
    "test_distinct_account_ids_same_natural_person_rejected": ("SEC-05",),
    "test_independent_dual_approval_rejects_requester_as_approver": ("SEC-05", "SEC-18"),
    # SEC-08 envelope encryption
    "test_tampered_ciphertext_is_rejected": ("SEC-08",),
    "test_wrong_context_object_version_is_rejected_as_tamper": ("SEC-08",),
    "test_wrong_context_tenant_and_object_swap_is_rejected": ("SEC-08",),
    "test_tampered_wrapped_dek_rejected": ("SEC-08",),
    "test_wrong_custody_adapter_cannot_unwrap_another_adapters_dek": ("SEC-08",),
    "test_two_seals_of_the_same_plaintext_use_different_nonces_and_ciphertexts": ("SEC-08",),
    # SEC-09 key ceremony
    "test_below_threshold_shares_fail_closed": ("SEC-09",),
    "test_synthetic_recovery_drill_covers_all_required_scenarios": ("SEC-09",),
    "test_corrupt_share_is_rejected_not_silently_accepted": ("SEC-09",),
    # SEC-10 canonical evidence
    "test_mutated_body_field_invalidates_signature": ("SEC-10",),
    "test_stripped_signature_fails_closed": ("SEC-10",),
    "test_wrong_public_key_fails_verification": ("SEC-10",),
    "test_verify_evidence_with_payload_catches_substituted_payload": ("SEC-10",),
    # SEC-13 ML-DSA / hybrid signatures
    "test_hybrid_dual_signature_seal_and_verify_round_trip": ("SEC-13",),
    "test_hybrid_profile_rejects_stripped_ml_dsa_signature": ("SEC-13",),
    "test_hybrid_profile_rejects_wrong_ml_dsa_key": ("SEC-13",),
    "test_mldsa_verify_fails_for_tampered_signature": ("SEC-13",),
    "test_mldsa_domain_separation_prevents_cross_protocol_replay": ("SEC-13",),
    # SEC-11 crypto inventory
    "test_mlkem_keypair_sizes_match_fips_203_table": ("SEC-11",),
    "test_mldsa_keypair_public_key_size_matches_fips_204_table": ("SEC-11",),
    "test_crypto_capability_report_reports_expected_algorithms": ("SEC-11",),
    # SEC-14 revocation
    "test_stale_presented_epoch_blocked": ("SEC-14",),
    "test_revocation_effective_blocks_new_mutation": ("SEC-14",),
    "test_stale_fence_state_blocks_mutation": ("SEC-14",),
    "test_mutation_committing_first_precedes_effective_local_revocation": ("SEC-14", "SEC-16"),
    # SEC-15 audit/witness/db lockdown
    "test_fork_attempt_different_head_hash_for_same_sequence_rejected": ("SEC-15",),
    "test_gap_in_sequence_rejected": ("SEC-15",),
    "test_freshness_check_blocks_when_stale": ("SEC-15",),
    "test_real_postgres_lockdown_actually_removes_direct_dml": ("SEC-15",),
    # SEC-18 release admission
    "test_admit_release_rejects_single_approver": ("SEC-18",),
    "test_verify_activation_rejects_binary_swap_after_admission": ("SEC-18",),
    "test_verify_activation_rejects_rollback_below_security_floor": ("SEC-18",),
    "test_verify_activation_rejects_stale_running_epoch_even_if_manifest_is_current": ("SEC-18",),
    # SEC-19 backup/restore
    "test_build_coherent_recovery_set_rejects_snapshot_taken_outside_pause_window": ("SEC-19",),
    "test_quarantine_verification_blocks_reopen_if_any_single_check_fails": ("SEC-19",),
    # SEC-20 incident response
    "test_no_degraded_state_other_than_normal_permits_new_publish": ("SEC-20",),
    "test_phases_must_advance_in_order_no_skipping": ("SEC-20",),
    "test_close_incident_requires_independent_checker_not_commander": ("SEC-20",),
    # CollateralOS integration (SEC-16 concurrency proof)
    "test_two_separate_connections_race_to_reserve_the_same_position_under_real_postgres_locking": ("SEC-16",),
    "test_revocation_committing_first_blocks_a_concurrently_attempted_reservation_under_real_postgres_locking": ("SEC-14", "SEC-16"),
}


@dataclass(frozen=True)
class TestOutcome:
    __test__ = False  # tell pytest this dataclass is not a test class despite the name

    test_id: str
    control_ids: tuple[str, ...]
    observed_result: str  # PASS | FAIL | NOT_RUN (skipped)
    observed_at: str
    duration_seconds: float


@dataclass(frozen=True)
class AcceptanceReport:
    schema: str
    release_digest: str
    source_revision: str
    environment_profile: str
    scope: tuple[str, ...]
    test_results: tuple[TestOutcome, ...]
    open_findings: tuple[str, ...]
    exceptions: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "schema": self.schema,
            "release_digest": self.release_digest,
            "source_revision": self.source_revision,
            "environment_profile": self.environment_profile,
            "scope": list(self.scope),
            "test_results": [
                {
                    "test_id": t.test_id,
                    "control_ids": list(t.control_ids),
                    "observed_result": t.observed_result,
                    "observed_at": t.observed_at,
                    "duration_seconds": t.duration_seconds,
                }
                for t in self.test_results
            ],
            "summary": {
                "total": len(self.test_results),
                "pass": sum(1 for t in self.test_results if t.observed_result == "PASS"),
                "fail": sum(1 for t in self.test_results if t.observed_result == "FAIL"),
                "not_run": sum(1 for t in self.test_results if t.observed_result == "NOT_RUN"),
                "unmapped_to_a_control": sum(1 for t in self.test_results if not t.control_ids),
            },
            "open_findings": list(self.open_findings),
            "exceptions": list(self.exceptions),
            "approval": "separate_human_release_decision",
        }


class AcceptanceRunError(RuntimeError):
    pass


def run_pytest_and_generate_report(
    *,
    test_paths: tuple[str, ...],
    repo_root: str,
    release_digest: str,
    source_revision: str,
    env: dict[str, str] | None = None,
    extra_open_findings: tuple[str, ...] = (),
) -> AcceptanceReport:
    """Actually executes pytest (never fabricates results) with a JUnit
    XML report, parses the real output, and maps each test to its SEC
    control ID(s) via `SEC_CONTROL_MAP`. A test not in the map is still
    included with `control_ids=()` -- unmapped, never silently dropped and
    never falsely credited."""

    junit_path = Path(repo_root) / ".cassandre_acceptance_junit.xml"
    cmd = ["uv", "run", "pytest", *test_paths, "-q", f"--junitxml={junit_path}"]

    import os

    full_env = dict(os.environ)
    if env:
        full_env.update(env)

    proc = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, env=full_env, timeout=600)
    if not junit_path.exists():
        raise AcceptanceRunError(f"pytest did not produce a JUnit report; stdout={proc.stdout[-2000:]} stderr={proc.stderr[-2000:]}")

    tree = ET.parse(junit_path)
    root = tree.getroot()
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()

    results: list[TestOutcome] = []
    for testcase in root.iter("testcase"):
        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        test_id = f"{classname}::{name}"
        duration = float(testcase.get("time", "0"))

        if testcase.find("failure") is not None or testcase.find("error") is not None:
            observed_result = "FAIL"
        elif testcase.find("skipped") is not None:
            observed_result = "NOT_RUN"
        else:
            observed_result = "PASS"

        control_ids = SEC_CONTROL_MAP.get(name, ())
        results.append(
            TestOutcome(
                test_id=test_id,
                control_ids=control_ids,
                observed_result=observed_result,
                observed_at=now_iso,
                duration_seconds=duration,
            )
        )

    open_findings = list(extra_open_findings)
    failed = [r.test_id for r in results if r.observed_result == "FAIL"]
    if failed:
        open_findings.append(f"{len(failed)} test(s) failed: {', '.join(failed[:10])}" + (" ..." if len(failed) > 10 else ""))

    junit_path.unlink(missing_ok=True)

    return AcceptanceReport(
        schema="cassandre.security-evidence.v1",
        release_digest=release_digest,
        source_revision=source_revision,
        environment_profile="S1_SYNTHETIC",
        scope=test_paths,
        test_results=tuple(results),
        open_findings=tuple(open_findings),
        exceptions=(
            "No SEC-09 physical ceremony (synthetic drill only)",
            "No SEC-12 full-infrastructure TLS hybrid enforcement",
            "No SEC-22 independent human review",
            "No SEC-23/24 confidential computing or FHE/MPC",
        ),
    )


# Spec section 15's hard-blocker list, mapped to the control(s) whose
# tests are expected to demonstrate that blocker is actually blocked. An
# entry with an empty tuple means this pass has no test proving that
# blocker is closed -- reported honestly, not hidden.
HARD_BLOCKERS: dict[str, tuple[str, ...]] = {
    "production data accessible to developer agents": (),  # organizational control, not testable in-repo
    "any unowned key": ("SEC-09",),
    "unauthorized decrypt/export": ("SEC-04", "SEC-08"),
    "cross-tenant or entity access": ("SEC-04",),
    "self-approval": ("SEC-05",),
    "wrong-hash approval acceptance": ("SEC-10",),
    "altered signature/envelope accepted": ("SEC-10", "SEC-13"),
    "required hybrid downgrade": (),  # SEC-12 full enforcement not built this pass
    "raw content in unauthorized logs": (),  # not covered by a test this pass
    "revocation resurrected by restore": ("SEC-14", "SEC-19"),
    "inaccessible or corrupt recovery": ("SEC-09", "SEC-19"),
    "inventory over-reservation": ("SEC-16",),
    "compromised release admission": ("SEC-18",),
    "any settlement transmission route in S1": (),  # no settlement transport exists in this codebase at all
}


def check_hard_blocker_coverage(report: AcceptanceReport) -> dict[str, bool]:
    """For each hard blocker, True only if every one of its mapped
    controls has at least one PASSing test in this report. A blocker
    mapped to `()` is reported False -- "no test covers this" is the
    honest answer, not silently excluded."""
    covered_controls = {
        control_id
        for outcome in report.test_results
        if outcome.observed_result == "PASS"
        for control_id in outcome.control_ids
    }
    return {
        blocker: bool(controls) and all(c in covered_controls for c in controls)
        for blocker, controls in HARD_BLOCKERS.items()
    }

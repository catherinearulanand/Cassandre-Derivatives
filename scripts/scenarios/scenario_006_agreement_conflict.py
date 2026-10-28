"""SIM-SCENARIO-006 "Agreement conflict" (ClauseOS).

Catalogue expected_control_behavior: "Publication blocked."

Uses the real corpus agreement bundle `SIM-AGR-BUNDLE-010` (`schemas/fixtures/
agreements/index.json` coverage_item: "conflicting clauses requiring
review"). Its CSA text states two different, unreconciled Threshold values
(USD 500,000.00 in clause (a) and USD 750,000.00 in clause (b), "Neither
clause is marked as superseding the other").

This script ingests the real bundle, analyzes it, and -- regardless of
exactly how ClauseOS's deterministic rule proposer resolves the ambiguity
(see the printed assertion list for what it actually extracted this run) --
attempts to publish a policy release without a checker-approved THRESHOLD
term. `clauseos.domain.policy.build_policy_release` requires every
`REQUIRED_MATERIAL_TERM_CONCEPT` (including THRESHOLD) to have a
CHECKER_APPROVED review case bound to the assertion's current evidence hash;
this script deliberately does NOT engineer around that, so publication is
blocked by the same `IncompletePolicyError` mechanism a real conflicting-
clause bundle would trip.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import (  # noqa: E402
    banner,
    check_service_up,
    clauseos_client,
    conclude,
    load_agreement_bundle_document,
    run_suffix,
    step,
)

SCENARIO_ID = "SIM-SCENARIO-006"
SCENARIO_NAME = "Agreement conflict"
EXPECTED = "Publication blocked"

BUNDLE_SOURCE_ID = "SIM-AGR-BUNDLE-010"
MAKER = "maker.alice@sim.cassandre.test"
CHECKER = "checker.bob@sim.cassandre.test"


def main() -> None:
    banner(f"{SCENARIO_ID} {SCENARIO_NAME!r} -- expected: {EXPECTED}")

    if not check_service_up("http://127.0.0.1:8001", "ClauseOS"):
        conclude(SCENARIO_ID, SCENARIO_NAME, False, "ClauseOS (port 8001) is not reachable")

    suffix = run_suffix()
    scenario_run_id = f"SIM-SCEN-CLAUSEOS-{suffix}"

    csa_text = load_agreement_bundle_document(BUNDLE_SOURCE_ID, f"{BUNDLE_SOURCE_ID.lower()}_csa.md")
    print(f"Real corpus document loaded from schemas/fixtures/agreements/{BUNDLE_SOURCE_ID.lower()}/ "
          f"({len(csa_text)} chars) -- it states two unreconciled Threshold clauses:")
    for line in csa_text.splitlines():
        if "Threshold" in line:
            print(f"    {line.strip()}")

    client = clauseos_client()

    step("Create bundle")
    resp = client.post("/v1/bundles", {"scenario_id": scenario_run_id, "actor_id": MAKER})
    if resp.status_code != 200:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"bundle creation failed: {resp.text}")
    bundle_id = resp.json()["bundle_id"]

    step("Ingest the real SIM-AGR-BUNDLE-010 CSA document text")
    resp = client.post(
        f"/v1/bundles/{bundle_id}/documents",
        {"document_name": "CSA", "content": csa_text, "actor_id": MAKER},
    )
    if resp.status_code != 200:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"document ingest failed: {resp.text}")

    step("Approve manifest (no incorporated documents for this bundle)")
    resp = client.post(
        f"/v1/bundles/{bundle_id}/manifest:approve",
        {
            "document_type": "CSA",
            "parties": ["Cassandre Simulation Bank N.A.", "SIM-COUNTERPARTY-KAPPA"],
            "execution_date": "2026-01-15",
            "effective_date": "2026-01-15",
            "incorporated_by_reference": [],
            "actor_id": MAKER,
        },
    )
    if resp.status_code != 200 or resp.json().get("status") != "PARSED":
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"manifest approve failed: {resp.text}")

    step("Analyze")
    resp = client.post(f"/v1/bundles/{bundle_id}:analyze", {"actor_id": MAKER})
    if resp.status_code != 200:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"analyze failed: {resp.text}")
    assertions = resp.json()["assertions"]
    print(f"\nExtracted {len(assertions)} assertions:")
    for a in assertions:
        print(
            f"  concept={a['concept']:<24} status={a['status']:<24} "
            f"conflict_reason={a['conflict_reason']!r} snippet={a['evidence']['snippet']!r}"
        )

    # Deliberately leave THRESHOLD (and anything else not CHECKER_APPROVED)
    # un-reviewed -- a reviewer confronted with an ambiguous/conflicting
    # clause should not manufacture an approval, and this script models that
    # by simply not approving it. We still exercise the maker/checker path on
    # any assertion that genuinely is CONFLICT-free and unambiguous, to keep
    # the bundle realistic, but never approve a THRESHOLD derived from this
    # specific conflicting bundle.
    step("Fetch review cases")
    resp = client.get(f"/v1/bundles/{bundle_id}/reviews")
    reviews = resp.json()["reviews"]
    threshold_assertion_ids = {a["assertion_id"] for a in assertions if a["concept"] == "THRESHOLD"}
    non_threshold_reviews = [r for r in reviews if r["assertion_id"] not in threshold_assertion_ids]

    step("Approve every non-THRESHOLD assertion normally (for a realistic bundle)")
    for review in non_threshold_reviews:
        client.post(f"/v1/reviews/{review['review_id']}/maker-decision", {"actor_id": MAKER, "decision": "APPROVE"})
        client.post(f"/v1/reviews/{review['review_id']}/checker-decision", {"actor_id": CHECKER, "decision": "APPROVE"})

    step("Attempt to publish a policy release -- must be blocked")
    resp = client.post("/v1/policy-releases", {"bundle_id": bundle_id, "actor_id": CHECKER})
    print(f"\npolicy-releases response: HTTP {resp.status_code}")

    blocked = resp.status_code == 422 and resp.json().get("error") == "IncompletePolicyError"

    step("Confirm the bundle never reached PUBLISHED")
    resp = client.get(f"/v1/bundles/{bundle_id}")
    bundle_status = resp.json().get("status")
    not_published = bundle_status != "PUBLISHED"
    print(f"bundle status = {bundle_status!r}")

    passed = blocked and not_published
    reason = "ok" if passed else f"blocked={blocked} bundle_status={bundle_status!r}"
    conclude(SCENARIO_ID, SCENARIO_NAME, passed, reason)


if __name__ == "__main__":
    main()

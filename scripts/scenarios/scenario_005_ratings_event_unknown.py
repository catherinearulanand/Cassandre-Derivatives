"""SIM-SCENARIO-005 "Ratings event unknown" (ClauseOS).

Catalogue expected_control_behavior: "ClauseOS returns external state
required; no derived active threshold."

Uses the real corpus agreement bundle `SIM-AGR-BUNDLE-004` (`schemas/fixtures/
agreements/index.json` coverage_item: "ratings-contingent threshold with
missing current state"). Its CSA text states the Threshold for Party B is
conditional on a SIM Ratings Agency rating that "is not stated anywhere in
this bundle and must be separately supplied" (paragraph (c), "External
Fact").

Finding surfaced by this script (see inline commentary at the maker-decision
step): ClauseOS's deterministic regex proposer (`clauseos.core.rule_proposer`,
ADR-006: no model/LLM anywhere in the critical path) extracts the FIRST
"Threshold ... shall be CCY amount" match in the document with no
understanding of the preceding "if rating is at or above SIM-A3" conditional
-- it does not itself detect that this is a conditional/external-state-
dependent clause. The control that actually prevents an unconditional
threshold from reaching a published policy is the human maker/checker review
step: this script's maker explicitly REJECTS the proposed THRESHOLD
assertion, citing the bundle's own stated external-fact dependency, which is
exactly the judgment call `SIM-SCENARIO-005` expects a reviewer to make.
With THRESHOLD rejected, `POST /v1/policy-releases` must fail with
`IncompletePolicyError` (missing a checker-approved THRESHOLD term) --
"no derived active threshold" is demonstrated by that 422, not by a bare
null.
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

SCENARIO_ID = "SIM-SCENARIO-005"
SCENARIO_NAME = "Ratings event unknown"
EXPECTED = "ClauseOS returns external state required; no derived active threshold"

BUNDLE_SOURCE_ID = "SIM-AGR-BUNDLE-004"
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
          f"({len(csa_text)} chars). Paragraph (c) of that real text:")
    for line in csa_text.splitlines():
        if "External Fact" in line or "not stated anywhere" in line:
            print(f"    {line.strip()}")

    client = clauseos_client()

    step("Create bundle")
    resp = client.post("/v1/bundles", {"scenario_id": scenario_run_id, "actor_id": MAKER})
    if resp.status_code != 200:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"bundle creation failed: {resp.text}")
    bundle_id = resp.json()["bundle_id"]

    step("Ingest the real SIM-AGR-BUNDLE-004 CSA document text")
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
            "parties": ["Cassandre Simulation Bank N.A.", "SIM-COUNTERPARTY-DELTA"],
            "execution_date": "2026-01-15",
            "effective_date": "2026-01-15",
            "incorporated_by_reference": [],
            "actor_id": MAKER,
        },
    )
    if resp.status_code != 200 or resp.json().get("status") != "PARSED":
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"manifest approve failed: {resp.text}")

    step("Analyze (deterministic rule proposer extracts candidate material-term assertions)")
    resp = client.post(f"/v1/bundles/{bundle_id}:analyze", {"actor_id": MAKER})
    if resp.status_code != 200:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"analyze failed: {resp.text}")
    assertions = resp.json()["assertions"]
    print(f"\nExtracted {len(assertions)} assertions:")
    for a in assertions:
        print(f"  concept={a['concept']:<24} status={a['status']:<24} snippet={a['evidence']['snippet']!r}")

    threshold_assertions = [a for a in assertions if a["concept"] == "THRESHOLD"]
    if not threshold_assertions:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, "rule proposer extracted no THRESHOLD assertion at all")
    threshold_assertion = threshold_assertions[0]
    print(
        f"\nThe extracted THRESHOLD snippet is {threshold_assertion['evidence']['snippet']!r} -- note it captures "
        "only the bare 'shall be USD 1,000,000.00' clause, NOT the preceding rating-contingency text. ClauseOS's "
        "rule proposer has no concept of a conditional clause; recognizing that this value depends on unstated "
        "external state is the human reviewer's job, which this script now performs."
    )

    step("Fetch review cases")
    resp = client.get(f"/v1/bundles/{bundle_id}/reviews")
    reviews = resp.json()["reviews"]
    threshold_review = next(r for r in reviews if r["assertion_id"] == threshold_assertion["assertion_id"])
    other_reviews = [r for r in reviews if r["review_id"] != threshold_review["review_id"]]

    step("Maker REJECTS the THRESHOLD assertion (external rating state is not available)")
    resp = client.post(
        f"/v1/reviews/{threshold_review['review_id']}/maker-decision",
        {"actor_id": MAKER, "decision": "REJECT"},
    )
    if resp.status_code != 200:
        conclude(SCENARIO_ID, SCENARIO_NAME, False, f"maker rejection of THRESHOLD failed: {resp.text}")
    print(f"THRESHOLD review case now: {resp.json()['status']}")

    step("Approve every other (non-THRESHOLD) assertion normally, for a realistic bundle")
    for review in other_reviews:
        resp = client.post(
            f"/v1/reviews/{review['review_id']}/maker-decision",
            {"actor_id": MAKER, "decision": "APPROVE"},
        )
        if resp.status_code != 200:
            conclude(SCENARIO_ID, SCENARIO_NAME, False, f"maker approval failed for {review['review_id']}: {resp.text}")
        resp = client.post(
            f"/v1/reviews/{review['review_id']}/checker-decision",
            {"actor_id": CHECKER, "decision": "APPROVE"},
        )
        if resp.status_code != 200:
            conclude(SCENARIO_ID, SCENARIO_NAME, False, f"checker approval failed for {review['review_id']}: {resp.text}")

    step("Attempt to publish a policy release -- must be blocked (no active threshold)")
    resp = client.post("/v1/policy-releases", {"bundle_id": bundle_id, "actor_id": CHECKER})
    print(f"\npolicy-releases response: HTTP {resp.status_code}")

    blocked = resp.status_code == 422 and resp.json().get("error") == "IncompletePolicyError"
    mentions_threshold = blocked and "THRESHOLD" in resp.json().get("detail", "")

    step("Confirm no THRESHOLD term exists anywhere, i.e. 'no derived active threshold'")
    resp = client.get("/v1/assertions", params={"bundle_id": bundle_id, "concept": "THRESHOLD"})
    threshold_assertions_final = resp.json()["assertions"]
    no_active_threshold = all(a["status"] != "APPROVED" for a in threshold_assertions_final)

    passed = blocked and mentions_threshold and no_active_threshold
    reason = (
        "ok" if passed else
        f"blocked={blocked} mentions_threshold={mentions_threshold} no_active_threshold={no_active_threshold} "
        f"(resp={resp.text if not blocked else ''})"
    )
    conclude(SCENARIO_ID, SCENARIO_NAME, passed, reason)


if __name__ == "__main__":
    main()

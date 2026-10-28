"""SIM-003: the synthetic agreement corpus, per plan Section 9.2.

Twelve newly authored bundles, one per item in the plan's Section 9.2 list.
Every bundle is a short, obviously synthetic plain-markdown document -- none
of them reproduce real CSA/ISDA language, and every one is captioned as a
simulation artifact with no execution effect. Exactly five (of the four to
six the plan allows) are designated `RISKOS_CALCULABLE_S1`, all drawn from
one family ("the straightforward bilateral USD CSA family with MTA-before-
rounding") so a bundle never becomes calculable "merely because ClauseOS
extracted its terms" (plan Section 9.2) -- calculability is a property this
generator asserts up front, independent of any extractor.

Every bundle's expected material terms are captured in a `GroundTruth`
record *authored here directly*, not derived by running any extractor over
the markdown text (plan Section 9.2: "Author every expected material
assertion before running any extractor."). Each ground-truth record is
content-hashed with `audit_protocol.canonical.content_hash` for provenance;
that hash is shipped alongside the manifest so any later consumer can detect
if the ground-truth file was altered after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass

from audit_protocol.canonical import content_hash

from simulation_factory.identifiers import require_sim_identifier
from simulation_factory.institution import CASSANDRE_SIM_BANK_NA
from simulation_factory.provenance import SimulationProvenance, make_provenance
from simulation_factory.serialization import to_jsonable

SCENARIO_ID = "SIM-SCENARIO-CORPUS-AGREEMENTS"

CALCULABLE_FAMILY = "SIM-FAMILY-BILATERAL-USD-CSA-MTA-BEFORE-ROUNDING"


@dataclass(frozen=True)
class GroundTruth:
    """Independently authored expected material terms for one bundle (plan
    Section 10.5's material-terms list, restricted to the fields relevant to
    the Section 9.2 coverage item this bundle demonstrates). `None` means
    "this bundle does not resolve this term", which is itself meaningful
    ground truth (e.g. a bundle with a missing incorporated document has no
    resolvable threshold at all)."""

    calculation_party: str
    counterparty: str
    threshold_calculation_party: str | None
    threshold_counterparty: str | None
    threshold_currency: str | None
    minimum_transfer_amount: str | None
    mta_currency: str | None
    independent_amount: str | None
    rounding_order: str | None  # "MTA_BEFORE_ROUNDING" | "ROUNDING_BEFORE_MTA" | None
    notification_deadline_business_days: int | None
    eligible_collateral: tuple[str, ...]
    rating_contingent_threshold: bool
    rating_state_known: bool
    amendment_overrides_eligible_collateral: bool
    side_letter_changes_notification: bool
    has_conflicting_clauses: bool
    missing_incorporated_document: bool
    wrong_way_issuer_exclusion: bool
    expected_bundle_status: str
    notes: str


@dataclass(frozen=True)
class AgreementBundle:
    bundle_id: str
    title: str
    coverage_item: str
    family: str | None
    riskos_calculable: bool
    documents: tuple[tuple[str, str], ...]  # (document_name, markdown_text)
    ground_truth: GroundTruth
    ground_truth_content_hash: str
    provenance: SimulationProvenance

    def __post_init__(self) -> None:
        require_sim_identifier(self.bundle_id, field_name="bundle_id")
        if self.riskos_calculable and self.family != CALCULABLE_FAMILY:
            raise ValueError(
                f"{self.bundle_id} is marked riskos_calculable but is not in "
                f"the selected calculable family {CALCULABLE_FAMILY!r}"
            )


def _party_a() -> str:
    return f"{CASSANDRE_SIM_BANK_NA.display_name} (\"Party A\")"


def _preamble(bundle_id: str, title: str, counterparty_label: str) -> str:
    return (
        f"SYNTHETIC CREDIT SUPPORT ANNEX -- {title.upper()}\n"
        f"Agreement ID: {bundle_id}\n\n"
        "This is a wholly fictional, simulation-only document authored for the "
        "Cassandre Platform S1 controlled simulation. It does not reproduce any "
        "real institution's agreement form, is not legal advice, and creates no "
        "real legal obligation.\n\n"
        f"Parties: {_party_a()} and {counterparty_label} (\"Party B\").\n\n"
        "Execution Date: 2026-01-15\n"
        "Effective Date: 2026-01-15\n\n"
        "Paragraph 13. Elections and Variables\n\n"
    )


def _make(
    *,
    seq: int,
    title: str,
    coverage_item: str,
    counterparty: str,
    body: str,
    ground_truth: GroundTruth,
    riskos_calculable: bool,
    extra_documents: tuple[tuple[str, str], ...] = (),
) -> AgreementBundle:
    bundle_id = f"SIM-AGR-BUNDLE-{seq:03d}"
    text = _preamble(bundle_id, title, counterparty) + body
    ghash = content_hash(to_jsonable(ground_truth))
    return AgreementBundle(
        bundle_id=bundle_id,
        title=title,
        coverage_item=coverage_item,
        family=CALCULABLE_FAMILY if riskos_calculable else None,
        riskos_calculable=riskos_calculable,
        documents=((f"{bundle_id.lower()}_csa.md", text),) + extra_documents,
        ground_truth=ground_truth,
        ground_truth_content_hash=ghash,
        provenance=make_provenance(SCENARIO_ID),
    )


def build_agreement_bundles() -> tuple[AgreementBundle, ...]:
    bundles: list[AgreementBundle] = []

    # 1. Straightforward threshold/MTA -- RISKOS_CALCULABLE_S1
    bundles.append(
        _make(
            seq=1,
            title="Straightforward Threshold and MTA",
            coverage_item="straightforward threshold/MTA",
            counterparty="SIM-COUNTERPARTY-ALPHA",
            body=(
                "(a) Threshold. The Threshold for each party shall be USD 500,000.00.\n\n"
                "(b) Minimum Transfer Amount. The Minimum Transfer Amount for each party "
                "shall be USD 50,000.00, applied before rounding.\n\n"
                "(c) Notification. A party disputing a Demand shall notify the other "
                "party within 2 Business Days of receipt.\n\n"
                "(d) Eligible Collateral. Cash USD and US Treasury securities.\n"
            ),
            ground_truth=GroundTruth(
                calculation_party="Party A", counterparty="Party B",
                threshold_calculation_party="500000.00", threshold_counterparty="500000.00",
                threshold_currency="USD",
                minimum_transfer_amount="50000.00", mta_currency="USD",
                independent_amount="0.00",
                rounding_order="MTA_BEFORE_ROUNDING",
                notification_deadline_business_days=2,
                eligible_collateral=("CASH_USD", "US_TREASURY"),
                rating_contingent_threshold=False, rating_state_known=True,
                amendment_overrides_eligible_collateral=False,
                side_letter_changes_notification=False,
                has_conflicting_clauses=False, missing_incorporated_document=False,
                wrong_way_issuer_exclusion=False,
                expected_bundle_status="ANALYZED",
                notes="Baseline calculable bundle: symmetric threshold, one MTA, MTA-before-rounding.",
            ),
            riskos_calculable=True,
        )
    )

    # 2. Zero threshold -- RISKOS_CALCULABLE_S1
    bundles.append(
        _make(
            seq=2,
            title="Zero Threshold",
            coverage_item="zero threshold",
            counterparty="SIM-COUNTERPARTY-BETA",
            body=(
                "(a) Threshold. The Threshold for each party shall be USD 0.00 (zero).\n\n"
                "(b) Minimum Transfer Amount. The Minimum Transfer Amount for each party "
                "shall be USD 25,000.00, applied before rounding.\n\n"
                "(c) Notification. A party disputing a Demand shall notify the other "
                "party within 2 Business Days of receipt.\n\n"
                "(d) Eligible Collateral. Cash USD only.\n"
            ),
            ground_truth=GroundTruth(
                calculation_party="Party A", counterparty="Party B",
                threshold_calculation_party="0.00", threshold_counterparty="0.00",
                threshold_currency="USD",
                minimum_transfer_amount="25000.00", mta_currency="USD",
                independent_amount="0.00",
                rounding_order="MTA_BEFORE_ROUNDING",
                notification_deadline_business_days=2,
                eligible_collateral=("CASH_USD",),
                rating_contingent_threshold=False, rating_state_known=True,
                amendment_overrides_eligible_collateral=False,
                side_letter_changes_notification=False,
                has_conflicting_clauses=False, missing_incorporated_document=False,
                wrong_way_issuer_exclusion=False,
                expected_bundle_status="ANALYZED",
                notes="Zero threshold means every net exposure above MTA is called; not a missing/UNKNOWN value.",
            ),
            riskos_calculable=True,
        )
    )

    # 3. Asymmetric bilateral thresholds -- RISKOS_CALCULABLE_S1
    bundles.append(
        _make(
            seq=3,
            title="Asymmetric Bilateral Thresholds",
            coverage_item="asymmetric bilateral thresholds",
            counterparty="SIM-COUNTERPARTY-GAMMA",
            body=(
                "(a) Threshold. The Threshold for Party A shall be USD 1,000,000.00. "
                "The Threshold for Party B shall be USD 250,000.00.\n\n"
                "(b) Minimum Transfer Amount. The Minimum Transfer Amount for each party "
                "shall be USD 50,000.00, applied before rounding.\n\n"
                "(c) Notification. A party disputing a Demand shall notify the other "
                "party within 2 Business Days of receipt.\n\n"
                "(d) Eligible Collateral. Cash USD and US Treasury securities.\n"
            ),
            ground_truth=GroundTruth(
                calculation_party="Party A", counterparty="Party B",
                threshold_calculation_party="1000000.00", threshold_counterparty="250000.00",
                threshold_currency="USD",
                minimum_transfer_amount="50000.00", mta_currency="USD",
                independent_amount="0.00",
                rounding_order="MTA_BEFORE_ROUNDING",
                notification_deadline_business_days=2,
                eligible_collateral=("CASH_USD", "US_TREASURY"),
                rating_contingent_threshold=False, rating_state_known=True,
                amendment_overrides_eligible_collateral=False,
                side_letter_changes_notification=False,
                has_conflicting_clauses=False, missing_incorporated_document=False,
                wrong_way_issuer_exclusion=False,
                expected_bundle_status="ANALYZED",
                notes="Party A and Party B thresholds must never be swapped or averaged; each is a distinct typed field.",
            ),
            riskos_calculable=True,
        )
    )

    # 4. Ratings-contingent threshold with missing current state -- NOT calculable
    bundles.append(
        _make(
            seq=4,
            title="Ratings-Contingent Threshold, Current State Missing",
            coverage_item="ratings-contingent threshold with missing current state",
            counterparty="SIM-COUNTERPARTY-DELTA",
            body=(
                "(a) Threshold. If Party B's long-term senior unsecured debt rating from "
                "SIM Ratings Agency is at or above SIM-A3, the Threshold for Party B "
                "shall be USD 1,000,000.00; otherwise the Threshold for Party B shall be "
                "USD 0.00. The Threshold for Party A shall be USD 1,000,000.00.\n\n"
                "(b) Minimum Transfer Amount. USD 50,000.00 for each party.\n\n"
                "(c) External Fact. Party B's current SIM Ratings Agency rating is not "
                "stated anywhere in this bundle and must be separately supplied.\n"
            ),
            ground_truth=GroundTruth(
                calculation_party="Party A", counterparty="Party B",
                threshold_calculation_party="1000000.00", threshold_counterparty=None,
                threshold_currency="USD",
                minimum_transfer_amount="50000.00", mta_currency="USD",
                independent_amount="0.00",
                rounding_order="MTA_BEFORE_ROUNDING",
                notification_deadline_business_days=2,
                eligible_collateral=("CASH_USD",),
                rating_contingent_threshold=True, rating_state_known=False,
                amendment_overrides_eligible_collateral=False,
                side_letter_changes_notification=False,
                has_conflicting_clauses=False, missing_incorporated_document=False,
                wrong_way_issuer_exclusion=False,
                expected_bundle_status="REVIEW_REQUIRED",
                notes=(
                    "threshold_counterparty is None (UNKNOWN), never defaulted to the "
                    "base contractual value, because the current rating state is an "
                    "external fact this bundle does not supply (EXTERNAL_STATE_REQUIRED)."
                ),
            ),
            riskos_calculable=False,
        )
    )

    # 5. Currency-specific MTA -- RISKOS_CALCULABLE_S1
    bundles.append(
        _make(
            seq=5,
            title="Currency-Specific Minimum Transfer Amount",
            coverage_item="currency-specific MTA",
            counterparty="SIM-COUNTERPARTY-EPSILON",
            body=(
                "(a) Threshold. The Threshold for each party shall be USD 500,000.00.\n\n"
                "(b) Minimum Transfer Amount. The Minimum Transfer Amount shall be "
                "USD 50,000.00 if the Transfer is to be made in USD, or EUR 45,000.00 if "
                "the Transfer is to be made in EUR, applied before rounding.\n\n"
                "(c) Notification. 2 Business Days.\n\n"
                "(d) Eligible Collateral. Cash USD and Cash EUR.\n"
            ),
            ground_truth=GroundTruth(
                calculation_party="Party A", counterparty="Party B",
                threshold_calculation_party="500000.00", threshold_counterparty="500000.00",
                threshold_currency="USD",
                minimum_transfer_amount="50000.00", mta_currency="USD",
                independent_amount="0.00",
                rounding_order="MTA_BEFORE_ROUNDING",
                notification_deadline_business_days=2,
                eligible_collateral=("CASH_USD", "CASH_EUR"),
                rating_contingent_threshold=False, rating_state_known=True,
                amendment_overrides_eligible_collateral=False,
                side_letter_changes_notification=False,
                has_conflicting_clauses=False, missing_incorporated_document=False,
                wrong_way_issuer_exclusion=False,
                expected_bundle_status="ANALYZED",
                notes=(
                    "There is a second, EUR 45000.00 MTA for EUR transfers; the ground "
                    "truth here records the USD-transfer MTA as the primary calculable "
                    "value and the EUR figure only in notes, since call_currency for "
                    "this bundle's calculable scenario is USD."
                ),
            ),
            riskos_calculable=True,
        )
    )

    # 6. MTA-before-rounding, explicit -- RISKOS_CALCULABLE_S1
    bundles.append(
        _make(
            seq=6,
            title="MTA Before Rounding (Explicit Election)",
            coverage_item="MTA-before-rounding",
            counterparty="SIM-COUNTERPARTY-ZETA",
            body=(
                "(a) Threshold. USD 500,000.00 for each party.\n\n"
                "(b) Minimum Transfer Amount. USD 50,000.00 for each party. The parties "
                "expressly elect that the Minimum Transfer Amount test is applied BEFORE "
                "rounding of the Delivery Amount or Return Amount.\n\n"
                "(c) Rounding. Any amount so calculated shall be rounded up to the "
                "nearest integral multiple of USD 10,000.00.\n"
            ),
            ground_truth=GroundTruth(
                calculation_party="Party A", counterparty="Party B",
                threshold_calculation_party="500000.00", threshold_counterparty="500000.00",
                threshold_currency="USD",
                minimum_transfer_amount="50000.00", mta_currency="USD",
                independent_amount="0.00",
                rounding_order="MTA_BEFORE_ROUNDING",
                notification_deadline_business_days=2,
                eligible_collateral=("CASH_USD",),
                rating_contingent_threshold=False, rating_state_known=True,
                amendment_overrides_eligible_collateral=False,
                side_letter_changes_notification=False,
                has_conflicting_clauses=False, missing_incorporated_document=False,
                wrong_way_issuer_exclusion=False,
                expected_bundle_status="ANALYZED",
                notes="Explicit MTA-before-rounding election, matching METHOD-PACK-001's provisional convention (DEC-RSK-01).",
            ),
            riskos_calculable=True,
        )
    )

    # 7. Rounding-before-MTA, expressly elected -- NOT calculable (unsupported election)
    bundles.append(
        _make(
            seq=7,
            title="Rounding Before MTA (Expressly Elected)",
            coverage_item="rounding-before-MTA where expressly elected",
            counterparty="SIM-COUNTERPARTY-ETA",
            body=(
                "(a) Threshold. USD 500,000.00 for each party.\n\n"
                "(b) Minimum Transfer Amount. USD 50,000.00 for each party. The parties "
                "expressly elect that any calculated Delivery Amount or Return Amount is "
                "rounded FIRST, and the Minimum Transfer Amount test is applied to the "
                "rounded figure.\n"
            ),
            ground_truth=GroundTruth(
                calculation_party="Party A", counterparty="Party B",
                threshold_calculation_party="500000.00", threshold_counterparty="500000.00",
                threshold_currency="USD",
                minimum_transfer_amount="50000.00", mta_currency="USD",
                independent_amount="0.00",
                rounding_order="ROUNDING_BEFORE_MTA",
                notification_deadline_business_days=2,
                eligible_collateral=("CASH_USD",),
                rating_contingent_threshold=False, rating_state_known=True,
                amendment_overrides_eligible_collateral=False,
                side_letter_changes_notification=False,
                has_conflicting_clauses=False, missing_incorporated_document=False,
                wrong_way_issuer_exclusion=False,
                expected_bundle_status="ANALYZED",
                notes=(
                    "Same family as bundle 006 but the opposite rounding-order election. "
                    "The one method pack this slice implements is MTA-before-rounding "
                    "only, so this bundle must resolve to UNSUPPORTED_AGREEMENT/"
                    "METHOD_UNRESOLVED in RiskOS even though ClauseOS can extract its "
                    "terms cleanly -- 'a bundle does not become calculable merely "
                    "because ClauseOS extracted its terms' (plan Section 9.2)."
                ),
            ),
            riskos_calculable=False,
        )
    )

    # 8. Amendment overriding eligible collateral -- NOT calculable
    bundles.append(
        _make(
            seq=8,
            title="Amendment Overriding Eligible Collateral",
            coverage_item="amendment overriding eligible collateral",
            counterparty="SIM-COUNTERPARTY-THETA",
            body=(
                "(a) Threshold. USD 500,000.00 for each party.\n\n"
                "(b) Minimum Transfer Amount. USD 50,000.00 for each party.\n\n"
                "(c) Eligible Collateral (original). Cash USD, Cash EUR, and US Treasury "
                "securities.\n"
            ),
            ground_truth=GroundTruth(
                calculation_party="Party A", counterparty="Party B",
                threshold_calculation_party="500000.00", threshold_counterparty="500000.00",
                threshold_currency="USD",
                minimum_transfer_amount="50000.00", mta_currency="USD",
                independent_amount="0.00",
                rounding_order="MTA_BEFORE_ROUNDING",
                notification_deadline_business_days=2,
                eligible_collateral=("CASH_USD",),  # post-amendment: EUR and UST removed
                rating_contingent_threshold=False, rating_state_known=True,
                amendment_overrides_eligible_collateral=True,
                side_letter_changes_notification=False,
                has_conflicting_clauses=False, missing_incorporated_document=False,
                wrong_way_issuer_exclusion=False,
                expected_bundle_status="ANALYZED",
                notes=(
                    "The amendment document narrows eligible collateral to Cash USD "
                    "only; the ground-truth eligible_collateral tuple reflects the "
                    "post-amendment set, and amendment precedence over the original CSA "
                    "is itself a material, maker-checker-approved term (plan Section "
                    "10.5: 'amendment precedence')."
                ),
            ),
            riskos_calculable=False,
            extra_documents=(
                (
                    "sim-agr-bundle-008_amendment_001.md",
                    "AMENDMENT NO. 1 (SYNTHETIC)\n\n"
                    "This synthetic amendment to SIM-AGR-BUNDLE-008 is incorporated by "
                    "reference and amends Paragraph 13(c). Effective as of 2026-03-01, "
                    "Eligible Collateral under this Agreement is narrowed to Cash USD "
                    "only; Cash EUR and US Treasury securities are no longer eligible "
                    "collateral for either party.\n",
                ),
            ),
        )
    )

    # 9. Side letter changing notification deadline -- NOT calculable
    bundles.append(
        _make(
            seq=9,
            title="Side Letter Changing Notification Deadline",
            coverage_item="side letter changing notification deadline",
            counterparty="SIM-COUNTERPARTY-IOTA",
            body=(
                "(a) Threshold. USD 500,000.00 for each party.\n\n"
                "(b) Minimum Transfer Amount. USD 50,000.00 for each party.\n\n"
                "(c) Notification. A party disputing a Demand shall notify the other "
                "party within 2 Business Days of receipt (original CSA term).\n"
            ),
            ground_truth=GroundTruth(
                calculation_party="Party A", counterparty="Party B",
                threshold_calculation_party="500000.00", threshold_counterparty="500000.00",
                threshold_currency="USD",
                minimum_transfer_amount="50000.00", mta_currency="USD",
                independent_amount="0.00",
                rounding_order="MTA_BEFORE_ROUNDING",
                notification_deadline_business_days=1,  # side letter changes 2 -> 1
                eligible_collateral=("CASH_USD",),
                rating_contingent_threshold=False, rating_state_known=True,
                amendment_overrides_eligible_collateral=False,
                side_letter_changes_notification=True,
                has_conflicting_clauses=False, missing_incorporated_document=False,
                wrong_way_issuer_exclusion=False,
                expected_bundle_status="ANALYZED",
                notes="Side letter shortens the notification deadline from 2 to 1 Business Day; ground truth reflects the side letter, not the original CSA text.",
            ),
            riskos_calculable=False,
            extra_documents=(
                (
                    "sim-agr-bundle-009_side_letter.md",
                    "SIDE LETTER (SYNTHETIC)\n\n"
                    "This synthetic side letter to SIM-AGR-BUNDLE-009 is incorporated by "
                    "reference. Notwithstanding Paragraph 13(c) of the CSA, a party "
                    "disputing a Demand shall notify the other party within 1 Business "
                    "Day of receipt of that Demand.\n",
                ),
            ),
        )
    )

    # 10. Conflicting clauses requiring review -- NOT calculable
    bundles.append(
        _make(
            seq=10,
            title="Conflicting Clauses Requiring Review",
            coverage_item="conflicting clauses requiring review",
            counterparty="SIM-COUNTERPARTY-KAPPA",
            body=(
                "(a) Threshold. Paragraph 13(a) states the Threshold for each party "
                "shall be USD 500,000.00.\n\n"
                "(b) Threshold (restated). Paragraph 13(a)(ii), added later in the same "
                "execution copy, states the Threshold for each party shall be USD "
                "750,000.00. Neither clause is marked as superseding the other and "
                "there is no amendment record establishing precedence.\n\n"
                "(c) Minimum Transfer Amount. USD 50,000.00 for each party.\n"
            ),
            ground_truth=GroundTruth(
                calculation_party="Party A", counterparty="Party B",
                threshold_calculation_party=None, threshold_counterparty=None,
                threshold_currency="USD",
                minimum_transfer_amount="50000.00", mta_currency="USD",
                independent_amount="0.00",
                rounding_order="MTA_BEFORE_ROUNDING",
                notification_deadline_business_days=2,
                eligible_collateral=("CASH_USD",),
                rating_contingent_threshold=False, rating_state_known=True,
                amendment_overrides_eligible_collateral=False,
                side_letter_changes_notification=False,
                has_conflicting_clauses=True, missing_incorporated_document=False,
                wrong_way_issuer_exclusion=False,
                expected_bundle_status="REVIEW_REQUIRED",
                notes="Two irreconcilable Threshold clauses (500000.00 vs 750000.00) with no stated precedence: threshold fields are None (CONFLICT), never averaged or defaulted to either value.",
            ),
            riskos_calculable=False,
        )
    )

    # 11. Missing incorporated document -- NOT calculable, BUNDLE_INCOMPLETE
    bundles.append(
        _make(
            seq=11,
            title="Missing Incorporated Document",
            coverage_item="missing incorporated document causing BUNDLE_INCOMPLETE",
            counterparty="SIM-COUNTERPARTY-LAMBDA",
            body=(
                "(a) Threshold. USD 500,000.00 for each party.\n\n"
                "(b) Minimum Transfer Amount. USD 50,000.00 for each party.\n\n"
                "(c) Incorporated Documents. This Agreement incorporates by reference "
                "the document named \"Confirmation Annex A\", which forms part of this "
                "Agreement bundle and must be read together with this Annex. That "
                "document is not included in this bundle.\n"
            ),
            ground_truth=GroundTruth(
                calculation_party="Party A", counterparty="Party B",
                threshold_calculation_party=None, threshold_counterparty=None,
                threshold_currency=None,
                minimum_transfer_amount=None, mta_currency=None,
                independent_amount=None,
                rounding_order=None,
                notification_deadline_business_days=None,
                eligible_collateral=(),
                rating_contingent_threshold=False, rating_state_known=True,
                amendment_overrides_eligible_collateral=False,
                side_letter_changes_notification=False,
                has_conflicting_clauses=False, missing_incorporated_document=True,
                wrong_way_issuer_exclusion=False,
                expected_bundle_status="BUNDLE_INCOMPLETE",
                notes="'Confirmation Annex A' is declared incorporated by reference but never supplied; the bundle cannot become analyzable (plan Section 10.1/10.2) and every material term is None, not a best-effort guess.",
            ),
            riskos_calculable=False,
        )
    )

    # 12. Wrong-way issuer exclusion and concentration -- NOT calculable
    bundles.append(
        _make(
            seq=12,
            title="Wrong-Way Issuer Exclusion and Concentration",
            coverage_item="wrong-way issuer exclusion and concentration rules",
            counterparty="SIM-COUNTERPARTY-MU",
            body=(
                "(a) Threshold. USD 500,000.00 for each party.\n\n"
                "(b) Minimum Transfer Amount. USD 50,000.00 for each party.\n\n"
                "(c) Eligible Collateral. Cash USD, US Treasury securities, and "
                "corporate bonds issued by entities other than Party B, an affiliate of "
                "Party B, or a guarantor of Party B's obligations under this Agreement "
                "(wrong-way exclusion).\n\n"
                "(d) Concentration. No single issuer group's securities may exceed 25% "
                "of the aggregate value of collateral delivered by Party B.\n"
            ),
            ground_truth=GroundTruth(
                calculation_party="Party A", counterparty="Party B",
                threshold_calculation_party="500000.00", threshold_counterparty="500000.00",
                threshold_currency="USD",
                minimum_transfer_amount="50000.00", mta_currency="USD",
                independent_amount="0.00",
                rounding_order="MTA_BEFORE_ROUNDING",
                notification_deadline_business_days=2,
                eligible_collateral=("CASH_USD", "US_TREASURY", "CORPORATE_BOND"),
                rating_contingent_threshold=False, rating_state_known=True,
                amendment_overrides_eligible_collateral=False,
                side_letter_changes_notification=False,
                has_conflicting_clauses=False, missing_incorporated_document=False,
                wrong_way_issuer_exclusion=True,
                expected_bundle_status="ANALYZED",
                notes="Wrong-way exclusion and a 25% single-issuer-group concentration cap are both deterministic approved mappings, not a market-correlation inference (plan Section 12.3).",
            ),
            riskos_calculable=False,
        )
    )

    calculable = [b for b in bundles if b.riskos_calculable]
    if not (4 <= len(calculable) <= 6):
        raise ValueError(
            f"expected 4-6 RISKOS_CALCULABLE_S1 bundles, got {len(calculable)}"
        )
    if not (8 <= len(bundles) <= 12):
        raise ValueError(f"expected 8-12 agreement bundles, got {len(bundles)}")
    ids = [b.bundle_id for b in bundles]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate bundle_id in agreement corpus")
    return tuple(bundles)

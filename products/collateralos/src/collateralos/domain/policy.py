"""`PolicyBundle` / `EligibilityRule` encoding of `POLICY-BUNDLE-001`.

Status: this bundle is **PROVISIONAL** (`docs/DECISION_REGISTER.md`
`DEC-COL-01`) -- a synthetic placeholder convention for the S1 slice, not an
approved production eligibility policy. Every numeric constant here is
traceable to `docs/controls/POLICY-BUNDLE-001-provisional.md` and
`docs/controls/wrong-way-map-001.json`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from collateralos.domain.identifiers import require_sim_identifier
from collateralos.domain.status import PolicyBundleStatus


class AssetClass(str, Enum):
    CASH_USD = "CASH_USD"
    CASH_EUR = "CASH_EUR"
    US_TREASURY = "US_TREASURY"
    CORPORATE_BOND = "CORPORATE_BOND"
    EQUITY = "EQUITY"
    SOVEREIGN_BOND = "SOVEREIGN_BOND"
    GOLD = "GOLD"


# POLICY-BUNDLE-001: "Eligible asset classes: CASH_USD, CASH_EUR, US_TREASURY."
ELIGIBLE_ASSET_CLASSES: frozenset[AssetClass] = frozenset(
    {AssetClass.CASH_USD, AssetClass.CASH_EUR, AssetClass.US_TREASURY}
)

# POLICY-BUNDLE-001 haircuts: adjusted_value = market_value * (1 - haircut).
DEFAULT_HAIRCUTS: dict[AssetClass, Decimal] = {
    AssetClass.CASH_USD: Decimal("0.00"),
    AssetClass.CASH_EUR: Decimal("0.02"),
    AssetClass.US_TREASURY: Decimal("0.02"),
}

# POLICY-BUNDLE-001: "Minimum lot: USD 1,000 cash increments; UST in $1,000
# face increments." Keyed by asset class; value is the smallest deliverable
# increment expressed in the position's own quantity unit.
DEFAULT_MIN_LOT: dict[AssetClass, Decimal] = {
    AssetClass.CASH_USD: Decimal("1000"),
    AssetClass.CASH_EUR: Decimal("1000"),
    AssetClass.US_TREASURY: Decimal("1000"),
}

DEFAULT_CONCENTRATION_CAP_PCT = Decimal("0.40")
# DEC-OPS-02: 0.01% conservative coverage tolerance.
DEFAULT_COVERAGE_TOLERANCE_PCT = Decimal("0.0001")
# DEC-COL-02: 5s wall-clock / 1% relative gap accepted as FEASIBLE_BOUNDED_GAP.
DEFAULT_SOLVER_TIME_LIMIT_SECONDS = Decimal("5")
DEFAULT_SOLVER_GAP_TOLERANCE_PCT = Decimal("0.01")


@dataclass(frozen=True)
class PolicyBundle:
    policy_id: str
    version: str
    status: PolicyBundleStatus
    eligible_asset_classes: frozenset[AssetClass] = field(default_factory=lambda: ELIGIBLE_ASSET_CLASSES)
    haircuts: dict[AssetClass, Decimal] = field(default_factory=lambda: dict(DEFAULT_HAIRCUTS))
    min_lot: dict[AssetClass, Decimal] = field(default_factory=lambda: dict(DEFAULT_MIN_LOT))
    concentration_cap_pct: Decimal = DEFAULT_CONCENTRATION_CAP_PCT
    coverage_tolerance_pct: Decimal = DEFAULT_COVERAGE_TOLERANCE_PCT
    solver_time_limit_seconds: Decimal = DEFAULT_SOLVER_TIME_LIMIT_SECONDS
    solver_gap_tolerance_pct: Decimal = DEFAULT_SOLVER_GAP_TOLERANCE_PCT
    # counterparty_id -> excluded issuer group IDs, from wrong-way-map-001.json
    wrong_way_map: dict[str, frozenset[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_sim_identifier(self.policy_id, field_name="policy_id")
        for asset_class in self.eligible_asset_classes:
            if asset_class not in self.haircuts:
                raise ValueError(f"eligible asset class {asset_class} has no declared haircut")

    def haircut_for(self, asset_class: AssetClass) -> Decimal:
        haircut = self.haircuts.get(asset_class)
        if haircut is None:
            raise ValueError(f"no haircut declared for asset class {asset_class}")
        return haircut

    def excluded_issuer_groups_for(self, counterparty_id: str) -> frozenset[str]:
        return self.wrong_way_map.get(counterparty_id, frozenset())


def load_wrong_way_map(raw: dict) -> dict[str, frozenset[str]]:
    """Build the `wrong_way_map` dict from the parsed contents of
    `docs/controls/wrong-way-map-001.json`. Pure transform, no file I/O here
    -- the caller reads/parses the JSON and passes the dict in, keeping this
    module free of filesystem dependencies."""
    result: dict[str, frozenset[str]] = {}
    for mapping in raw.get("mappings", []):
        result[mapping["counterparty_id"]] = frozenset(mapping["excluded_issuer_groups"])
    return result


def build_policy_bundle_sim_001(*, status: PolicyBundleStatus, wrong_way_map: dict[str, frozenset[str]]) -> PolicyBundle:
    """Construct the one provisional policy bundle this slice implements
    (`SIM-POLICY-001`, DEC-COL-01)."""
    return PolicyBundle(
        policy_id="SIM-POLICY-001",
        version="1.0.0",
        status=status,
        wrong_way_map=wrong_way_map,
    )

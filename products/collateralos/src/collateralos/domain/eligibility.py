"""Eligibility compiler (plan Section 12.3, work item COL-005).

Every position returns either ELIGIBLE or a complete ORDERED rejection-reason
list -- never a bare boolean. This module performs the *static*, per-position
checks only (asset class, wrong-way, self/affiliate-issued, encumbrance,
recall, staleness). Post-transfer concentration is an allocation-level check
against a candidate set of delivered lines plus existing held collateral
(plan Section 12.3: "test limits on the post-transfer pool, not only the
proposed delivery") and lives in `collateralos.domain.verifier`, because it
cannot be evaluated for one position in isolation.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from collateralos.domain.inventory import InventoryPosition
from collateralos.domain.policy import PolicyBundle, PolicyBundleStatus
from collateralos.domain.status import RejectionReason


@dataclass(frozen=True)
class EligibilityResult:
    position_id: str
    eligible: bool
    rejection_reasons: tuple[RejectionReason, ...]


def compile_eligibility(
    position: InventoryPosition,
    *,
    policy: PolicyBundle,
    counterparty_id: str,
    as_of: dt.datetime,
) -> EligibilityResult:
    reasons: list[RejectionReason] = []

    if policy.status is not PolicyBundleStatus.ACTIVE:
        # Fail closed (ADR-014): an unapproved/unresolved policy cannot make
        # anything eligible, regardless of the position's own attributes.
        reasons.append(RejectionReason.POLICY_NOT_ACTIVE)

    if position.asset_class not in policy.eligible_asset_classes:
        reasons.append(RejectionReason.ASSET_CLASS_NOT_ELIGIBLE)

    excluded_groups = policy.excluded_issuer_groups_for(counterparty_id)
    if position.issuer_group_id is not None and position.issuer_group_id in excluded_groups:
        reasons.append(RejectionReason.WRONG_WAY_EXCLUDED)

    if position.is_self_issued:
        reasons.append(RejectionReason.SELF_ISSUED)

    if position.is_affiliate_issued:
        reasons.append(RejectionReason.AFFILIATE_ISSUED)

    if position.is_encumbered:
        reasons.append(RejectionReason.ENCUMBERED)

    if position.is_recalled:
        reasons.append(RejectionReason.RECALLED)

    if position.is_stale(as_of):
        reasons.append(RejectionReason.STALE_PRICE)

    return EligibilityResult(
        position_id=position.position_id,
        eligible=len(reasons) == 0,
        rejection_reasons=tuple(reasons),
    )


def compile_eligibility_matrix(
    positions: tuple[InventoryPosition, ...],
    *,
    policy: PolicyBundle,
    counterparty_id: str,
    as_of: dt.datetime,
) -> dict[str, EligibilityResult]:
    return {
        p.position_id: compile_eligibility(p, policy=policy, counterparty_id=counterparty_id, as_of=as_of)
        for p in positions
    }

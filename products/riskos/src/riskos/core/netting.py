"""Resolve calculation entity, counterparty, agreement, and netting set from
approved masters, per plan Section 11.3 step 3 / Section 9.3: "Zero or
multiple matches block the entire affected netting-set calculation."
"""

from __future__ import annotations

from dataclasses import dataclass, field

from riskos.domain.entities import NettingSetVersion, TradeMTM


@dataclass(frozen=True)
class NettingSetResolutionResult:
    ok: bool
    resolved: NettingSetVersion | None
    match_count: int
    blocking_reason: str | None = None


def resolve_netting_set(
    *,
    calculation_entity_id: str,
    counterparty_entity_id: str,
    candidates: tuple[NettingSetVersion, ...],
) -> NettingSetResolutionResult:
    matches = [
        ns
        for ns in candidates
        if ns.calculation_entity_id == calculation_entity_id
        and ns.counterparty_entity_id == counterparty_entity_id
        and ns.approved
    ]
    if len(matches) == 0:
        return NettingSetResolutionResult(
            ok=False,
            resolved=None,
            match_count=0,
            blocking_reason=(
                f"zero approved netting-set matches for calculation_entity_id="
                f"{calculation_entity_id!r} counterparty_entity_id={counterparty_entity_id!r}"
            ),
        )
    if len(matches) > 1:
        return NettingSetResolutionResult(
            ok=False,
            resolved=None,
            match_count=len(matches),
            blocking_reason=(
                f"{len(matches)} approved netting-set matches for calculation_entity_id="
                f"{calculation_entity_id!r} counterparty_entity_id={counterparty_entity_id!r}; "
                "ambiguous resolution, blocking"
            ),
        )
    return NettingSetResolutionResult(ok=True, resolved=matches[0], match_count=1)


@dataclass(frozen=True)
class TradeMembershipResult:
    """Every accepted trade must resolve to exactly this one netting set;
    booking location, branch, calculation agent, collateral provider,
    custodian, and account are separate identities never inferred from a
    display name (plan Section 9.3) — this slice only checks the identity
    fields it actually models (entity/counterparty/netting-set)."""

    ok: bool
    mismatched_trade_ids: tuple[str, ...] = field(default_factory=tuple)


def verify_trade_membership(
    trades: tuple[TradeMTM, ...], *, netting_set: NettingSetVersion
) -> TradeMembershipResult:
    mismatched = tuple(
        t.trade_id
        for t in trades
        if t.calculation_entity_id != netting_set.calculation_entity_id
        or t.counterparty_entity_id != netting_set.counterparty_entity_id
        or t.netting_set_id != netting_set.netting_set_id
    )
    return TradeMembershipResult(ok=not mismatched, mismatched_trade_ids=mismatched)

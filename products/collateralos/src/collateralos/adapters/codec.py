"""to-row/from-row mapping between the pure domain objects in
`collateralos.domain` and the SQLAlchemy rows in
`collateralos.adapters.models` (work item COL-014's persistence half).

Every domain object here is immutable (`@dataclass(frozen=True)`, or a
pydantic `Money`/`Quantity`), so each `*_from_row` function reconstructs a
fresh instance rather than mutating anything -- the only mutable state that
lives across calls is the ORM row itself, and each `*_to_row`/`*_to_json`
function is the single place that decides how a domain object's fields
become columns or a JSON blob.

Money/quantity amounts are always round-tripped as `Decimal` (never through
`float`), matching ADR-005 and the same invariant `adapters.models` already
enforces with `Numeric` columns. JSON blobs (`positions_json`,
`evidence_json`, `definition_json`, `approval_json`, the export envelope
wrapper) store every Decimal as its plain string form and reconstruct it
with `Decimal(...)`, never `json`'s own float parsing.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from money_types import Money, Quantity

from audit_protocol import AuditEvent

from collateralos.adapters.models import (
    AllocationLineRow,
    AuditEventRow,
    InventoryBalanceRow,
    InventorySnapshotRow,
    ObligationRow,
    PlanRow,
    PolicyBundleRow,
    SettlementInstructionRow,
)
from collateralos.domain.approval import PlanApproval
from collateralos.domain.inventory import InventoryPosition, InventorySnapshot
from collateralos.domain.obligation import Obligation
from collateralos.domain.plan import AllocationLine, Plan, SolverEvidence
from collateralos.domain.policy import AssetClass, PolicyBundle
from collateralos.domain.reservation import InventoryBalanceState
from collateralos.domain.settlement import SettlementInstruction
from collateralos.domain.status import (
    ApprovalStatus,
    ObligationDirection,
    ObligationStatus,
    PolicyBundleStatus,
    SolverStatus,
)

# --- Obligation -----------------------------------------------------------


def obligation_to_row(obligation: Obligation) -> ObligationRow:
    return ObligationRow(
        obligation_id=obligation.obligation_id,
        counterparty_id=obligation.counterparty_id,
        entity_id=obligation.entity_id,
        agreement_id=obligation.agreement_id,
        direction=obligation.direction.value,
        amount=obligation.amount.amount,
        currency=obligation.amount.currency,
        due_at=obligation.due_at,
        source=obligation.source,
        version=obligation.version,
        status=obligation.status.value,
    )


def obligation_from_row(row: ObligationRow) -> Obligation:
    # `rounding_context` is not modeled as an ObligationRow column (it never
    # feeds obligation.content_hash_fields() or any planning arithmetic), so
    # it round-trips as the fixed convention every obligation in this slice
    # actually uses ("obligation") rather than being silently invented as
    # something that looks preserved but isn't.
    return Obligation(
        obligation_id=row.obligation_id,
        counterparty_id=row.counterparty_id,
        entity_id=row.entity_id,
        agreement_id=row.agreement_id,
        direction=ObligationDirection(row.direction),
        amount=Money(amount=str(row.amount), currency=row.currency, rounding_context="obligation"),
        due_at=row.due_at,
        source=row.source,
        version=row.version,
        status=ObligationStatus(row.status),
    )


# --- PolicyBundle -----------------------------------------------------------


def policy_to_row(policy: PolicyBundle) -> PolicyBundleRow:
    definition = {
        "eligible_asset_classes": sorted(ac.value for ac in policy.eligible_asset_classes),
        "haircuts": {ac.value: str(v) for ac, v in policy.haircuts.items()},
        "min_lot": {ac.value: str(v) for ac, v in policy.min_lot.items()},
        "concentration_cap_pct": str(policy.concentration_cap_pct),
        "coverage_tolerance_pct": str(policy.coverage_tolerance_pct),
        "solver_time_limit_seconds": str(policy.solver_time_limit_seconds),
        "solver_gap_tolerance_pct": str(policy.solver_gap_tolerance_pct),
        "wrong_way_map": {cpty: sorted(groups) for cpty, groups in policy.wrong_way_map.items()},
    }
    return PolicyBundleRow(
        policy_id=policy.policy_id,
        version=policy.version,
        status=policy.status.value,
        definition_json=json.dumps(definition),
    )


def policy_from_row(row: PolicyBundleRow) -> PolicyBundle:
    definition = json.loads(row.definition_json)
    return PolicyBundle(
        policy_id=row.policy_id,
        version=row.version,
        status=PolicyBundleStatus(row.status),
        eligible_asset_classes=frozenset(AssetClass(v) for v in definition["eligible_asset_classes"]),
        haircuts={AssetClass(k): Decimal(v) for k, v in definition["haircuts"].items()},
        min_lot={AssetClass(k): Decimal(v) for k, v in definition["min_lot"].items()},
        concentration_cap_pct=Decimal(definition["concentration_cap_pct"]),
        coverage_tolerance_pct=Decimal(definition["coverage_tolerance_pct"]),
        solver_time_limit_seconds=Decimal(definition["solver_time_limit_seconds"]),
        solver_gap_tolerance_pct=Decimal(definition["solver_gap_tolerance_pct"]),
        wrong_way_map={cpty: frozenset(groups) for cpty, groups in definition["wrong_way_map"].items()},
    )


# --- InventoryPosition / InventorySnapshot / InventoryBalanceState --------


def _position_to_dict(position: InventoryPosition) -> dict:
    return {
        "position_id": position.position_id,
        "owner_entity_id": position.owner_entity_id,
        "account_id": position.account_id,
        "location": position.location,
        "asset_class": position.asset_class.value,
        "issuer_group_id": position.issuer_group_id,
        "currency": position.currency,
        "market_value": {"amount": str(position.market_value.amount), "currency": position.market_value.currency},
        "quantity": {"amount": str(position.quantity.amount), "unit": position.quantity.unit},
        "price_as_of": position.price_as_of.astimezone(dt.timezone.utc).isoformat(),
        "stale_after": position.stale_after.astimezone(dt.timezone.utc).isoformat(),
        "is_encumbered": position.is_encumbered,
        "is_recalled": position.is_recalled,
        "is_self_issued": position.is_self_issued,
        "is_affiliate_issued": position.is_affiliate_issued,
    }


def _position_from_dict(data: dict) -> InventoryPosition:
    return InventoryPosition(
        position_id=data["position_id"],
        owner_entity_id=data["owner_entity_id"],
        account_id=data["account_id"],
        location=data["location"],
        asset_class=AssetClass(data["asset_class"]),
        issuer_group_id=data["issuer_group_id"],
        currency=data["currency"],
        market_value=Money(
            amount=data["market_value"]["amount"], currency=data["market_value"]["currency"], rounding_context="market"
        ),
        quantity=Quantity(amount=data["quantity"]["amount"], unit=data["quantity"]["unit"]),
        price_as_of=dt.datetime.fromisoformat(data["price_as_of"]),
        stale_after=dt.datetime.fromisoformat(data["stale_after"]),
        is_encumbered=data["is_encumbered"],
        is_recalled=data["is_recalled"],
        is_self_issued=data["is_self_issued"],
        is_affiliate_issued=data["is_affiliate_issued"],
    )


def snapshot_to_row(snapshot: InventorySnapshot) -> InventorySnapshotRow:
    return InventorySnapshotRow(
        snapshot_id=snapshot.snapshot_id,
        version=snapshot.version,
        as_of=snapshot.as_of,
        positions_json=json.dumps([_position_to_dict(p) for p in snapshot.positions]),
    )


def positions_from_snapshot_row(row: InventorySnapshotRow) -> tuple[InventoryPosition, ...]:
    return tuple(_position_from_dict(d) for d in json.loads(row.positions_json))


def snapshot_from_row(row: InventorySnapshotRow) -> InventorySnapshot:
    return InventorySnapshot(
        snapshot_id=row.snapshot_id,
        version=row.version,
        as_of=row.as_of,
        positions=positions_from_snapshot_row(row),
    )


def balance_state_to_row(state: InventoryBalanceState, *, snapshot_id: str) -> InventoryBalanceRow:
    return InventoryBalanceRow(
        position_id=state.position_id,
        snapshot_id=snapshot_id,
        gross_settled=state.gross_settled,
        encumbered=state.encumbered,
        pending_outbound=state.pending_outbound,
        blocked_or_recalled=state.blocked_or_recalled,
        reserved=state.reserved,
        version=state.version,
    )


def balance_row_to_state(row: InventoryBalanceRow) -> InventoryBalanceState:
    return InventoryBalanceState(
        position_id=row.position_id,
        gross_settled=row.gross_settled,
        encumbered=row.encumbered,
        pending_outbound=row.pending_outbound,
        blocked_or_recalled=row.blocked_or_recalled,
        reserved=row.reserved,
        version=row.version,
    )


# --- Plan / AllocationLine -------------------------------------------------


def plan_to_row(plan: Plan) -> PlanRow:
    evidence = plan.evidence
    evidence_payload = {
        # Denormalized alongside the solver evidence itself (plan Section
        # 12.4 step 9's evidence record) rather than adding dedicated
        # obligation_version/policy_version/inventory_snapshot_version
        # columns to `PlanRow` -- exactly the pattern this table's own
        # `evidence_json` column already exists for.
        "obligation_version": plan.obligation_version,
        "policy_version": plan.policy_version,
        "inventory_snapshot_version": plan.inventory_snapshot_version,
        "solver_evidence": {
            "method": evidence.method,
            "solver_build": evidence.solver_build,
            "status": evidence.status.value,
            "objective_value": evidence.objective_value,
            "gap_pct": evidence.gap_pct,
            "runtime_seconds": evidence.runtime_seconds,
            "time_limit_seconds": evidence.time_limit_seconds,
            "gap_tolerance_pct": evidence.gap_tolerance_pct,
            "verified": evidence.verified,
            "verifier_notes": list(evidence.verifier_notes),
        },
    }
    return PlanRow(
        plan_id=plan.plan_id,
        obligation_id=plan.obligation_id,
        policy_id=plan.policy_id,
        inventory_snapshot_id=plan.inventory_snapshot_id,
        status=plan.status.value,
        plan_hash=plan.plan_hash(),
        evidence_json=json.dumps(evidence_payload),
    )


def allocation_line_to_row(plan_id: str, line: AllocationLine) -> AllocationLineRow:
    return AllocationLineRow(
        plan_id=plan_id,
        position_id=line.position_id,
        delivered_quantity=line.delivered_quantity.amount,
        delivered_unit=line.delivered_quantity.unit,
        adjusted_value=line.adjusted_value.amount,
        adjusted_value_currency=line.adjusted_value.currency,
    )


def allocation_line_from_row(row: AllocationLineRow) -> AllocationLine:
    return AllocationLine(
        position_id=row.position_id,
        delivered_quantity=Quantity(amount=str(row.delivered_quantity), unit=row.delivered_unit),
        adjusted_value=Money(
            amount=str(row.adjusted_value), currency=row.adjusted_value_currency, rounding_context="candidate"
        ),
    )


def plan_from_row(row: PlanRow, line_rows: list[AllocationLineRow]) -> Plan:
    evidence_payload = json.loads(row.evidence_json)
    se = evidence_payload["solver_evidence"]
    evidence = SolverEvidence(
        method=se["method"],
        solver_build=se["solver_build"],
        status=SolverStatus(se["status"]),
        objective_value=se["objective_value"],
        gap_pct=se["gap_pct"],
        runtime_seconds=se["runtime_seconds"],
        time_limit_seconds=se["time_limit_seconds"],
        gap_tolerance_pct=se["gap_tolerance_pct"],
        verified=se["verified"],
        verifier_notes=tuple(se["verifier_notes"]),
    )
    return Plan(
        plan_id=row.plan_id,
        obligation_id=row.obligation_id,
        obligation_version=evidence_payload["obligation_version"],
        policy_id=row.policy_id,
        policy_version=evidence_payload["policy_version"],
        inventory_snapshot_id=row.inventory_snapshot_id,
        inventory_snapshot_version=evidence_payload["inventory_snapshot_version"],
        lines=tuple(allocation_line_from_row(r) for r in line_rows),
        status=SolverStatus(row.status),
        evidence=evidence,
    )


# --- SettlementInstruction --------------------------------------------------


def instruction_from_row(
    row: SettlementInstructionRow, *, plan_hash: str, obligation_id: str, lines: tuple[AllocationLine, ...]
) -> SettlementInstruction:
    # `plan_hash`/`obligation_id`/`lines` are always exactly the owning
    # plan's (store.reserve_plan constructs the instruction that way) --
    # there are no independent columns for them on `settlement_instruction`
    # beyond the already-computed `instruction_hash`, so the caller supplies
    # them from the plan it already looked up rather than this module
    # re-deriving or re-querying `allocation_line` a second time.
    return SettlementInstruction(
        instruction_id=row.instruction_id,
        plan_id=row.plan_id,
        plan_hash=plan_hash,
        reservation_id=row.reservation_id,
        obligation_id=obligation_id,
        lines=lines,
    )


# --- PlanApproval (stored as JSON on ReservationRow.approval_json) --------


def approval_to_json(approval: PlanApproval) -> str:
    return json.dumps(
        {
            "approval_id": approval.approval_id,
            "plan_id": approval.plan_id,
            "reservation_id": approval.reservation_id,
            "maker_id": approval.maker_id,
            "checker_id": approval.checker_id,
            "binding_hash": approval.binding_hash,
            "status": approval.status.value,
        }
    )


def approval_from_json(raw: str) -> PlanApproval:
    data = json.loads(raw)
    return PlanApproval(
        approval_id=data["approval_id"],
        plan_id=data["plan_id"],
        reservation_id=data["reservation_id"],
        maker_id=data["maker_id"],
        checker_id=data["checker_id"],
        binding_hash=data["binding_hash"],
        status=ApprovalStatus(data["status"]),
    )


# --- AuditEvent -------------------------------------------------------------


def audit_event_to_row(event: AuditEvent) -> AuditEventRow:
    return AuditEventRow(
        event_id=event.event_id,
        sequence=event.sequence,
        product=event.product,
        aggregate_id=event.aggregate_id,
        actor_id=event.actor_id,
        actor_type=event.actor_type,
        action=event.action,
        reason_code=event.reason_code,
        object_version=event.object_version,
        object_content_hash=event.object_content_hash,
        event_timestamp=event.event_timestamp,
        previous_event_hash=event.previous_event_hash,
        event_hash=event.event_hash,
        trace_id=event.trace_id,
        simulation_provenance_json=json.dumps(event.simulation_provenance),
    )


def audit_event_from_row(row: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        event_id=row.event_id,
        sequence=row.sequence,
        product=row.product,
        aggregate_id=row.aggregate_id,
        actor_id=row.actor_id,
        actor_type=row.actor_type,
        action=row.action,
        reason_code=row.reason_code,
        object_version=row.object_version,
        object_content_hash=row.object_content_hash,
        event_timestamp=row.event_timestamp,
        previous_event_hash=row.previous_event_hash,
        simulation_provenance=json.loads(row.simulation_provenance_json),
        trace_id=row.trace_id,
        event_hash=row.event_hash,
    )

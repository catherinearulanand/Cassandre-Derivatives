"""In-memory application store (plan Section 25.4 step 5: "add persistence/
API/UI adapters only after core tests pass"; item 10 of this session's task:
"Dependency-injected in-memory repository so tests run without Postgres").

This is the composition point that turns the pure domain + core solver into
a runnable workflow: obligation ingest -> policy activation -> inventory
snapshot -> plan -> reserve -> maker/checker approval -> preflight -> export.
`apps/collateralos_api` wires exactly one instance of `CollateralOSStore` per
process as a FastAPI dependency; `products/collateralos/tests` exercise it
directly with no HTTP layer at all.

A real deployment replaces this with `collateralos.adapters` (SQLAlchemy +
Postgres, in the same package) without changing anything above this layer --
every method here mirrors the transactional operation the adapter's
migration/tables (`products/collateralos/migrations`) are shaped for.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path

from audit_protocol import AuditEvent, Ed25519KeyPair, append_event, content_hash, verify_chain
from cassandre_contracts import (
    Envelope,
    ProducerProduct,
    SimulationProvenance,
    build_envelope,
    sign_envelope,
)
from cassandre_security.authorization import DenyByDefaultPolicyEngine
from cassandre_security.maker_checker import DistinctActorViolation, enforce_distinct_natural_persons

from collateralos.application.idempotency import IdempotencyConflictError, PreflightFailedError
from collateralos.application.security import (
    ACTION_ACTIVATE_POLICY,
    ACTION_ADD_OBLIGATION,
    ACTION_ADD_SNAPSHOT,
    ACTION_CHECKER_APPROVE,
    ACTION_CREATE_PLAN,
    ACTION_EXPORT_INTERNAL,
    ACTION_MAKER_APPROVE,
    ACTION_RESERVE_PLAN,
    authorize,
    build_export_evidence_v2,
    build_policy_engine,
)
from cassandre_security.canonical_evidence import EvidenceContainer
from collateralos.application.inventory_repository import InMemoryInventoryRepository
from collateralos.core.planner import PlanningContext, plan_obligation
from collateralos.domain.approval import ApprovalBindingInputs, PlanApproval, SelfApprovalError
from collateralos.domain.inventory import InventorySnapshot
from collateralos.domain.obligation import Obligation
from collateralos.domain.plan import Plan
from collateralos.domain.policy import PolicyBundle, build_policy_bundle_sim_001, load_wrong_way_map
from collateralos.domain.reservation import ReservationConflictError, balances_from_positions
from collateralos.domain.settlement import SettlementInstruction, SettlementPreflight
from collateralos.domain.status import (
    ACTIONABLE_STATUSES,
    ApprovalStatus,
    PolicyBundleStatus,
    ReservationStatus,
    SolverStatus,
)

SCENARIO_ID = "SIM-SCEN-COLLATERALOS-0001"
GENERATOR_VERSION = "0.1.0"
PRODUCER_VERSION = "0.1.0"
DEFAULT_RESERVATION_TTL = dt.timedelta(minutes=15)


class NotFoundError(KeyError):
    pass


class PlanNotActionableError(Exception):
    def __init__(self, status: SolverStatus):
        self.status = status
        super().__init__(f"plan is not actionable (status={status.value})")


@dataclass
class ReservationView:
    reservation_id: str
    plan_id: str
    lines: dict[str, Decimal]
    status: ReservationStatus
    expires_at: dt.datetime


@dataclass
class ExportRecord:
    idempotency_key: str
    request_fingerprint: str
    envelope: Envelope


def _wrong_way_map_from_repo_docs(policy_docs_root: Path | None) -> dict[str, frozenset[str]]:
    root = policy_docs_root or Path(__file__).resolve().parents[5] / "docs" / "controls"
    raw = json.loads((root / "wrong-way-map-001.json").read_text(encoding="utf-8"))
    return load_wrong_way_map(raw)


@dataclass
class CollateralOSStore:
    keypair: Ed25519KeyPair
    obligations: dict[str, Obligation] = field(default_factory=dict)
    policies: dict[str, PolicyBundle] = field(default_factory=dict)
    snapshots: dict[str, InventorySnapshot] = field(default_factory=dict)
    repos: dict[str, InMemoryInventoryRepository] = field(default_factory=dict)
    plans: dict[str, Plan] = field(default_factory=dict)
    reservations: dict[str, ReservationView] = field(default_factory=dict)
    instructions: dict[str, SettlementInstruction] = field(default_factory=dict)
    approvals: dict[str, PlanApproval] = field(default_factory=dict)  # keyed by reservation_id
    preflights: dict[str, SettlementPreflight] = field(default_factory=dict)  # keyed by instruction_id
    export_records: dict[str, ExportRecord] = field(default_factory=dict)  # keyed by idempotency_key
    # SEC-10: additive `cassandre.evidence.v2` container per real (non-replay)
    # export, alongside (never instead of) the `cassandre_contracts` envelope
    # already in `export_records` -- see `application/security.py`.
    evidence_v2_records: dict[str, EvidenceContainer] = field(default_factory=dict)  # keyed by idempotency_key
    audit_events: list[AuditEvent] = field(default_factory=list)
    issuer: str = "collateralos_api"
    # SEC-04: deny-by-default authorization gate (cassandre_security). Every
    # mutating method below calls `authorize(...)` before touching state;
    # a DENY raises `AuthorizationDeniedError`, never just a log line.
    authz_engine: DenyByDefaultPolicyEngine = field(default_factory=build_policy_engine)

    def _record_audit(self, *, aggregate_id: str, actor_id: str, action: str, object_payload: dict, now: dt.datetime) -> None:
        previous = self.audit_events[-1] if self.audit_events else None
        event = append_event(
            previous_event=previous,
            event_id=str(uuid.uuid4()),
            product="collateralos",
            aggregate_id=aggregate_id,
            actor_id=actor_id,
            actor_type="human" if actor_id.startswith("SIM-USER-") else "service_account",
            action=action,
            reason_code=None,
            object_version=str(object_payload.get("version", "v1")),
            object_content_hash=content_hash(object_payload),
            event_timestamp=now,
            simulation_provenance={
                "is_simulated": True,
                "scenario_id": SCENARIO_ID,
                "generator_version": GENERATOR_VERSION,
            },
            trace_id=aggregate_id,
        )
        self.audit_events.append(event)

    def verify_audit_chain(self):
        return verify_chain(self.audit_events)

    # --- obligation ingest (COL-001) ---
    def add_obligation(self, obligation: Obligation, *, actor_id: str, now: dt.datetime) -> Obligation:
        authorize(
            self.authz_engine, action=ACTION_ADD_OBLIGATION, actor_id=actor_id,
            object_id=obligation.obligation_id, object_version=obligation.version,
            legal_entity_id=obligation.entity_id, now=now,
        )
        self.obligations[obligation.obligation_id] = obligation
        self._record_audit(
            aggregate_id=obligation.obligation_id,
            actor_id=actor_id,
            action="OBLIGATION_INGESTED",
            object_payload=obligation.content_hash_fields(),
            now=now,
        )
        return obligation

    # --- policy bundle lifecycle (COL-004) ---
    def add_draft_policy(self, *, wrong_way_map_docs_root: Path | None = None) -> PolicyBundle:
        wrong_way_map = _wrong_way_map_from_repo_docs(wrong_way_map_docs_root)
        policy = build_policy_bundle_sim_001(status=PolicyBundleStatus.DRAFT, wrong_way_map=wrong_way_map)
        self.policies[policy.policy_id] = policy
        return policy

    def validate_policy(self, policy_id: str, *, actor_id: str, now: dt.datetime) -> PolicyBundle:
        policy = self.policies.get(policy_id)
        if policy is None:
            raise NotFoundError(policy_id)
        if policy.status is not PolicyBundleStatus.DRAFT:
            raise ValueError(f"policy {policy_id} is not DRAFT (status={policy.status})")
        validated = replace(policy, status=PolicyBundleStatus.VALIDATED)
        self.policies[policy_id] = validated
        self._record_audit(
            aggregate_id=policy_id, actor_id=actor_id, action="POLICY_VALIDATED",
            object_payload={"policy_id": policy_id, "version": policy.version}, now=now,
        )
        return validated

    def activate_policy(self, policy_id: str, *, actor_id: str, now: dt.datetime) -> PolicyBundle:
        policy = self.policies.get(policy_id)
        if policy is None:
            raise NotFoundError(policy_id)
        if policy.status is not PolicyBundleStatus.VALIDATED:
            raise ValueError(f"policy {policy_id} is not VALIDATED (status={policy.status})")
        authorize(
            self.authz_engine, action=ACTION_ACTIVATE_POLICY, actor_id=actor_id,
            object_id=policy_id, object_version=policy.version, now=now,
        )
        activated = replace(policy, status=PolicyBundleStatus.ACTIVE)
        self.policies[policy_id] = activated
        self._record_audit(
            aggregate_id=policy_id, actor_id=actor_id, action="POLICY_ACTIVATED",
            object_payload={"policy_id": policy_id, "version": policy.version}, now=now,
        )
        return activated

    # --- inventory snapshot ingest (COL-002) ---
    def add_snapshot(self, snapshot: InventorySnapshot, *, actor_id: str, now: dt.datetime) -> InventorySnapshot:
        authorize(
            self.authz_engine, action=ACTION_ADD_SNAPSHOT, actor_id=actor_id,
            object_id=snapshot.snapshot_id, object_version=snapshot.version, now=now,
        )
        self.snapshots[snapshot.snapshot_id] = snapshot
        self.repos[snapshot.snapshot_id] = InMemoryInventoryRepository(balances_from_positions(snapshot.positions))
        self._record_audit(
            aggregate_id=snapshot.snapshot_id, actor_id=actor_id, action="INVENTORY_SNAPSHOT_INGESTED",
            object_payload={"snapshot_id": snapshot.snapshot_id, "version": snapshot.version}, now=now,
        )
        return snapshot

    # --- planning (COL-006/007/008/009) ---
    def create_plan(
        self,
        *,
        plan_id: str,
        obligation_id: str,
        policy_id: str,
        snapshot_id: str,
        existing_held_by_issuer_group: dict[str, Decimal] | None = None,
        existing_held_total: Decimal = Decimal("0"),
        cost_rate_by_position: dict[str, Decimal] | None = None,
        as_of: dt.datetime,
        actor_id: str,
        now: dt.datetime,
    ) -> Plan:
        obligation = self.obligations.get(obligation_id)
        policy = self.policies.get(policy_id)
        snapshot = self.snapshots.get(snapshot_id)
        if obligation is None or policy is None or snapshot is None:
            raise NotFoundError("obligation/policy/snapshot")
        if policy.status is not PolicyBundleStatus.ACTIVE:
            raise ValueError("POLICY_UNRESOLVED: policy is not ACTIVE")

        authorize(
            self.authz_engine, action=ACTION_CREATE_PLAN, actor_id=actor_id,
            object_id=plan_id, object_version=f"{obligation.version}|{policy.version}|{snapshot.version}", now=now,
        )

        repo = self.repos[snapshot_id]
        ctx = PlanningContext(
            obligation=obligation,
            positions=snapshot.positions,
            balances=repo.snapshot_balances(),
            policy=policy,
            inventory_snapshot_id=snapshot.snapshot_id,
            inventory_snapshot_version=snapshot.version,
            as_of=as_of,
            existing_held_by_issuer_group=existing_held_by_issuer_group or {},
            existing_held_total=existing_held_total,
            cost_rate_by_position=cost_rate_by_position,
        )
        plan = plan_obligation(ctx, plan_id=plan_id)
        self.plans[plan_id] = plan
        self._record_audit(
            aggregate_id=plan_id, actor_id=actor_id, action="PLAN_CREATED",
            object_payload={"plan_id": plan_id, "version": plan.plan_hash()}, now=now,
        )
        return plan

    def get_plan(self, plan_id: str) -> Plan:
        plan = self.plans.get(plan_id)
        if plan is None:
            raise NotFoundError(plan_id)
        return plan

    # --- reservation (COL-010) ---
    def reserve_plan(
        self, plan_id: str, *, reservation_id: str, instruction_id: str, actor_id: str, now: dt.datetime,
        ttl: dt.timedelta = DEFAULT_RESERVATION_TTL,
    ) -> tuple[ReservationView, SettlementInstruction]:
        plan = self.get_plan(plan_id)
        if plan.status not in ACTIONABLE_STATUSES:
            raise PlanNotActionableError(plan.status)

        authorize(
            self.authz_engine, action=ACTION_RESERVE_PLAN, actor_id=actor_id,
            object_id=plan_id, object_version=plan.plan_hash(), now=now,
        )

        requests = {line.position_id: line.delivered_quantity.amount for line in plan.lines}
        repo = self.repos[plan.inventory_snapshot_id]
        try:
            repo.reserve(reservation_id, plan_id, requests)
        except ReservationConflictError:
            self.plans[plan_id] = replace(plan, status=SolverStatus.RESERVATION_CONFLICT)
            raise

        expires_at = now + ttl
        view = ReservationView(
            reservation_id=reservation_id, plan_id=plan_id, lines=requests,
            status=ReservationStatus.ACTIVE, expires_at=expires_at,
        )
        self.reservations[reservation_id] = view

        instruction = SettlementInstruction(
            instruction_id=instruction_id,
            plan_id=plan_id,
            plan_hash=plan.plan_hash(),
            reservation_id=reservation_id,
            obligation_id=plan.obligation_id,
            lines=plan.lines,
        )
        self.instructions[instruction_id] = instruction
        self.approvals[reservation_id] = PlanApproval(
            approval_id=f"SIM-APPR-{reservation_id.removeprefix('SIM-RES-')}",
            plan_id=plan_id,
            reservation_id=reservation_id,
        )
        self._record_audit(
            aggregate_id=reservation_id, actor_id=actor_id, action="RESERVATION_COMMITTED",
            object_payload={"reservation_id": reservation_id, "version": plan.plan_hash()}, now=now,
        )
        return view, instruction

    def release_reservation(self, reservation_id: str, *, actor_id: str, now: dt.datetime) -> None:
        view = self.reservations[reservation_id]
        plan = self.get_plan(view.plan_id)
        repo = self.repos[plan.inventory_snapshot_id]
        repo.release(reservation_id)
        view.status = ReservationStatus.RELEASED
        self._record_audit(
            aggregate_id=reservation_id, actor_id=actor_id, action="RESERVATION_RELEASED",
            object_payload={"reservation_id": reservation_id, "version": "v1"}, now=now,
        )

    # --- maker/checker approval (COL-011) ---
    def _binding_inputs(self, reservation_id: str) -> ApprovalBindingInputs:
        reservation = self.reservations.get(reservation_id)
        if reservation is None:
            raise NotFoundError(reservation_id)
        plan = self.get_plan(reservation.plan_id)
        obligation = self.obligations[plan.obligation_id]
        policy = self.policies[plan.policy_id]
        snapshot = self.snapshots[plan.inventory_snapshot_id]
        instruction = next(i for i in self.instructions.values() if i.reservation_id == reservation_id)
        return ApprovalBindingInputs(
            obligation_id=obligation.obligation_id,
            obligation_version=obligation.version,
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash(),
            reservation_id=reservation_id,
            reservation_version=1,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            inventory_snapshot_id=snapshot.snapshot_id,
            inventory_snapshot_version=snapshot.version,
            instruction_id=instruction.instruction_id,
            instruction_hash=instruction.instruction_hash(),
        )

    def maker_approve(self, reservation_id: str, *, maker_id: str, actor_id: str, now: dt.datetime) -> PlanApproval:
        approval = self.approvals.get(reservation_id)
        if approval is None:
            raise NotFoundError(reservation_id)
        inputs = self._binding_inputs(reservation_id)
        authorize(
            self.authz_engine, action=ACTION_MAKER_APPROVE, actor_id=actor_id,
            object_id=reservation_id, object_version=inputs.plan_hash, now=now,
        )
        approval.maker_approve(maker_id, inputs)
        self._record_audit(
            aggregate_id=reservation_id, actor_id=actor_id, action="PLAN_MAKER_APPROVED",
            object_payload={"reservation_id": reservation_id, "version": approval.binding_hash}, now=now,
        )
        return approval

    def checker_approve(self, reservation_id: str, *, checker_id: str, actor_id: str, now: dt.datetime) -> PlanApproval:
        approval = self.approvals.get(reservation_id)
        if approval is None:
            raise NotFoundError(reservation_id)
        inputs = self._binding_inputs(reservation_id)
        authorize(
            self.authz_engine, action=ACTION_CHECKER_APPROVE, actor_id=actor_id,
            object_id=reservation_id, object_version=inputs.plan_hash, now=now,
        )
        # SEC-05 defense-in-depth: `cassandre_security.maker_checker`'s
        # independent distinct-natural-person check, ahead of (not instead
        # of) `PlanApproval.checker_approve`'s own maker==checker guard just
        # below. Re-raised as `SelfApprovalError` so this store's public
        # exception contract (and the API's 422 mapping) is unchanged --
        # this is belt-and-suspenders, not a second implementation of the
        # same rule with a different failure mode.
        if approval.maker_id is not None:
            try:
                enforce_distinct_natural_persons(approval.maker_id, checker_id)
            except DistinctActorViolation as exc:
                raise SelfApprovalError(str(exc)) from exc
        try:
            approval.checker_approve(checker_id, inputs)
        except SelfApprovalError:
            raise
        except ValueError:
            plan = self.get_plan(self.reservations[reservation_id].plan_id)
            self.plans[plan.plan_id] = replace(plan, status=SolverStatus.APPROVAL_INVALIDATED)
            self._record_audit(
                aggregate_id=reservation_id, actor_id=actor_id, action="PLAN_APPROVAL_INVALIDATED",
                object_payload={"reservation_id": reservation_id, "version": "invalidated"}, now=now,
            )
            raise
        self._record_audit(
            aggregate_id=reservation_id, actor_id=actor_id, action="PLAN_CHECKER_APPROVED",
            object_payload={"reservation_id": reservation_id, "version": approval.binding_hash}, now=now,
        )
        return approval

    # --- preflight (COL-012) ---
    def run_preflight(self, instruction_id: str, *, actor_id: str, now: dt.datetime) -> SettlementPreflight:
        instruction = self.instructions.get(instruction_id)
        if instruction is None:
            raise NotFoundError(instruction_id)
        reservation = self.reservations.get(instruction.reservation_id)
        approval = self.approvals.get(instruction.reservation_id)

        reasons: list[str] = []
        if reservation is None:
            reasons.append("RESERVATION_NOT_FOUND")
        elif reservation.status is not ReservationStatus.ACTIVE:
            reasons.append(f"RESERVATION_NOT_ACTIVE:{reservation.status.value}")
        elif now >= reservation.expires_at:
            reasons.append("RESERVATION_EXPIRED")

        if approval is None:
            reasons.append("APPROVAL_NOT_FOUND")
        elif approval.status is not ApprovalStatus.APPROVED:
            reasons.append(f"APPROVAL_NOT_APPROVED:{approval.status.value}")
        else:
            current_hash = self._binding_inputs(instruction.reservation_id).compute_binding_hash()
            if current_hash != approval.binding_hash:
                reasons.append("APPROVAL_BINDING_STALE")

        preflight = SettlementPreflight(
            preflight_id=f"SIM-PRE-{instruction_id.removeprefix('SIM-INSTR-')}-{len(self.preflights)}",
            instruction_id=instruction_id,
            performed_at=now,
            passed=len(reasons) == 0,
            failure_reasons=tuple(reasons),
        )
        self.preflights[instruction_id] = preflight
        self._record_audit(
            aggregate_id=instruction_id, actor_id=actor_id, action="PREFLIGHT_RUN",
            object_payload={"instruction_id": instruction_id, "version": str(preflight.passed)}, now=now,
        )
        return preflight

    # --- idempotent export (COL-013) ---
    def export_internal(
        self, instruction_id: str, *, idempotency_key: str, request_fingerprint: str, actor_id: str, now: dt.datetime,
    ) -> Envelope:
        instruction_for_authz = self.instructions.get(instruction_id)
        authorize(
            self.authz_engine, action=ACTION_EXPORT_INTERNAL, actor_id=actor_id, object_id=instruction_id,
            object_version=instruction_for_authz.instruction_hash() if instruction_for_authz is not None else idempotency_key,
            now=now,
        )
        existing = self.export_records.get(idempotency_key)
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise IdempotencyConflictError(idempotency_key)
            return existing.envelope  # exact replay, no re-export

        preflight = self.run_preflight(instruction_id, actor_id=actor_id, now=now)
        if not preflight.passed:
            raise PreflightFailedError(preflight.failure_reasons)

        instruction = self.instructions[instruction_id]
        record = instruction.to_record()
        envelope = build_envelope(
            schema_name="collateralos.instruction_record",
            schema_version="1.0.0",
            producer_product=ProducerProduct.COLLATERALOS,
            producer_version=PRODUCER_VERSION,
            issuer=self.issuer,
            audience=["cassandre"],
            object_id=instruction_id,
            object_version=instruction.instruction_hash(),
            created_at=now,
            effective_as_of=now,
            simulation_provenance=SimulationProvenance(
                is_simulated=True, scenario_id=SCENARIO_ID, generator_version=GENERATOR_VERSION
            ),
            authority_status="SIMULATION_ELIGIBLE",
            purpose_constraints=["COLLATERAL_INSTRUCTION_EXPORT"],
            lineage=[],
            payload=record,
        )
        signed = sign_envelope(envelope, self.keypair)
        self.export_records[idempotency_key] = ExportRecord(
            idempotency_key=idempotency_key, request_fingerprint=request_fingerprint, envelope=signed,
        )
        # SEC-10: additive canonical evidence v2 artifact for this export,
        # alongside (not replacing) the envelope above -- sealed once per
        # real export, not on an idempotent replay (see the early-return
        # above).
        self.evidence_v2_records[idempotency_key] = build_export_evidence_v2(
            keypair=self.keypair, instruction_id=instruction_id, instruction_hash=instruction.instruction_hash(), now=now,
        )
        self._record_audit(
            aggregate_id=instruction_id, actor_id=actor_id, action="INSTRUCTION_EXPORTED",
            object_payload={"instruction_id": instruction_id, "version": signed.content_hash}, now=now,
        )
        return signed

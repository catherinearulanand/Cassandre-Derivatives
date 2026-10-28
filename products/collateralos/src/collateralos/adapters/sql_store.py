"""SQLAlchemy + Postgres-backed `CollateralOSStore` (work item COL-014's
persistence half). `application/store.py`'s own docstring names exactly
this module: "A real deployment replaces this with `collateralos.adapters`
... without changing anything above this layer."

`SqlAlchemyCollateralOSStore` implements the identical public surface as
`collateralos.application.store.CollateralOSStore` -- same method names,
same call signatures, same return types, same exceptions (reused, never
redefined, from their existing modules) -- so it is a drop-in alternative
backed by a real `Session` against the tables in `collateralos.adapters.
models` instead of in-process dicts/lists. `apps/collateralos_api` does not
wire this in by default this session (see the product README); it exists to
be exercised directly, e.g. from `products/collateralos/tests/
test_sql_store.py` against a real Postgres.

Every method opens its own short-lived `Session` from the `sessionmaker`
passed to the constructor (one call = one transaction = one connection
checkout, the same shape a real request handler would use), commits before
returning, and lets the `with` block close/return the connection to the
pool on the way out -- including on an exception, via `Session.close()`'s
implicit rollback of anything not yet committed.

The one place behavior *actually* changes versus the in-memory store is
`reserve_plan`: it delegates the locked read/write of `inventory_balance`
rows to `collateralos.adapters.sql_reservation.reserve_positions_for_update`,
a real `SELECT ... FOR UPDATE` in place of `InMemoryInventoryRepository`'s
`threading.Lock` (see that module's docstring for the deferred-scope gap
this closes, and `test_sql_store.py` for the two-connection proof).
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from audit_protocol import Ed25519KeyPair, append_event, content_hash, verify_chain
from cassandre_contracts import (
    Envelope,
    ProducerProduct,
    SimulationProvenance,
    build_envelope,
    sign_envelope,
)
from cassandre_security.authorization import DenyByDefaultPolicyEngine
from cassandre_security.maker_checker import DistinctActorViolation, enforce_distinct_natural_persons
from cassandre_security.revocation import ensure_fence_exists, lock_fences_for_mutation

from collateralos.adapters import codec
from collateralos.adapters.models import (
    AllocationLineRow,
    AuditEventRow,
    InventoryBalanceRow,
    InventorySnapshotRow,
    ObligationRow,
    PlanRow,
    PolicyBundleRow,
    ReservationRow,
    SettlementInstructionRow,
)
from collateralos.adapters.sql_reservation import reserve_positions_for_update, release_positions_for_update
from collateralos.application.idempotency import IdempotencyConflictError, PreflightFailedError
from cassandre_security.canonical_evidence import EvidenceContainer
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
    fence_scope_key,
    peek_fence_epoch,
)
from collateralos.application.store import (
    DEFAULT_RESERVATION_TTL,
    GENERATOR_VERSION,
    PRODUCER_VERSION,
    SCENARIO_ID,
    NotFoundError,
    PlanNotActionableError,
    ReservationView,
)
from collateralos.application.store import _wrong_way_map_from_repo_docs as _load_wrong_way_map
from collateralos.core.planner import PlanningContext, plan_obligation
from collateralos.domain.approval import ApprovalBindingInputs, PlanApproval, SelfApprovalError
from collateralos.domain.inventory import InventorySnapshot
from collateralos.domain.obligation import Obligation
from collateralos.domain.plan import Plan
from collateralos.domain.policy import PolicyBundle, build_policy_bundle_sim_001
from collateralos.domain.reservation import ReservationConflictError, balances_from_positions
from collateralos.domain.settlement import SettlementInstruction, SettlementPreflight
from collateralos.domain.status import (
    ACTIONABLE_STATUSES,
    ApprovalStatus,
    PolicyBundleStatus,
    ReservationStatus,
    SolverStatus,
)


class SqlAlchemyCollateralOSStore:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        keypair: Ed25519KeyPair,
        *,
        issuer: str = "collateralos_api",
        authz_engine: DenyByDefaultPolicyEngine | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.keypair = keypair
        self.issuer = issuer
        # SEC-04: same deny-by-default gate as the in-memory store (see
        # `application/security.py`); a separate engine instance per store
        # by default so tests get isolated revocation state, exactly like
        # the in-memory store's per-instance `authz_engine` default.
        self.authz_engine = authz_engine or build_policy_engine()
        # SEC-10: additive evidence-v2 cache -- see export_internal's own
        # comment on why this is in-process only, not a DB column.
        self._evidence_v2_records: dict[str, EvidenceContainer] = {}

    def get_export_evidence_v2(self, idempotency_key: str) -> EvidenceContainer | None:
        return self._evidence_v2_records.get(idempotency_key)

    # --- audit chain (shared by every mutating method) --------------------

    def _record_audit(
        self, session: Session, *, aggregate_id: str, actor_id: str, action: str, object_payload: dict, now: dt.datetime
    ) -> None:
        # Lock the chain head (highest `sequence`) before allocating the
        # next one, per `AuditEventRow`'s own docstring -- this is the
        # Postgres-specific "SELECT ... FOR UPDATE on audit_chain_head"
        # `audit_protocol.append_event`'s docstring assigns to the calling
        # adapter, not to the (DB-agnostic) `audit_protocol` package itself.
        last_row = (
            session.execute(
                select(AuditEventRow).order_by(AuditEventRow.sequence.desc()).limit(1).with_for_update()
            )
            .scalars()
            .first()
        )
        previous = codec.audit_event_from_row(last_row) if last_row is not None else None
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
        session.add(codec.audit_event_to_row(event))

    def verify_audit_chain(self):
        with self._session_factory() as session:
            rows = session.execute(select(AuditEventRow).order_by(AuditEventRow.sequence.asc())).scalars().all()
            events = [codec.audit_event_from_row(r) for r in rows]
            return verify_chain(events)

    # --- obligation ingest (COL-001) ---------------------------------------

    def add_obligation(self, obligation: Obligation, *, actor_id: str, now: dt.datetime) -> Obligation:
        with self._session_factory() as session:
            authorize(
                self.authz_engine, action=ACTION_ADD_OBLIGATION, actor_id=actor_id,
                object_id=obligation.obligation_id, object_version=obligation.version,
                legal_entity_id=obligation.entity_id, now=now,
            )
            session.merge(codec.obligation_to_row(obligation))
            # SEC-14: provision this obligation's revocation-fence scope the
            # moment the obligation itself comes into existence (idempotent
            # -- a re-ingested obligation with the same ID does not reset an
            # already-advanced epoch). `reserve_plan`/`export_internal`
            # below are the mutation path that later locks and checks it.
            ensure_fence_exists(session, fence_scope_key(obligation_id=obligation.obligation_id), now=now)
            self._record_audit(
                session,
                aggregate_id=obligation.obligation_id,
                actor_id=actor_id,
                action="OBLIGATION_INGESTED",
                object_payload=obligation.content_hash_fields(),
                now=now,
            )
            session.commit()
        return obligation

    # --- policy bundle lifecycle (COL-004) ---------------------------------

    def _latest_policy_row(self, session: Session, policy_id: str) -> PolicyBundleRow | None:
        stmt = select(PolicyBundleRow).where(PolicyBundleRow.policy_id == policy_id)
        return session.execute(stmt).scalars().first()

    def add_draft_policy(self, *, wrong_way_map_docs_root: Path | None = None) -> PolicyBundle:
        wrong_way_map = _load_wrong_way_map(wrong_way_map_docs_root)
        policy = build_policy_bundle_sim_001(status=PolicyBundleStatus.DRAFT, wrong_way_map=wrong_way_map)
        with self._session_factory() as session:
            session.merge(codec.policy_to_row(policy))
            session.commit()
        return policy

    def validate_policy(self, policy_id: str, *, actor_id: str, now: dt.datetime) -> PolicyBundle:
        with self._session_factory() as session:
            row = self._latest_policy_row(session, policy_id)
            if row is None:
                raise NotFoundError(policy_id)
            policy = codec.policy_from_row(row)
            if policy.status is not PolicyBundleStatus.DRAFT:
                raise ValueError(f"policy {policy_id} is not DRAFT (status={policy.status})")
            row.status = PolicyBundleStatus.VALIDATED.value
            self._record_audit(
                session, aggregate_id=policy_id, actor_id=actor_id, action="POLICY_VALIDATED",
                object_payload={"policy_id": policy_id, "version": policy.version}, now=now,
            )
            session.commit()
            from dataclasses import replace as _replace

            return _replace(policy, status=PolicyBundleStatus.VALIDATED)

    def activate_policy(self, policy_id: str, *, actor_id: str, now: dt.datetime) -> PolicyBundle:
        with self._session_factory() as session:
            row = self._latest_policy_row(session, policy_id)
            if row is None:
                raise NotFoundError(policy_id)
            policy = codec.policy_from_row(row)
            if policy.status is not PolicyBundleStatus.VALIDATED:
                raise ValueError(f"policy {policy_id} is not VALIDATED (status={policy.status})")
            authorize(
                self.authz_engine, action=ACTION_ACTIVATE_POLICY, actor_id=actor_id,
                object_id=policy_id, object_version=policy.version, now=now,
            )
            row.status = PolicyBundleStatus.ACTIVE.value
            self._record_audit(
                session, aggregate_id=policy_id, actor_id=actor_id, action="POLICY_ACTIVATED",
                object_payload={"policy_id": policy_id, "version": policy.version}, now=now,
            )
            session.commit()
            from dataclasses import replace as _replace

            return _replace(policy, status=PolicyBundleStatus.ACTIVE)

    # --- inventory snapshot ingest (COL-002) -------------------------------

    def add_snapshot(self, snapshot: InventorySnapshot, *, actor_id: str, now: dt.datetime) -> InventorySnapshot:
        with self._session_factory() as session:
            authorize(
                self.authz_engine, action=ACTION_ADD_SNAPSHOT, actor_id=actor_id,
                object_id=snapshot.snapshot_id, object_version=snapshot.version, now=now,
            )
            session.merge(codec.snapshot_to_row(snapshot))
            session.flush()  # snapshot row must exist before inventory_balance's FK insert below
            balances = balances_from_positions(snapshot.positions)
            for position_id, state in balances.items():
                session.merge(codec.balance_state_to_row(state, snapshot_id=snapshot.snapshot_id))
            self._record_audit(
                session, aggregate_id=snapshot.snapshot_id, actor_id=actor_id, action="INVENTORY_SNAPSHOT_INGESTED",
                object_payload={"snapshot_id": snapshot.snapshot_id, "version": snapshot.version}, now=now,
            )
            session.commit()
        return snapshot

    # --- planning (COL-006/007/008/009) ------------------------------------

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
        with self._session_factory() as session:
            obligation_row = session.get(ObligationRow, obligation_id)
            policy_row = self._latest_policy_row(session, policy_id)
            snapshot_row = session.get(InventorySnapshotRow, snapshot_id)
            if obligation_row is None or policy_row is None or snapshot_row is None:
                raise NotFoundError("obligation/policy/snapshot")

            obligation = codec.obligation_from_row(obligation_row)
            policy = codec.policy_from_row(policy_row)
            if policy.status is not PolicyBundleStatus.ACTIVE:
                raise ValueError("POLICY_UNRESOLVED: policy is not ACTIVE")

            authorize(
                self.authz_engine, action=ACTION_CREATE_PLAN, actor_id=actor_id, object_id=plan_id,
                object_version=f"{obligation.version}|{policy.version}|{snapshot_row.version}", now=now,
            )

            positions = codec.positions_from_snapshot_row(snapshot_row)
            balance_rows = (
                session.execute(select(InventoryBalanceRow).where(InventoryBalanceRow.snapshot_id == snapshot_id))
                .scalars()
                .all()
            )
            balances = {row.position_id: codec.balance_row_to_state(row) for row in balance_rows}

            ctx = PlanningContext(
                obligation=obligation,
                positions=positions,
                balances=balances,
                policy=policy,
                inventory_snapshot_id=snapshot_id,
                inventory_snapshot_version=snapshot_row.version,
                as_of=as_of,
                existing_held_by_issuer_group=existing_held_by_issuer_group or {},
                existing_held_total=existing_held_total,
                cost_rate_by_position=cost_rate_by_position,
            )
            plan = plan_obligation(ctx, plan_id=plan_id)

            session.add(codec.plan_to_row(plan))
            session.flush()  # plan row must exist before allocation_line's FK insert below
            for line in plan.lines:
                session.add(codec.allocation_line_to_row(plan_id, line))

            self._record_audit(
                session, aggregate_id=plan_id, actor_id=actor_id, action="PLAN_CREATED",
                object_payload={"plan_id": plan_id, "version": plan.plan_hash()}, now=now,
            )
            session.commit()
            return plan

    def _get_plan(self, session: Session, plan_id: str) -> Plan:
        row = session.get(PlanRow, plan_id)
        if row is None:
            raise NotFoundError(plan_id)
        line_rows = session.execute(select(AllocationLineRow).where(AllocationLineRow.plan_id == plan_id)).scalars().all()
        return codec.plan_from_row(row, line_rows)

    def get_plan(self, plan_id: str) -> Plan:
        with self._session_factory() as session:
            return self._get_plan(session, plan_id)

    # --- reservation (COL-010) ----------------------------------------------

    def reserve_plan(
        self, plan_id: str, *, reservation_id: str, instruction_id: str, actor_id: str, now: dt.datetime,
        ttl: dt.timedelta = DEFAULT_RESERVATION_TTL,
    ) -> tuple[ReservationView, SettlementInstruction]:
        with self._session_factory() as session:
            plan = self._get_plan(session, plan_id)
            if plan.status not in ACTIONABLE_STATUSES:
                raise PlanNotActionableError(plan.status)

            authorize(
                self.authz_engine, action=ACTION_RESERVE_PLAN, actor_id=actor_id,
                object_id=plan_id, object_version=plan.plan_hash(), now=now,
            )

            # SEC-14: fence-lock this obligation's revocation scope in the
            # SAME transaction as the reservation commit below. `presented`
            # is a plain (non-locking) read of the epoch as of just before
            # this call; `lock_fences_for_mutation`'s own locked re-check is
            # what actually catches a revocation that commits in the race
            # window between that read and this point -- see
            # `test_sql_store.py`'s revocation-fence tests.
            fence_scope = fence_scope_key(obligation_id=plan.obligation_id)
            presented_epoch = peek_fence_epoch(session, fence_scope)
            lock_fences_for_mutation(
                session, scope_keys=(fence_scope,),
                presented_epoch_by_scope={fence_scope: presented_epoch if presented_epoch is not None else -1},
                now=now,
            )

            requests = {line.position_id: line.delivered_quantity.amount for line in plan.lines}
            try:
                reserve_positions_for_update(session, requests)
            except ReservationConflictError:
                plan_row = session.get(PlanRow, plan_id)
                plan_row.status = SolverStatus.RESERVATION_CONFLICT.value
                session.commit()
                raise

            expires_at = now + ttl
            session.add(
                ReservationRow(
                    reservation_id=reservation_id, plan_id=plan_id, status=ReservationStatus.ACTIVE.value,
                    expires_at=expires_at, approval_json=None,
                )
            )
            session.flush()  # reservation row must exist before settlement_instruction's FK insert below
            instruction = SettlementInstruction(
                instruction_id=instruction_id, plan_id=plan_id, plan_hash=plan.plan_hash(),
                reservation_id=reservation_id, obligation_id=plan.obligation_id, lines=plan.lines,
            )
            session.add(
                SettlementInstructionRow(
                    instruction_id=instruction_id, plan_id=plan_id, reservation_id=reservation_id,
                    instruction_hash=instruction.instruction_hash(), exported_envelope_json=None, idempotency_key=None,
                )
            )
            approval = PlanApproval(
                approval_id=f"SIM-APPR-{reservation_id.removeprefix('SIM-RES-')}",
                plan_id=plan_id,
                reservation_id=reservation_id,
            )
            reservation_row = session.get(ReservationRow, reservation_id)
            reservation_row.approval_json = codec.approval_to_json(approval)

            self._record_audit(
                session, aggregate_id=reservation_id, actor_id=actor_id, action="RESERVATION_COMMITTED",
                object_payload={"reservation_id": reservation_id, "version": plan.plan_hash()}, now=now,
            )
            session.commit()

            view = ReservationView(
                reservation_id=reservation_id, plan_id=plan_id, lines=requests,
                status=ReservationStatus.ACTIVE, expires_at=expires_at,
            )
            return view, instruction

    def release_reservation(self, reservation_id: str, *, actor_id: str, now: dt.datetime) -> None:
        with self._session_factory() as session:
            reservation_row = session.get(ReservationRow, reservation_id)
            if reservation_row is None:
                raise KeyError(reservation_id)
            plan = self._get_plan(session, reservation_row.plan_id)
            if ReservationStatus(reservation_row.status) is ReservationStatus.ACTIVE:
                lines = {line.position_id: line.delivered_quantity.amount for line in plan.lines}
                release_positions_for_update(session, lines)
            reservation_row.status = ReservationStatus.RELEASED.value  # idempotent either way
            self._record_audit(
                session, aggregate_id=reservation_id, actor_id=actor_id, action="RESERVATION_RELEASED",
                object_payload={"reservation_id": reservation_id, "version": "v1"}, now=now,
            )
            session.commit()

    # --- maker/checker approval (COL-011) -----------------------------------

    def _binding_inputs(self, session: Session, reservation_id: str) -> ApprovalBindingInputs:
        reservation_row = session.get(ReservationRow, reservation_id)
        if reservation_row is None:
            raise NotFoundError(reservation_id)
        plan = self._get_plan(session, reservation_row.plan_id)
        obligation = codec.obligation_from_row(session.get(ObligationRow, plan.obligation_id))
        policy = codec.policy_from_row(self._latest_policy_row(session, plan.policy_id))
        snapshot_row = session.get(InventorySnapshotRow, plan.inventory_snapshot_id)
        instruction_row = (
            session.execute(select(SettlementInstructionRow).where(SettlementInstructionRow.reservation_id == reservation_id))
            .scalars()
            .one()
        )
        instruction = codec.instruction_from_row(
            instruction_row, plan_hash=plan.plan_hash(), obligation_id=plan.obligation_id, lines=plan.lines
        )
        return ApprovalBindingInputs(
            obligation_id=obligation.obligation_id,
            obligation_version=obligation.version,
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash(),
            reservation_id=reservation_id,
            reservation_version=1,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            inventory_snapshot_id=snapshot_row.snapshot_id,
            inventory_snapshot_version=snapshot_row.version,
            instruction_id=instruction.instruction_id,
            instruction_hash=instruction.instruction_hash(),
        )

    def maker_approve(self, reservation_id: str, *, maker_id: str, actor_id: str, now: dt.datetime) -> PlanApproval:
        with self._session_factory() as session:
            reservation_row = session.get(ReservationRow, reservation_id)
            if reservation_row is None or reservation_row.approval_json is None:
                raise NotFoundError(reservation_id)
            approval = codec.approval_from_json(reservation_row.approval_json)
            inputs = self._binding_inputs(session, reservation_id)
            authorize(
                self.authz_engine, action=ACTION_MAKER_APPROVE, actor_id=actor_id,
                object_id=reservation_id, object_version=inputs.plan_hash, now=now,
            )
            approval.maker_approve(maker_id, inputs)
            reservation_row.approval_json = codec.approval_to_json(approval)
            self._record_audit(
                session, aggregate_id=reservation_id, actor_id=actor_id, action="PLAN_MAKER_APPROVED",
                object_payload={"reservation_id": reservation_id, "version": approval.binding_hash}, now=now,
            )
            session.commit()
            return approval

    def checker_approve(self, reservation_id: str, *, checker_id: str, actor_id: str, now: dt.datetime) -> PlanApproval:
        with self._session_factory() as session:
            reservation_row = session.get(ReservationRow, reservation_id)
            if reservation_row is None or reservation_row.approval_json is None:
                raise NotFoundError(reservation_id)
            approval = codec.approval_from_json(reservation_row.approval_json)
            inputs = self._binding_inputs(session, reservation_id)
            authorize(
                self.authz_engine, action=ACTION_CHECKER_APPROVE, actor_id=actor_id,
                object_id=reservation_id, object_version=inputs.plan_hash, now=now,
            )
            # SEC-05 defense-in-depth -- see the identical comment in
            # `application/store.py::CollateralOSStore.checker_approve`.
            if approval.maker_id is not None:
                try:
                    enforce_distinct_natural_persons(approval.maker_id, checker_id)
                except DistinctActorViolation as exc:
                    raise SelfApprovalError(str(exc)) from exc
            try:
                approval.checker_approve(checker_id, inputs)
            except SelfApprovalError:
                raise  # nothing mutated yet (domain method checks identity before touching state); no commit needed
            except ValueError:
                reservation_row.approval_json = codec.approval_to_json(approval)  # persists APPROVAL_INVALIDATED
                plan_row = session.get(PlanRow, reservation_row.plan_id)
                plan_row.status = SolverStatus.APPROVAL_INVALIDATED.value
                self._record_audit(
                    session, aggregate_id=reservation_id, actor_id=actor_id, action="PLAN_APPROVAL_INVALIDATED",
                    object_payload={"reservation_id": reservation_id, "version": "invalidated"}, now=now,
                )
                session.commit()
                raise
            reservation_row.approval_json = codec.approval_to_json(approval)
            self._record_audit(
                session, aggregate_id=reservation_id, actor_id=actor_id, action="PLAN_CHECKER_APPROVED",
                object_payload={"reservation_id": reservation_id, "version": approval.binding_hash}, now=now,
            )
            session.commit()
            return approval

    # --- preflight (COL-012) -------------------------------------------------

    def run_preflight(self, instruction_id: str, *, actor_id: str, now: dt.datetime) -> SettlementPreflight:
        with self._session_factory() as session:
            instruction_row = session.get(SettlementInstructionRow, instruction_id)
            if instruction_row is None:
                raise NotFoundError(instruction_id)
            reservation_row = session.get(ReservationRow, instruction_row.reservation_id)
            approval = (
                codec.approval_from_json(reservation_row.approval_json)
                if reservation_row is not None and reservation_row.approval_json is not None
                else None
            )

            reasons: list[str] = []
            if reservation_row is None:
                reasons.append("RESERVATION_NOT_FOUND")
            else:
                status = ReservationStatus(reservation_row.status)
                if status is not ReservationStatus.ACTIVE:
                    reasons.append(f"RESERVATION_NOT_ACTIVE:{status.value}")
                elif now >= reservation_row.expires_at:
                    reasons.append("RESERVATION_EXPIRED")

            if approval is None:
                reasons.append("APPROVAL_NOT_FOUND")
            elif approval.status is not ApprovalStatus.APPROVED:
                reasons.append(f"APPROVAL_NOT_APPROVED:{approval.status.value}")
            else:
                current_hash = self._binding_inputs(session, instruction_row.reservation_id).compute_binding_hash()
                if current_hash != approval.binding_hash:
                    reasons.append("APPROVAL_BINDING_STALE")

            prior_count = (
                session.execute(
                    select(AuditEventRow).where(
                        AuditEventRow.aggregate_id == instruction_id, AuditEventRow.action == "PREFLIGHT_RUN"
                    )
                )
                .scalars()
                .all()
            )
            preflight = SettlementPreflight(
                preflight_id=f"SIM-PRE-{instruction_id.removeprefix('SIM-INSTR-')}-{len(prior_count)}",
                instruction_id=instruction_id,
                performed_at=now,
                passed=len(reasons) == 0,
                failure_reasons=tuple(reasons),
            )
            self._record_audit(
                session, aggregate_id=instruction_id, actor_id=actor_id, action="PREFLIGHT_RUN",
                object_payload={"instruction_id": instruction_id, "version": str(preflight.passed)}, now=now,
            )
            session.commit()
            return preflight

    # --- idempotent export (COL-013) -----------------------------------------

    def export_internal(
        self, instruction_id: str, *, idempotency_key: str, request_fingerprint: str, actor_id: str, now: dt.datetime,
    ) -> Envelope:
        with self._session_factory() as session:
            instruction_row = session.get(SettlementInstructionRow, instruction_id)
            if instruction_row is None:
                raise NotFoundError(instruction_id)

            authorize(
                self.authz_engine, action=ACTION_EXPORT_INTERNAL, actor_id=actor_id,
                object_id=instruction_id, object_version=instruction_row.instruction_hash, now=now,
            )

            if instruction_row.idempotency_key == idempotency_key and instruction_row.exported_envelope_json is not None:
                stored = json.loads(instruction_row.exported_envelope_json)
                if stored["request_fingerprint"] != request_fingerprint:
                    raise IdempotencyConflictError(idempotency_key)
                return Envelope.model_validate(stored["envelope"])  # exact replay, no re-export, no new audit event

            preflight = self.run_preflight(instruction_id, actor_id=actor_id, now=now)
            if not preflight.passed:
                raise PreflightFailedError(preflight.failure_reasons)

            plan = self._get_plan(session, instruction_row.plan_id)

            # SEC-14: fence-lock the same obligation scope `reserve_plan`
            # already provisioned/locked, through this export's own commit
            # -- a revocation that commits first must block the export too,
            # not just the reservation.
            fence_scope = fence_scope_key(obligation_id=plan.obligation_id)
            presented_epoch = peek_fence_epoch(session, fence_scope)
            lock_fences_for_mutation(
                session, scope_keys=(fence_scope,),
                presented_epoch_by_scope={fence_scope: presented_epoch if presented_epoch is not None else -1},
                now=now,
            )

            instruction = SettlementInstruction(
                instruction_id=instruction_id, plan_id=instruction_row.plan_id, plan_hash=plan.plan_hash(),
                reservation_id=instruction_row.reservation_id, obligation_id=plan.obligation_id, lines=plan.lines,
            )
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

            instruction_row.idempotency_key = idempotency_key
            instruction_row.exported_envelope_json = json.dumps(
                {"request_fingerprint": request_fingerprint, "envelope": signed.model_dump(mode="json")}
            )
            # SEC-10: additive canonical evidence v2 artifact, alongside
            # (not replacing) the envelope above -- sealed once per real
            # export. Kept as an in-process cache only, not a new DB
            # column/table (a schema change was judged not worth it for
            # this optional/lighter-touch item this pass -- see the README
            # "Security integration" section); a real deployment wanting
            # this durable would add an `evidence_v2_json` column exactly
            # like `exported_envelope_json`'s own pattern.
            self._evidence_v2_records[idempotency_key] = build_export_evidence_v2(
                keypair=self.keypair, instruction_id=instruction_id, instruction_hash=instruction.instruction_hash(), now=now,
            )
            self._record_audit(
                session, aggregate_id=instruction_id, actor_id=actor_id, action="INSTRUCTION_EXPORTED",
                object_payload={"instruction_id": instruction_id, "version": signed.content_hash}, now=now,
            )
            session.commit()
            return signed

"""SQLAlchemy tables for CollateralOS (plan Section 12.1, work item COL-014's
persistence half; database ownership map in `docs/INTERPRETATION.md` Section
5: table `collateralos`).

Money/quantity columns are `Numeric` bound to exact `Decimal` (never
`Float`), matching ADR-005. This module only declares schema -- it never
runs a query itself; see `collateralos.adapters.db` for engine/session
helpers and `products/collateralos/tests/conftest.py` for the
skip-if-no-Postgres test fixture (Docker Desktop is unavailable this
session, see `docs/adr/ADR-002-database-per-product.md`).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ObligationRow(Base):
    __tablename__ = "obligation"

    obligation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    counterparty_id: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agreement_id: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    due_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class PolicyBundleRow(Base):
    __tablename__ = "policy_bundle"

    policy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False)


class InventorySnapshotRow(Base):
    """`positions_json` is a denormalized full serialization of every
    `InventoryPosition` in the snapshot (asset class, issuer group, quantity,
    market value, staleness/encumbrance/recall/self-issued/affiliate-issued
    flags) -- the immutable eligibility-relevant data the planner needs back
    on every `create_plan` call. It is populated once at snapshot ingest and
    never mutated after (the *mutable* per-position ledger fields live in
    `InventoryBalanceRow` instead, per that row's own docstring)."""

    __tablename__ = "inventory_snapshot"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    positions_json: Mapped[str] = mapped_column(Text, nullable=False)


class InventoryBalanceRow(Base):
    """Mutable balance rows kept separate from the immutable snapshot (plan
    Section 12.4 item 1) -- `reserved` is the only column a reservation
    transaction mutates; `gross_settled`/`encumbered`/`pending_outbound`/
    `blocked_or_recalled` are refreshed only by a new snapshot ingest."""

    __tablename__ = "inventory_balance"

    position_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("inventory_snapshot.snapshot_id"), nullable=False)
    gross_settled: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    encumbered: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    pending_outbound: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    blocked_or_recalled: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    reserved: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)


class PlanRow(Base):
    __tablename__ = "plan"

    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    obligation_id: Mapped[str] = mapped_column(ForeignKey("obligation.obligation_id"), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    inventory_snapshot_id: Mapped[str] = mapped_column(ForeignKey("inventory_snapshot.snapshot_id"), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)


class AllocationLineRow(Base):
    __tablename__ = "allocation_line"

    plan_id: Mapped[str] = mapped_column(ForeignKey("plan.plan_id"), primary_key=True)
    position_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    delivered_quantity: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    delivered_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    adjusted_value: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    adjusted_value_currency: Mapped[str] = mapped_column(String(3), nullable=False)


class ReservationRow(Base):
    """`approval_json` is a denormalized serialization of the reservation's
    1:1 `domain.approval.PlanApproval` (maker_id/checker_id/binding_hash/
    status) -- there is no separate approval table in this schema, and
    approval is intrinsically owned by exactly one reservation (plan Section
    12.4 step 10 binds approval to the reservation), so it lives here rather
    than inventing a new table for three mutable scalar fields plus a status
    enum. Null until `reserve_plan` creates the pending-maker approval."""

    __tablename__ = "reservation"

    reservation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plan.plan_id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approval_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class SettlementInstructionRow(Base):
    __tablename__ = "settlement_instruction"

    instruction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plan.plan_id"), nullable=False)
    reservation_id: Mapped[str] = mapped_column(ForeignKey("reservation.reservation_id"), nullable=False)
    instruction_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    exported_envelope_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)


class AuditEventRow(Base):
    """Wired to `audit_protocol.chain` (ADR-010): the application/adapter
    layer is responsible for appending the domain mutation, this row, and
    the outbox row in one transaction, and for locking the chain head with
    `SELECT ... FOR UPDATE` before allocating `sequence`."""

    __tablename__ = "audit_event"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence: Mapped[int] = mapped_column(nullable=False, unique=True)
    product: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    object_version: Mapped[str] = mapped_column(String(64), nullable=False)
    object_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    simulation_provenance_json: Mapped[str] = mapped_column(Text, nullable=False)


class OutboxRow(Base):
    """Polled with `SELECT ... FOR UPDATE SKIP LOCKED` (ADR-004); consumers
    dedupe by `event_id`."""

    __tablename__ = "outbox"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dispatched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

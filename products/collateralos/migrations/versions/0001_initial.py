"""Initial CollateralOS schema (work item COL-014 persistence half)

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "obligation",
        sa.Column("obligation_id", sa.String(64), primary_key=True),
        sa.Column("counterparty_id", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("agreement_id", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(28, 8), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
    )

    op.create_table(
        "policy_bundle",
        sa.Column("policy_id", sa.String(64), primary_key=True),
        sa.Column("version", sa.String(32), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
    )

    op.create_table(
        "inventory_snapshot",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("positions_json", sa.Text(), nullable=False),
    )

    op.create_table(
        "inventory_balance",
        sa.Column("position_id", sa.String(64), primary_key=True),
        sa.Column("snapshot_id", sa.String(64), sa.ForeignKey("inventory_snapshot.snapshot_id"), nullable=False),
        sa.Column("gross_settled", sa.Numeric(28, 8), nullable=False),
        sa.Column("encumbered", sa.Numeric(28, 8), nullable=False),
        sa.Column("pending_outbound", sa.Numeric(28, 8), nullable=False),
        sa.Column("blocked_or_recalled", sa.Numeric(28, 8), nullable=False),
        sa.Column("reserved", sa.Numeric(28, 8), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )

    op.create_table(
        "plan",
        sa.Column("plan_id", sa.String(64), primary_key=True),
        sa.Column("obligation_id", sa.String(64), sa.ForeignKey("obligation.obligation_id"), nullable=False),
        sa.Column("policy_id", sa.String(64), nullable=False),
        sa.Column("inventory_snapshot_id", sa.String(64), sa.ForeignKey("inventory_snapshot.snapshot_id"), nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
    )

    op.create_table(
        "allocation_line",
        sa.Column("plan_id", sa.String(64), sa.ForeignKey("plan.plan_id"), primary_key=True),
        sa.Column("position_id", sa.String(64), primary_key=True),
        sa.Column("delivered_quantity", sa.Numeric(28, 8), nullable=False),
        sa.Column("delivered_unit", sa.String(32), nullable=False),
        sa.Column("adjusted_value", sa.Numeric(28, 8), nullable=False),
        sa.Column("adjusted_value_currency", sa.String(3), nullable=False),
    )

    op.create_table(
        "reservation",
        sa.Column("reservation_id", sa.String(64), primary_key=True),
        sa.Column("plan_id", sa.String(64), sa.ForeignKey("plan.plan_id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_json", sa.Text(), nullable=True),
    )

    op.create_table(
        "settlement_instruction",
        sa.Column("instruction_id", sa.String(64), primary_key=True),
        sa.Column("plan_id", sa.String(64), sa.ForeignKey("plan.plan_id"), nullable=False),
        sa.Column("reservation_id", sa.String(64), sa.ForeignKey("reservation.reservation_id"), nullable=False),
        sa.Column("instruction_hash", sa.String(64), nullable=False),
        sa.Column("exported_envelope_json", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
    )

    op.create_table(
        "audit_event",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("sequence", sa.Integer(), nullable=False, unique=True),
        sa.Column("product", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("object_version", sa.String(64), nullable=False),
        sa.Column("object_content_hash", sa.String(64), nullable=False),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_event_hash", sa.String(64), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("simulation_provenance_json", sa.Text(), nullable=False),
    )

    op.create_table(
        "outbox",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("outbox")
    op.drop_table("audit_event")
    op.drop_table("settlement_instruction")
    op.drop_table("reservation")
    op.drop_table("allocation_line")
    op.drop_table("plan")
    op.drop_table("inventory_balance")
    op.drop_table("inventory_snapshot")
    op.drop_table("policy_bundle")
    op.drop_table("obligation")

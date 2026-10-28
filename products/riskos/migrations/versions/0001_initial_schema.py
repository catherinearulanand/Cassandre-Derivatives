"""initial riskos schema

Revision ID: 0001
Revises:
Create Date: 2026-01-01

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "legal_entity_version",
        sa.Column("entity_id", sa.String, primary_key=True),
        sa.Column("version", sa.String, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "netting_set_version",
        sa.Column("netting_set_id", sa.String, primary_key=True),
        sa.Column("version", sa.String, primary_key=True),
        sa.Column("calculation_entity_id", sa.String, nullable=False),
        sa.Column("counterparty_entity_id", sa.String, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_netting_set_version_calc_entity", "netting_set_version", ["calculation_entity_id"]
    )
    op.create_index(
        "ix_netting_set_version_cp_entity", "netting_set_version", ["counterparty_entity_id"]
    )

    op.create_table(
        "agreement_terms_package",
        sa.Column("agreement_id", sa.String, primary_key=True),
        sa.Column("netting_set_id", sa.String, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_agreement_terms_package_netting_set", "agreement_terms_package", ["netting_set_id"]
    )

    op.create_table(
        "portfolio_snapshot",
        sa.Column("snapshot_id", sa.String, primary_key=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "trade_mtm",
        sa.Column("trade_id", sa.String, primary_key=True),
        sa.Column("snapshot_id", sa.String, sa.ForeignKey("portfolio_snapshot.snapshot_id"), primary_key=True),
        sa.Column("netting_set_id", sa.String, nullable=False),
        sa.Column("disposition", sa.String, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_trade_mtm_netting_set", "trade_mtm", ["netting_set_id"])
    op.create_index("ix_trade_mtm_disposition", "trade_mtm", ["disposition"])

    op.create_table(
        "collateral_state_package",
        sa.Column("package_id", sa.String, primary_key=True),
        sa.Column("netting_set_id", sa.String, nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_collateral_state_package_netting_set", "collateral_state_package", ["netting_set_id"]
    )

    op.create_table(
        "calculation_run",
        sa.Column("run_id", sa.String, primary_key=True),
        sa.Column("netting_set_id", sa.String, nullable=False),
        sa.Column("calculation_status", sa.String, nullable=False),
        sa.Column("reconciliation_case_id", sa.String, nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_calculation_run_netting_set", "calculation_run", ["netting_set_id"])
    op.create_index("ix_calculation_run_status", "calculation_run", ["calculation_status"])

    op.create_table(
        "margin_waterfall",
        sa.Column("run_id", sa.String, sa.ForeignKey("calculation_run.run_id"), primary_key=True),
        sa.Column("netting_set_id", sa.String, nullable=False),
        sa.Column("call_status", sa.String, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_margin_waterfall_netting_set", "margin_waterfall", ["netting_set_id"])

    op.create_table(
        "reconciliation_case",
        sa.Column("case_id", sa.String, primary_key=True),
        sa.Column("run_id", sa.String, sa.ForeignKey("calculation_run.run_id"), nullable=False),
        sa.Column("reconciliation_status", sa.String, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reconciliation_case_run", "reconciliation_case", ["run_id"])

    op.create_table(
        "audit_event",
        sa.Column("sequence", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("event_id", sa.String, nullable=False, unique=True),
        sa.Column("product", sa.String, nullable=False),
        sa.Column("aggregate_id", sa.String, nullable=False),
        sa.Column("actor_id", sa.String, nullable=False),
        sa.Column("actor_type", sa.String, nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("reason_code", sa.String, nullable=True),
        sa.Column("object_version", sa.String, nullable=False),
        sa.Column("object_content_hash", sa.String, nullable=False),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_event_hash", sa.String, nullable=False),
        sa.Column("simulation_provenance", sa.JSON, nullable=False),
        sa.Column("trace_id", sa.String, nullable=False),
        sa.Column("event_hash", sa.String, nullable=False),
    )
    op.create_index("ix_audit_event_product", "audit_event", ["product"])
    op.create_index("ix_audit_event_aggregate", "audit_event", ["aggregate_id"])

    op.create_table(
        "outbox",
        sa.Column("outbox_id", sa.String, primary_key=True),
        sa.Column("aggregate_id", sa.String, nullable=False),
        sa.Column("event_type", sa.String, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outbox_aggregate", "outbox", ["aggregate_id"])
    op.create_index("ix_outbox_published_at", "outbox", ["published_at"])


def downgrade() -> None:
    op.drop_table("outbox")
    op.drop_table("audit_event")
    op.drop_table("reconciliation_case")
    op.drop_table("margin_waterfall")
    op.drop_table("calculation_run")
    op.drop_table("collateral_state_package")
    op.drop_table("trade_mtm")
    op.drop_table("portfolio_snapshot")
    op.drop_table("agreement_terms_package")
    op.drop_table("netting_set_version")
    op.drop_table("legal_entity_version")

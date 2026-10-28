"""RSK-X01 native valuation challenger tables (additive)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-13

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_snapshot",
        sa.Column("snapshot_id", sa.String, primary_key=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "challenger_run",
        sa.Column("run_id", sa.String, primary_key=True),
        sa.Column("netting_set_id", sa.String, nullable=False),
        sa.Column("calculation_status", sa.String, nullable=False),
        sa.Column("reconciliation_status", sa.String, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_challenger_run_netting_set", "challenger_run", ["netting_set_id"])
    op.create_index("ix_challenger_run_calc_status", "challenger_run", ["calculation_status"])
    op.create_index("ix_challenger_run_recon_status", "challenger_run", ["reconciliation_status"])


def downgrade() -> None:
    op.drop_table("challenger_run")
    op.drop_table("market_snapshot")

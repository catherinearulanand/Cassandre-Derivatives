"""Security-fence table for cassandre_security revocation (SEC-14)

Revision ID: 0002_security_fence
Revises: 0001_initial
Create Date: 2026-09-13

Creates `cassandre_security_fence`, matching
`cassandre_security.revocation.SecurityFenceRow` exactly (that module owns
the schema; this migration only applies it to CollateralOS's own database,
mirroring how `0001_initial.py` applies `adapters/models.py`'s schema).
`adapters/sql_store.py::SqlAlchemyCollateralOSStore` is what actually reads
and writes this table (`reserve_plan`/`export_internal`, via
`ensure_fence_exists`/`lock_fences_for_mutation`); this migration only
creates it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_security_fence"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cassandre_security_fence",
        sa.Column("scope_key", sa.String(256), primary_key=True),
        sa.Column("current_epoch", sa.Integer(), nullable=False),
        sa.Column("revocation_state", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cassandre_security_fence")

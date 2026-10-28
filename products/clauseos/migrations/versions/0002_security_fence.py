"""cassandre_security fence table (SEC-14)

Creates `cassandre_security_fence` in ClauseOS's own database, matching
`cassandre_security.revocation.SecurityFenceRow`'s schema exactly (see that
module) so ClauseOS's DB-backed critical-mutation fencing
(`clauseos.adapters.db.fence_gateway`) has a real table to lock rows in via
`cassandre_security.revocation.lock_fences_for_mutation`/
`ensure_fence_exists`/`advance_revocation`.

This migration defines the table directly (via `op.create_table`) rather
than importing `cassandre_security.revocation.Base.metadata` into
ClauseOS's own Alembic `env.py` target metadata -- keeping this product's
migration chain self-contained and not coupled to the shared package's
internal `DeclarativeBase` wiring, while still producing the identical
column set/types/primary key the shared package's model expects.

Revision ID: 0002_security_fence
Revises: 0001_initial
Create Date: 2026-09-13

"""

from __future__ import annotations

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
        sa.Column("scope_key", sa.String(length=256), primary_key=True),
        sa.Column("current_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revocation_state", sa.String(length=32), nullable=False, server_default="CURRENT"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cassandre_security_fence")

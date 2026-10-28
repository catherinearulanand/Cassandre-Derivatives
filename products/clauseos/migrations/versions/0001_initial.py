"""initial ClauseOS slice tables

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-12

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agreement_bundle",
        sa.Column("bundle_id", sa.String(), primary_key=True),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=True),
        sa.Column("manifest_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("incompleteness_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "document_node",
        sa.Column("document_id", sa.String(), primary_key=True),
        sa.Column("bundle_id", sa.String(), sa.ForeignKey("agreement_bundle.bundle_id"), nullable=False),
        sa.Column("document_name", sa.String(), nullable=False),
        sa.Column("raw_bytes_sha256", sa.String(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("normalized_text_sha256", sa.String(), nullable=False),
    )

    op.create_table(
        "evidence_span",
        sa.Column("span_id", sa.String(), primary_key=True),
        sa.Column("document_id", sa.String(), sa.ForeignKey("document_node.document_id"), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("source_content_hash", sa.String(), nullable=False),
    )

    op.create_table(
        "assertion",
        sa.Column("assertion_id", sa.String(), primary_key=True),
        sa.Column("bundle_id", sa.String(), sa.ForeignKey("agreement_bundle.bundle_id"), nullable=False),
        sa.Column("document_id", sa.String(), sa.ForeignKey("document_node.document_id"), nullable=False),
        sa.Column("span_id", sa.String(), sa.ForeignKey("evidence_span.span_id"), nullable=False),
        sa.Column("concept", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("proposer", sa.String(), nullable=False),
        sa.Column("candidate_id", sa.String(), nullable=False),
        sa.Column("proposed_value_json", sa.JSON(), nullable=False),
        sa.Column("evidence_content_hash", sa.String(), nullable=False),
        sa.Column("conflict_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "review_case",
        sa.Column("review_id", sa.String(), primary_key=True),
        sa.Column("assertion_id", sa.String(), sa.ForeignKey("assertion.assertion_id"), nullable=False),
        sa.Column("bundle_id", sa.String(), sa.ForeignKey("agreement_bundle.bundle_id"), nullable=False),
        sa.Column("bound_evidence_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("maker_actor_id", sa.String(), nullable=True),
        sa.Column("maker_decision", sa.String(), nullable=True),
        sa.Column("maker_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checker_actor_id", sa.String(), nullable=True),
        sa.Column("checker_decision", sa.String(), nullable=True),
        sa.Column("checker_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "policy_release",
        sa.Column("release_id", sa.String(), primary_key=True),
        sa.Column("bundle_id", sa.String(), sa.ForeignKey("agreement_bundle.bundle_id"), nullable=False),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("terms_json", sa.JSON(), nullable=False),
    )

    op.create_table(
        "audit_event",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("product", sa.String(), nullable=False),
        sa.Column("aggregate_id", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("object_version", sa.String(), nullable=False),
        sa.Column("object_content_hash", sa.String(), nullable=False),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_event_hash", sa.String(), nullable=False),
        sa.Column("simulation_provenance_json", sa.JSON(), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=False),
        sa.Column("event_hash", sa.String(), nullable=False),
    )
    op.create_index("ix_audit_event_aggregate_sequence", "audit_event", ["aggregate_id", "sequence"], unique=True)

    op.create_table(
        "outbox",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("outbox")
    op.drop_index("ix_audit_event_aggregate_sequence", table_name="audit_event")
    op.drop_table("audit_event")
    op.drop_table("policy_release")
    op.drop_table("review_case")
    op.drop_table("assertion")
    op.drop_table("evidence_span")
    op.drop_table("document_node")
    op.drop_table("agreement_bundle")

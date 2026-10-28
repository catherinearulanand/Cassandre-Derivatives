"""SQLAlchemy ORM tables for the RiskOS `riskos` database (per
`docs/INTERPRETATION.md` Section 5's database ownership map). Each table has
its natural-key scalar columns plus a `payload` JSON column holding the full
structured aggregate body (see `riskos.adapters.codec`), rather than fully
normalizing every nested value object (collateral positions, waterfall
steps, reconciliation breaks) into its own table for this slice.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LegalEntityVersionRow(Base):
    __tablename__ = "legal_entity_version"

    entity_id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class NettingSetVersionRow(Base):
    __tablename__ = "netting_set_version"

    netting_set_id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, primary_key=True)
    calculation_entity_id: Mapped[str] = mapped_column(String, index=True)
    counterparty_entity_id: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class AgreementTermsPackageRow(Base):
    __tablename__ = "agreement_terms_package"

    agreement_id: Mapped[str] = mapped_column(String, primary_key=True)
    netting_set_id: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class PortfolioSnapshotRow(Base):
    __tablename__ = "portfolio_snapshot"

    snapshot_id: Mapped[str] = mapped_column(String, primary_key=True)
    as_of: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class TradeMTMRow(Base):
    """Denormalized per-trade rows for query/reporting; the authoritative
    body of record for a snapshot is `PortfolioSnapshotRow.payload`."""

    __tablename__ = "trade_mtm"

    trade_id: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        String, ForeignKey("portfolio_snapshot.snapshot_id"), primary_key=True
    )
    netting_set_id: Mapped[str] = mapped_column(String, index=True)
    disposition: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class CollateralStatePackageRow(Base):
    __tablename__ = "collateral_state_package"

    package_id: Mapped[str] = mapped_column(String, primary_key=True)
    netting_set_id: Mapped[str] = mapped_column(String, index=True)
    as_of: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class CalculationRunRow(Base):
    __tablename__ = "calculation_run"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    netting_set_id: Mapped[str] = mapped_column(String, index=True)
    calculation_status: Mapped[str] = mapped_column(String, index=True)
    reconciliation_case_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class MarginWaterfallRow(Base):
    """The waterfall is embedded in `CalculationRunRow.payload`; this table
    exists so it is independently queryable/exportable by `run_id` per the
    `GET /v1/calculation-runs/{run_id}/waterfall` endpoint without deserializing
    the whole run."""

    __tablename__ = "margin_waterfall"

    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("calculation_run.run_id"), primary_key=True
    )
    netting_set_id: Mapped[str] = mapped_column(String, index=True)
    call_status: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)


class ReconciliationCaseRow(Base):
    __tablename__ = "reconciliation_case"

    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("calculation_run.run_id"), index=True)
    reconciliation_status: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class AuditEventRow(Base):
    __tablename__ = "audit_event"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    event_id: Mapped[str] = mapped_column(String, unique=True)
    product: Mapped[str] = mapped_column(String, index=True)
    aggregate_id: Mapped[str] = mapped_column(String, index=True)
    actor_id: Mapped[str] = mapped_column(String)
    actor_type: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    reason_code: Mapped[str | None] = mapped_column(String, nullable=True)
    object_version: Mapped[str] = mapped_column(String)
    object_content_hash: Mapped[str] = mapped_column(String)
    event_timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    previous_event_hash: Mapped[str] = mapped_column(String)
    simulation_provenance: Mapped[dict] = mapped_column(JSON)
    trace_id: Mapped[str] = mapped_column(String)
    event_hash: Mapped[str] = mapped_column(String)


class MarketSnapshotRow(Base):
    """RSK-X01 native valuation challenger input (plan 11.6); additive,
    independent of every MARGIN_CALL-path table above."""

    __tablename__ = "market_snapshot"

    snapshot_id: Mapped[str] = mapped_column(String, primary_key=True)
    as_of: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class ChallengerRunRow(Base):
    """RSK-X01 challenger run result (plan 11.6). Deliberately its own
    table, never `calculation_run`: a CHALLENGER run must never be
    queryable through, or confusable with, the authoritative MARGIN_CALL
    `CalculationRun` storage."""

    __tablename__ = "challenger_run"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    netting_set_id: Mapped[str] = mapped_column(String, index=True)
    calculation_status: Mapped[str] = mapped_column(String, index=True)
    reconciliation_status: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class OutboxRow(Base):
    """Transactional outbox (plan Section 8.4): a domain mutation and its
    outbound-event row are written in the same DB transaction by the calling
    adapter method; a separate (not-built-in-this-slice) relay process would
    poll `published_at IS NULL` and publish, then mark `published_at`."""

    __tablename__ = "outbox"

    outbox_id: Mapped[str] = mapped_column(String, primary_key=True)
    aggregate_id: Mapped[str] = mapped_column(String, index=True)
    event_type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

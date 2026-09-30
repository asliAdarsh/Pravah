"""SQLAlchemy 2.0 ORM entities for Pravah.

Enum columns are stored as plain ``String`` and validated in
:mod:`app.event_types` / :mod:`app.schemas` — the registry is the single source of
truth for event-type codes.  Geography is plain lat/lon (portable to PostGIS).

A note on annotations
--------------------
SQLAlchemy 2.0.36 on Python 3.14 raises ``TypeError`` when it evaluates a
``Mapped[X | None]`` annotation, so every optional column here is declared with
its non-optional type plus an explicit ``nullable=True``.  Runtime nullability is
unchanged; only the static hint is looser.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

__all__ = [
    "AhpProfile",
    "Alert",
    "AlertEventLink",
    "AnomalyAlert",
    "AppConfig",
    "BacktestRun",
    "Base",
    "Document",
    "DrillingEvent",
    "EngineerAction",
    "Evidence",
    "Formation",
    "OffsetRelation",
    "TelemetrySample",
    "Well",
    "utcnow",
]


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp (used as a column default)."""
    return datetime.now(timezone.utc)
class Base(DeclarativeBase):
    """Declarative base with a portable JSON annotation map."""

    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TelemetrySample(Base):
    """One real MWD/mud-logger depth sample for an active well.

    Only the channels the anomaly engine watches are stored, as a JSON map, so
    a 16,000-sample well stays small and the API never re-parses the source CSV
    on every request. ``row_index`` preserves the original export order, which
    is what the causal (leakage-free) detectors walk.
    """

    __tablename__ = "telemetry_samples"
    __table_args__ = (Index("ix_telemetry_well_row", "well_id", "row_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    well_id: Mapped[str] = mapped_column(ForeignKey("wells.id", ondelete="CASCADE"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    md: Mapped[float] = mapped_column(Float)
    tvd: Mapped[float] = mapped_column(Float, default=0.0)
    channels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AnomalyAlert(Base):
    """A physical anomaly detected on a telemetry channel at a depth."""

    __tablename__ = "anomaly_alerts"
    __table_args__ = (Index("ix_anomaly_well_md", "well_id", "md"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    well_id: Mapped[str] = mapped_column(ForeignKey("wells.id", ondelete="CASCADE"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    md: Mapped[float] = mapped_column(Float)
    hazard: Mapped[str] = mapped_column(String(32), index=True)
    channel: Mapped[str] = mapped_column(String(96))
    detector: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16), index=True)
    value: Mapped[float] = mapped_column(Float)
    baseline_mean: Mapped[float] = mapped_column(Float, default=0.0)
    baseline_std: Mapped[float] = mapped_column(Float, default=0.0)
    z_score: Mapped[float] = mapped_column(Float, default=0.0)
    cusum_s_plus: Mapped[float] = mapped_column(Float, default=0.0)
    cusum_s_minus: Mapped[float] = mapped_column(Float, default=0.0)
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BacktestRun(Base):
    """A walk-forward validation of the anomaly engine against a real incident."""

    __tablename__ = "backtest_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    well_id: Mapped[str] = mapped_column(ForeignKey("wells.id", ondelete="CASCADE"), index=True)
    incident_hazard: Mapped[str] = mapped_column(String(32), index=True)
    incident_depth_md: Mapped[float] = mapped_column(Float)
    incident_row_index: Mapped[int] = mapped_column(Integer)
    incident_source: Mapped[str] = mapped_column(String(256), default="")
    first_precursor_md: Mapped[float] = mapped_column(Float, default=0.0)
    first_critical_md: Mapped[float] = mapped_column(Float, default=0.0)
    first_sustained_md: Mapped[float] = mapped_column(Float, default=0.0)
    lead_distance_m: Mapped[float] = mapped_column(Float, default=0.0)
    lead_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    wilson_lower: Mapped[float] = mapped_column(Float, default=0.0)
    wilson_upper: Mapped[float] = mapped_column(Float, default=0.0)
    risk_at_crossing: Mapped[float] = mapped_column(Float, default=0.0)
    window: Mapped[int] = mapped_column(Integer, default=50)
    #: The complete backtest payload as produced. Served verbatim so a stored
    #: run is byte-identical to a freshly computed one; a hand-maintained
    #: summary here would drift from the live shape.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    detector: Mapped[str] = mapped_column(String(48), default="z_score+cusum")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    leakage_audit: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class AhpProfile(Base):
    """An Analytic Hierarchy Process weighting for one hazard's offset ranking.

    ``matrix`` is the Saaty pairwise-comparison matrix; ``weights`` is the
    principal-eigenvector result; ``consistency_ratio`` is Saaty's CR, which
    says whether the judgements are coherent enough to use.
    """

    __tablename__ = "ahp_profiles"

    hazard: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(String(64))
    features: Mapped[list[Any]] = mapped_column(JSON, default=list)
    matrix: Mapped[list[Any]] = mapped_column(JSON, default=list)
    weights: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    consistency_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    lambda_max: Mapped[float] = mapped_column(Float, default=0.0)
    engineering_rationale: Mapped[str] = mapped_column(Text, default="")
    references: Mapped[list[Any]] = mapped_column(JSON, default=list)



class Formation(Base):
    """A stratigraphic unit with a depth range (TVD)."""

    __tablename__ = "formations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    top_depth: Mapped[float] = mapped_column(Float)
    bottom_depth: Mapped[float] = mapped_column(Float)
    lithology: Mapped[str] = mapped_column(String(128))
    depositional_environment: Mapped[str] = mapped_column(String(128))
    age: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(16), default="#1E3A8A")
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)

    def brief(self) -> dict[str, Any]:
        """Compact formation payload used inside well/event/alert responses."""
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "top_depth": self.top_depth,
            "bottom_depth": self.bottom_depth,
            "lithology": self.lithology,
        }


class Well(Base):
    """A drilling location with an operating context and a sampled trajectory."""

    __tablename__ = "wells"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    field: Mapped[str] = mapped_column(String(64), index=True)
    block: Mapped[str] = mapped_column(String(64))
    operator: Mapped[str] = mapped_column(String(64))
    well_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    spud_date: Mapped[date] = mapped_column(Date)
    completion_date: Mapped[date] = mapped_column(Date, nullable=True)
    rig: Mapped[str] = mapped_column(String(32))
    mud_system: Mapped[str] = mapped_column(String(64))
    water_depth_m: Mapped[float] = mapped_column(Float)
    total_depth_md: Mapped[float] = mapped_column(Float)
    current_depth_md: Mapped[float] = mapped_column(Float)
    current_tvd: Mapped[float] = mapped_column(Float)
    section_size: Mapped[str] = mapped_column(String(16), default="")
    bit_size: Mapped[str] = mapped_column(String(16), default="")
    mud_weight_ppg: Mapped[float] = mapped_column(Float, default=0.0)
    rop_mph: Mapped[float] = mapped_column(Float, default=0.0)
    wob_klb: Mapped[float] = mapped_column(Float, default=0.0)
    status_note: Mapped[str] = mapped_column(Text, default="")
    trajectory: Mapped[list[Any]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)

    formation_id: Mapped[int] = mapped_column(
        ForeignKey("formations.id", ondelete="SET NULL"), nullable=True
    )

    formation: Mapped[Formation] = relationship(lazy="joined")
    events: Mapped[list["DrillingEvent"]] = relationship(
        back_populates="well", cascade="all, delete-orphan", lazy="selectin"
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="well", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Well {self.id} {self.status}>"


class Document(Base):
    """A DDR / WCR / incident report style source document."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    well_id: Mapped[str] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), nullable=True, index=True
    )
    doc_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256))
    filename: Mapped[str] = mapped_column(String(256))
    doc_date: Mapped[date] = mapped_column(Date, index=True)
    source_system: Mapped[str] = mapped_column(String(64), default="SYNTHETIC_ARCHIVE")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    ocr_engine: Mapped[str] = mapped_column(String(32), default="NONE")
    extraction_method: Mapped[str] = mapped_column(String(48), default="RULE_TEMPLATE_SIMULATED")
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    excerpt: Mapped[str] = mapped_column(Text, default="")
    sections: Mapped[list[Any]] = mapped_column(JSON, default=list)
    full_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    well: Mapped[Well] = relationship(back_populates="documents")
    events: Mapped[list["DrillingEvent"]] = relationship(back_populates="document", lazy="selectin")
    evidence: Mapped[list["Evidence"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Document {self.id} {self.doc_type}>"


class DrillingEvent(Base):
    """A single recorded drilling event at a depth in a well."""

    __tablename__ = "drilling_events"
    __table_args__ = (
        Index("ix_events_well_md", "well_id", "md"),
        Index("ix_events_type_tvd", "event_type", "tvd"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    well_id: Mapped[str] = mapped_column(ForeignKey("wells.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    formation_id: Mapped[int] = mapped_column(
        ForeignKey("formations.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    event_subtype: Mapped[str] = mapped_column(String(128), default="")
    md: Mapped[float] = mapped_column(Float)
    tvd: Mapped[float] = mapped_column(Float, index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    severity_score: Mapped[float] = mapped_column(Float, default=0.5)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    day_number: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(Text)
    mitigation: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="CLOSED")
    days_open: Mapped[int] = mapped_column(Integer, default=0)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)

    well: Mapped[Well] = relationship(back_populates="events")
    document: Mapped[Document] = relationship(back_populates="events")
    formation: Mapped[Formation] = relationship(lazy="joined")
    evidence: Mapped[list["Evidence"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )
    alert_links: Mapped[list["AlertEventLink"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<DrillingEvent {self.id} {self.event_type} {self.tvd}m>"


class Evidence(Base):
    """A verbatim excerpt backing an event, with honest page metadata."""

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("drilling_events.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    page: Mapped[int] = mapped_column(Integer, nullable=True)
    section: Mapped[str] = mapped_column(String(128), default="")
    text_span: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.9)
    bbox: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True, default=None)
    extraction_method: Mapped[str] = mapped_column(
        String(48), default="RULE_TEMPLATE_SIMULATED"
    )
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)

    event: Mapped[DrillingEvent] = relationship(back_populates="evidence")
    document: Mapped[Document] = relationship(back_populates="evidence")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Evidence {self.id} page={self.page}>"


class OffsetRelation(Base):
    """Cached relevance score between a current well and an offset well."""

    __tablename__ = "offset_relations"
    __table_args__ = (
        UniqueConstraint("current_well_id", "offset_well_id", name="uq_offset_pair"),
        Index("ix_offset_current_score", "current_well_id", "relevance_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    current_well_id: Mapped[str] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), index=True
    )
    offset_well_id: Mapped[str] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), index=True
    )
    distance_km: Mapped[float] = mapped_column(Float, default=0.0)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    relevance_band: Mapped[str] = mapped_column(String(16), default="LOW")
    formation_similarity: Mapped[float] = mapped_column(Float, default=0.0)
    depth_similarity: Mapped[float] = mapped_column(Float, default=0.0)
    spatial_proximity: Mapped[float] = mapped_column(Float, default=0.0)
    event_similarity: Mapped[float] = mapped_column(Float, default=0.0)
    factors: Mapped[list[Any]] = mapped_column(JSON, default=list)
    why_relevant: Mapped[list[Any]] = mapped_column(JSON, default=list)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    offset_well: Mapped[Well] = relationship(foreign_keys=[offset_well_id], lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<OffsetRelation {self.current_well_id}->{self.offset_well_id} "
            f"{self.relevance_score:.3f}>"
        )


class Alert(Base):
    """A proactive, explainable risk alert for the current well."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_well_status", "current_well_id", "status"),
        UniqueConstraint("current_well_id", "event_type", "interval_key", name="uq_alert_key"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    current_well_id: Mapped[str] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256))
    headline: Mapped[str] = mapped_column(Text)
    severity_band: Mapped[str] = mapped_column(String(16), index=True)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    current_tvd: Mapped[float] = mapped_column(Float, default=0.0)
    interval_top_tvd: Mapped[float] = mapped_column(Float, default=0.0)
    interval_bottom_tvd: Mapped[float] = mapped_column(Float, default=0.0)
    formation_id: Mapped[int] = mapped_column(
        ForeignKey("formations.id", ondelete="SET NULL"), nullable=True
    )
    formation_match: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    supporting_well_count: Mapped[int] = mapped_column(Integer, default=0)
    supporting_event_count: Mapped[int] = mapped_column(Integer, default=0)
    distance_to_interval_m: Mapped[float] = mapped_column(Float, nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)
    rule_id: Mapped[str] = mapped_column(String(48), default="RULE_PROTOTYPE")
    rule_version: Mapped[str] = mapped_column(String(32), default="rule-engine-0.1.0")
    risk_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_factors: Mapped[list[Any]] = mapped_column(JSON, default=list)
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    interval_key: Mapped[str] = mapped_column(String(64), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    formation: Mapped[Formation] = relationship(lazy="joined")
    well: Mapped[Well] = relationship(foreign_keys=[current_well_id], lazy="joined")
    event_links: Mapped[list["AlertEventLink"]] = relationship(
        back_populates="alert", cascade="all, delete-orphan", lazy="selectin"
    )
    actions: Mapped[list["EngineerAction"]] = relationship(
        back_populates="alert", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Alert {self.id} {self.event_type} {self.severity_band}>"


class AlertEventLink(Base):
    """Join table: which offset events support which alert."""

    __tablename__ = "alert_event_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("drilling_events.id", ondelete="CASCADE"), index=True
    )
    well_id: Mapped[str] = mapped_column(String(32), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    tvd: Mapped[float] = mapped_column(Float, default=0.0)
    distance_m: Mapped[float] = mapped_column(Float, default=0.0)

    alert: Mapped[Alert] = relationship(back_populates="event_links")
    event: Mapped[DrillingEvent] = relationship(back_populates="alert_links", lazy="joined")


class EngineerAction(Base):
    """An engineer interaction recorded against an alert (audit trail)."""

    __tablename__ = "engineer_actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), index=True
    )
    engineer: Mapped[str] = mapped_column(String(128))
    action_type: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(Text, default="")
    from_status: Mapped[str] = mapped_column(String(16), nullable=True, default=None)
    to_status: Mapped[str] = mapped_column(String(16), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    alert: Mapped[Alert] = relationship(back_populates="actions")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<EngineerAction {self.id} {self.action_type}>"


class AppConfig(Base):
    """Persisted engine configuration (relevance weights / risk rules)."""

    __tablename__ = "app_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[str] = mapped_column(String(32), default="1")
    description: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

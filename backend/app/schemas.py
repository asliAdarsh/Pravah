"""Pydantic response models and the ORM → dict serialisers behind them.

Serialisation lives here (not in routers) so every endpoint emits the same shape
for the same entity, and the routers stay validation-and-delegation only.
"""

from __future__ import annotations

from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .config import DATA_PROVENANCE
from .event_types import EVENT_TYPE_REGISTRY
from .models import (
    Alert,
    Document,
    DrillingEvent,
    EngineerAction,
    Evidence,
    Formation,
    OffsetRelation,
    Well,
)

__all__ = [
    "AlertBrief",
    "DocumentBrief",
    "DocumentDetail",
    "DocumentSummary",
    "EngineerActionOut",
    "EventBrief",
    "EventDetail",
    "EvidenceBrief",
    "FormationOut",
    "Factor",
    "IngestRequest",
    "MutedModel",
    "OffsetWell",
    "SearchFilters",
    "SearchRequest",
    "document_brief",
    "document_summary",
    "engineer_action_out",
    "event_brief",
    "event_detail",
    "evidence_brief",
    "formation_out",
    "offset_well_out",
    "well_full",
    "well_summary",
]

DOC_TYPE_LABELS: dict[str, str] = {
    "DDR": "Daily Drilling Report",
    "WCR": "Well Completion Report",
    "DGR": "Daily Geological Report",
    "INCIDENT_REPORT": "Incident Report",
    "WELL_LOG": "Well Log",
    "LESSONS_LEARNED": "Lessons Learned",
}
SEVERITY_BAND_META: list[dict[str, str]] = [
    {"code": "INFO", "label": "Info", "color": "#1D4ED8", "description": "Monitor only."},
    {
        "code": "WARNING",
        "label": "Warning",
        "color": "#B45309",
        "description": "Review before the interval is drilled.",
    },
    {
        "code": "HIGH",
        "label": "High",
        "color": "#C2410C",
        "description": "Multiple nearby wells show this hazard at the current depth.",
    },
    {
        "code": "CRITICAL",
        "label": "Critical",
        "color": "#B91C1C",
        "description": "Recurring hazard with strong support at the current depth.",
    },
]
RELEVANCE_BAND_META: list[dict[str, str]] = [
    {"code": "HIGH", "label": "High relevance", "color": "#0F4C81"},
    {"code": "MEDIUM", "label": "Medium relevance", "color": "#1D4ED8"},
    {"code": "LOW", "label": "Low relevance", "color": "#64748B"},
    {"code": "MINIMAL", "label": "Minimal relevance", "color": "#94A3B8"},
]


def doc_type_label(doc_type: str) -> str:
    """Return the human label for a document type code."""
    return DOC_TYPE_LABELS.get(doc_type, doc_type.replace("_", " ").title())


def _iso(value: Any) -> str | None:
    """Return an ISO-8601 string for a date/datetime, or ``None``."""
    if value is None:
        return None
    return value.isoformat()


# --------------------------------------------------------------------------- #
# Serialisers
# --------------------------------------------------------------------------- #


def formation_out(formation: Formation | None) -> dict[str, Any] | None:
    """Serialise a formation."""
    if formation is None:
        return None
    return {
        "id": formation.id,
        "name": formation.name,
        "code": formation.code,
        "top_depth": formation.top_depth,
        "bottom_depth": formation.bottom_depth,
        "lithology": formation.lithology,
        "depositional_environment": formation.depositional_environment,
        "age": formation.age,
        "description": formation.description,
        "color": formation.color,
        "is_simulated": formation.is_simulated,
    }


def _formation_ref(formation: Formation | None) -> dict[str, Any] | None:
    """Serialise the compact formation reference used inside well payloads."""
    if formation is None:
        return None
    return {
        "id": formation.id,
        "name": formation.name,
        "top_depth": formation.top_depth,
        "bottom_depth": formation.bottom_depth,
        "lithology": formation.lithology,
    }


def well_summary(
    well: Well,
    offset_well_count: int | None = None,
    relevant_event_count: int | None = None,
    top_alert_severity: str | None = None,
) -> dict[str, Any]:
    """Serialise a :class:`Well` as a ``WellSummary``."""
    return {
        "id": well.id,
        "name": well.name,
        "field": well.field,
        "block": well.block,
        "latitude": well.latitude,
        "longitude": well.longitude,
        "status": well.status,
        "current_depth_md": well.current_depth_md,
        "current_tvd": well.current_tvd,
        "current_formation": _formation_ref(well.formation),
        "operator": well.operator,
        "well_type": well.well_type,
        "spud_date": _iso(well.spud_date),
        "water_depth_m": well.water_depth_m,
        "is_active": well.is_active,
        "offset_well_count": offset_well_count,
        "relevant_event_count": relevant_event_count,
        "top_alert_severity": top_alert_severity,
        "data_provenance": DATA_PROVENANCE,
    }


def well_full(
    well: Well,
    offset_well_count: int | None = None,
    relevant_event_count: int | None = None,
    top_alert_severity: str | None = None,
) -> dict[str, Any]:
    """Serialise a :class:`Well` as the full ``Well`` payload."""
    payload = well_summary(well, offset_well_count, relevant_event_count, top_alert_severity)
    payload.update(
        {
            "completion_date": _iso(well.completion_date),
            "rig": well.rig,
            "mud_system": well.mud_system,
            "total_depth_md": well.total_depth_md,
            "current_formation": _formation_ref(well.formation),
            "trajectory": [
                {
                    "md": point.get("md", 0.0),
                    "tvd": point.get("tvd", 0.0),
                    "inclination": point.get("inclination", 0.0),
                }
                for point in (well.trajectory or [])
                if isinstance(point, dict)
            ],
            "operating_context": {
                "section_size": well.section_size,
                "bit_size": well.bit_size,
                "mud_weight_ppg": well.mud_weight_ppg,
                "rop_mph": well.rop_mph,
                "wob_klb": well.wob_klb,
                "block": well.block,
                "status_note": well.status_note,
            },
        }
    )
    return payload


def document_brief(document: Document | None) -> dict[str, Any] | None:
    """Serialise the compact document reference embedded in event payloads."""
    if document is None:
        return None
    return {
        "id": document.id,
        "doc_type": document.doc_type,
        "doc_type_label": doc_type_label(document.doc_type),
        "title": document.title,
        "filename": document.filename,
        "doc_date": _iso(document.doc_date),
        "page_count": document.page_count,
        "source_system": document.source_system,
    }


def document_summary(document: Document, event_count: int = 0, evidence_count: int = 0) -> dict[str, Any]:
    """Serialise a document as a ``DocumentSummary``."""
    return {
        "id": document.id,
        "well_id": document.well_id,
        "well_name": document.well.name if document.well else None,
        "doc_type": document.doc_type,
        "doc_type_label": doc_type_label(document.doc_type),
        "title": document.title,
        "filename": document.filename,
        "doc_date": _iso(document.doc_date),
        "source_system": document.source_system,
        "page_count": document.page_count,
        "event_count": event_count,
        "evidence_count": evidence_count,
        "ocr_engine": document.ocr_engine,
        "extraction_method": document.extraction_method,
        "is_simulated": document.is_simulated,
        "data_provenance": "REAL_PUBLIC_DATA" if document.is_simulated else "OPERATOR_SUPPLIED_UNVERIFIED",
    }


def document_detail(
    document: Document, event_count: int = 0, evidence_count: int = 0
) -> dict[str, Any]:
    """Serialise a document as a ``DocumentSummary`` plus excerpt and sections."""
    payload = document_summary(document, event_count, evidence_count)
    payload["excerpt"] = document.excerpt
    payload["sections"] = [
        {
            "heading": section.get("heading", "Section"),
            "page": section.get("page"),
            "text": section.get("text", ""),
        }
        for section in (document.sections or [])
        if isinstance(section, dict)
    ]
    return payload


def evidence_brief(evidence: Evidence) -> dict[str, Any]:
    """Serialise an :class:`Evidence` row.

    ``page`` is passed through unchanged — ``None`` means the prototype record has
    no page for this excerpt, and the UI is expected to say so.
    """
    return {
        "id": evidence.id,
        "page": evidence.page,
        "section": evidence.section,
        "text_span": evidence.text_span,
        "confidence": evidence.confidence,
        "bbox": evidence.bbox,
        "extraction_method": evidence.extraction_method,
        "document_id": evidence.document_id,
        "event_id": evidence.event_id,
    }


def event_brief(event: DrillingEvent) -> dict[str, Any]:
    """Serialise a compact event reference."""
    spec = EVENT_TYPE_REGISTRY.get(event.event_type)
    return {
        "id": event.id,
        "well_id": event.well_id,
        "well_name": event.well.name if event.well else event.well_id,
        "event_type": event.event_type,
        "event_label": spec.label if spec else event.event_type,
        "md": event.md,
        "tvd": event.tvd,
        "formation": event.formation.name if event.formation else None,
        "severity": event.severity,
        "severity_score": event.severity_score,
        "occurred_at": _iso(event.occurred_at),
        "description": event.description,
        "mitigation": event.mitigation,
    }


def event_detail(event: DrillingEvent) -> dict[str, Any]:
    """Serialise a full ``EventDetail``."""
    payload = event_brief(event)
    payload.update(
        {
            "event_subtype": event.event_subtype,
            "status": event.status,
            "days_open": event.days_open,
            "document": document_brief(event.document),
            "evidence_count": len(event.evidence or []),
            "data_provenance": DATA_PROVENANCE,
        }
    )
    return payload


def offset_well_out(
    relation: OffsetRelation,
    relevant_events: Sequence[DrillingEvent] = (),
    document_count: int = 0,
    source: dict[str, Any] | None = None,
    selected: bool = False,
) -> dict[str, Any]:
    """Serialise an :class:`OffsetRelation` as an ``OffsetWell``."""
    well = relation.offset_well
    payload = {
        "well": well_summary(well, relevant_event_count=len(relevant_events)),
        "distance_km": relation.distance_km,
        "relevance_score": relation.relevance_score,
        "relevance_band": relation.relevance_band,
        "similarity": {
            "formation_similarity": relation.formation_similarity,
            "depth_similarity": relation.depth_similarity,
            "spatial_proximity": relation.spatial_proximity,
            "event_similarity": relation.event_similarity,
        },
        "factors": relation.factors or [],
        "why_relevant": relation.why_relevant or [],
        "depth_range": {
            "min_md": 0.0,
            "max_md": well.current_depth_md if well else 0.0,
            "max_tvd": max(
                (
                    float(p.get("tvd", 0.0))
                    for p in (well.trajectory or [])
                    if isinstance(p, dict) and float(p.get("md", 0.0)) <= well.current_depth_md
                ),
                default=well.current_tvd if well else 0.0,
            ),
        },
        "relevant_events": [event_brief(event) for event in relevant_events],
        "event_count": relation.event_count,
        "document_count": document_count,
        "source_availability": source
        or {"documents": document_count, "with_evidence": 0, "coverage": "NONE"},
        "selected": selected,
        "method": "PROTOTYPE_HEURISTIC",
    }
    return payload


def alert_brief(alert: Alert) -> dict[str, Any]:
    """Serialise an :class:`Alert` as an ``AlertBrief``."""
    spec = EVENT_TYPE_REGISTRY.get(alert.event_type)
    return {
        "id": alert.id,
        "current_well_id": alert.current_well_id,
        "current_well_name": alert.well.name if alert.well else alert.current_well_id,
        "event_type": alert.event_type,
        "event_label": spec.label if spec else alert.event_type,
        "title": alert.title,
        "severity_band": alert.severity_band,
        "risk_score": alert.risk_score,
        "current_tvd": alert.current_tvd,
        "interval": {
            "top_tvd": alert.interval_top_tvd,
            "bottom_tvd": alert.interval_bottom_tvd,
        },
        "formation": alert.formation.name if alert.formation else None,
        "formation_match": alert.formation_match,
        "supporting_well_count": alert.supporting_well_count,
        "supporting_event_count": alert.supporting_event_count,
        "distance_to_interval_m": alert.distance_to_interval_m,
        "status": alert.status,
        "created_at": _iso(alert.created_at),
        "updated_at": _iso(alert.updated_at),
        "is_active": alert.is_active,
        "headline": alert.headline,
        "rule_id": alert.rule_id,
        "rule_version": alert.rule_version,
        "data_provenance": DATA_PROVENANCE,
    }


def engineer_action_out(action: EngineerAction) -> dict[str, Any]:
    """Serialise an :class:`EngineerAction` audit record."""
    return {
        "id": action.id,
        "alert_id": action.alert_id,
        "engineer": action.engineer,
        "action_type": action.action_type,
        "note": action.note,
        "from_status": action.from_status,
        "to_status": action.to_status,
        "created_at": _iso(action.created_at),
    }


# --------------------------------------------------------------------------- #
# Pydantic models (used for request validation and response typing)
# --------------------------------------------------------------------------- #


class MutedModel(BaseModel):
    """Base model that reads ORM attributes and populates by field name."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Factor(MutedModel):
    """One explainability factor: code, label, value, weight, contribution, detail."""

    code: str
    label: str
    value: float
    weight: float
    contribution: float
    detail: str


class FormationOut(MutedModel):
    """A stratigraphic unit."""

    id: int
    name: str
    code: str
    top_depth: float
    bottom_depth: float
    lithology: str
    depositional_environment: str
    age: str
    description: str
    is_simulated: bool = True


class EventBrief(MutedModel):
    """Compact event reference."""

    id: str
    well_id: str
    well_name: str
    event_type: str
    event_label: str
    md: float
    tvd: float
    formation: str | None = None
    severity: str
    severity_score: float
    occurred_at: str | None = None
    description: str
    mitigation: str = ""


class EventDetail(MutedModel):
    """Full event payload with its document reference and evidence count."""

    id: str
    well_id: str
    well_name: str
    event_type: str
    event_label: str
    event_subtype: str = ""
    md: float
    tvd: float
    formation: str | None = None
    severity: str
    severity_score: float
    occurred_at: str | None = None
    description: str
    mitigation: str = ""
    status: str
    days_open: int
    document: dict[str, Any] | None = None
    evidence_count: int = 0
    data_provenance: str = DATA_PROVENANCE


class EvidenceBrief(MutedModel):
    """A verbatim evidence excerpt. ``page`` may legitimately be ``None``."""

    id: str
    page: int | None = None
    section: str = ""
    text_span: str
    confidence: float
    bbox: dict[str, Any] | None = None
    extraction_method: str
    document_id: str | None = None
    event_id: str | None = None


class DocumentBrief(MutedModel):
    """Compact document reference embedded in event payloads."""

    id: str
    doc_type: str
    doc_type_label: str
    title: str
    filename: str
    doc_date: str | None = None
    page_count: int
    source_system: str


class DocumentSummary(MutedModel):
    """Document list payload."""

    id: str
    well_id: str | None = None
    well_name: str | None = None
    doc_type: str
    doc_type_label: str
    title: str
    filename: str
    doc_date: str | None = None
    source_system: str
    page_count: int
    event_count: int = 0
    evidence_count: int = 0
    ocr_engine: str
    extraction_method: str
    is_simulated: bool
    data_provenance: str = DATA_PROVENANCE


class DocumentDetail(DocumentSummary):
    """Document detail payload with excerpt and sections."""

    excerpt: str = ""
    sections: list[dict[str, Any]] = Field(default_factory=list)


class OffsetWell(MutedModel):
    """An offset well plus its explainable relevance scoring."""

    well: dict[str, Any]
    distance_km: float
    relevance_score: float
    relevance_band: str
    similarity: dict[str, float]
    factors: list[dict[str, Any]]
    why_relevant: list[str]
    depth_range: dict[str, Any]
    relevant_events: list[dict[str, Any]]
    event_count: int
    document_count: int
    source_availability: dict[str, Any]
    selected: bool = False
    method: str = "PROTOTYPE_HEURISTIC"


class AlertBrief(MutedModel):
    """Alert list payload."""

    id: str
    current_well_id: str
    current_well_name: str
    event_type: str
    event_label: str
    title: str
    severity_band: str
    risk_score: float
    current_tvd: float
    interval: dict[str, float]
    formation: str | None = None
    formation_match: str
    supporting_well_count: int
    supporting_event_count: int
    distance_to_interval_m: float | None = None
    status: str
    created_at: str | None = None
    is_active: bool
    headline: str
    data_provenance: str = DATA_PROVENANCE


class EngineerActionOut(MutedModel):
    """Engineer audit-trail record."""

    id: str
    alert_id: str
    engineer: str
    action_type: str
    note: str = ""
    from_status: str | None = None
    to_status: str | None = None
    created_at: str | None = None


class SearchFilters(MutedModel):
    """Explicit structured filters that override the parsed intent."""

    radius_km: float | None = Field(default=None, gt=0, le=200)
    event_type: str | None = None
    formation: str | None = None
    tvd_min: float | None = None
    tvd_max: float | None = None
    well_id: str | None = None
    doc_type: str | None = None
    severity_min: float | None = Field(default=None, ge=0, le=1)


class SearchRequest(MutedModel):
    """``POST /api/v1/search`` body."""

    query: str = Field(min_length=1, max_length=2000)
    current_well_id: str | None = None
    filters: SearchFilters | None = None
    limit: int = Field(default=20, ge=1, le=200)


class IngestRequest(MutedModel):
    """``POST /api/v1/documents/ingest`` JSON body."""

    filename: str = Field(min_length=1, max_length=512)
    well_id: str | None = None
    doc_type: str = "DDR"
    doc_date: str | None = None
    text: str | None = None
    ocr_engine: str = "NONE"
    title: str | None = None


class AcknowledgeRequest(MutedModel):
    """``POST /api/v1/alerts/{alert_id}/acknowledge`` body."""

    engineer: str = Field(min_length=1, max_length=128)
    note: str = ""


class NoteRequest(MutedModel):
    """``POST /api/v1/alerts/{alert_id}/notes`` body."""

    engineer: str = Field(min_length=1, max_length=128)
    note: str = ""


class StatusRequest(MutedModel):
    """``POST /api/v1/alerts/{alert_id}/status`` body."""

    engineer: str = Field(min_length=1, max_length=128)
    status: Literal["OPEN", "ACKNOWLEDGED", "DISMISSED", "CLOSED"]
    note: str = ""


class RelevanceConfigRequest(MutedModel):
    """``POST /api/v1/config/relevance`` body."""

    weights: dict[str, float]
    radius_km: float = Field(default=8.0, gt=0, le=200)
    min_relevance: float = Field(default=0.15, ge=0, le=1)


class RiskConfigRequest(MutedModel):
    """``POST /api/v1/config/risk`` body."""

    min_support_wells: int = Field(default=2, ge=1, le=20)
    tvd_tolerance_m: float = Field(default=60.0, gt=0, le=2000)
    min_relevance: float = Field(default=0.25, ge=0, le=1)
    formation_match_required: bool = True
    weights: dict[str, float]
    severity_thresholds: dict[str, float] = Field(
        default_factory=lambda: {"WARNING": 0.45, "HIGH": 0.6, "CRITICAL": 0.78}
    )


class RecomputeRequest(MutedModel):
    """``POST /api/v1/risk/recompute`` body."""

    well_id: str | None = None

"""Formation and well endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import NotFoundError
from ..models import Alert, Document, DrillingEvent, Formation, OffsetRelation, Well
from ..schemas import formation_out
from ..services import build_nearby, build_timeline, summarize_well, well_counts

router = APIRouter(tags=["wells"])

DEFAULT_LIMIT = 200


@router.get("/formations")
def list_formations(session: Session = Depends(get_db)) -> dict[str, Any]:
    """List every mapped formation, shallowest first."""
    formations = session.scalars(select(Formation).order_by(Formation.top_depth))
    return {
        "items": [formation_out(formation) for formation in formations],
        "data_provenance": "REAL_PUBLIC_DATA",
    }


@router.get("/wells")
def list_wells(
    status: str | None = Query(default=None, description="WellStatus filter"),
    field: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Free-text id/name search"),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """List wells with their aggregate offset/event/alert counts."""
    stmt = select(Well)
    count_stmt = select(func.count()).select_from(Well)
    conditions = []
    if status:
        conditions.append(Well.status == status.upper())
    if field:
        conditions.append(Well.field == field)
    if q:
        pattern = f"%{q.strip()}%"
        conditions.append(or_(Well.id.ilike(pattern), Well.name.ilike(pattern)))
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = session.scalar(count_stmt) or 0
    wells = session.scalars(
        stmt.order_by(Well.id).limit(limit).offset(offset)
    ).all()
    return {
        "items": [summarize_well(session, well) for well in wells],
        "total": total,
        "limit": limit,
        "offset": offset,
        "data_provenance": "REAL_PUBLIC_DATA",
    }


def _require_well(session: Session, well_id: str) -> Well:
    """Return the well or raise a 404."""
    well = session.get(Well, well_id)
    if well is None:
        raise NotFoundError(f"Unknown well: {well_id}")
    return well


@router.get("/wells/{well_id:path}/nearby")
def nearby_wells(
    well_id: str,
    radius_km: float = Query(default=8.0, gt=0, le=200),
    min_relevance: float = Query(default=0.15, ge=0, le=1),
    event_type: str | None = Query(default=None),
    formation: str | None = Query(default=None),
    status: str | None = Query(default=None),
    depth_min_md: float | None = Query(default=None, ge=0),
    depth_max_md: float | None = Query(default=None, ge=0),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Ranked, explainable offset wells for the current well."""
    well = _require_well(session, well_id)
    return build_nearby(
        session,
        well,
        radius_km=radius_km,
        min_relevance=min_relevance,
        event_type=event_type.upper() if event_type else None,
        formation=formation,
        status=status.upper() if status else None,
        depth_min_md=depth_min_md,
        depth_max_md=depth_max_md,
    )


@router.get("/wells/{well_id:path}/events")
def well_events(
    well_id: str,
    event_type: str | None = Query(default=None),
    tvd_min: float | None = Query(default=None, ge=0),
    tvd_max: float | None = Query(default=None, ge=0),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=1000),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """List a well's drilling events, filtered by type and TVD window."""
    from ..schemas import event_detail

    well = _require_well(session, well_id)
    stmt = select(DrillingEvent).where(DrillingEvent.well_id == well.id)
    if event_type:
        stmt = stmt.where(DrillingEvent.event_type == event_type.upper())
    if tvd_min is not None:
        stmt = stmt.where(DrillingEvent.tvd >= tvd_min)
    if tvd_max is not None:
        stmt = stmt.where(DrillingEvent.tvd <= tvd_max)
    total = session.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0
    events = session.scalars(stmt.order_by(DrillingEvent.md, DrillingEvent.id).limit(limit))
    return {
        "items": [event_detail(event) for event in events],
        "total": total,
        "data_provenance": "REAL_PUBLIC_DATA",
    }


@router.get("/wells/{well_id:path}/timeline")
def well_timeline(
    well_id: str,
    window_md: float = Query(default=300.0, ge=0, le=5000),
    event_type: str | None = Query(default=None),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Depth-aware merged timeline across the current and relevant offset wells."""
    well = _require_well(session, well_id)
    return build_timeline(
        session,
        well,
        window_md=window_md,
        event_type=event_type.upper() if event_type else None,
    )


@router.get("/offset-replay/{well_id:path}")
def offset_replay(
    well_id: str,
    radius_km: float = Query(default=8.0, gt=0, le=200),
    event_type: str | None = Query(default=None),
    top_tvd: float | None = Query(default=None, ge=0),
    bottom_tvd: float | None = Query(default=None, ge=0),
    limit_wells: int = Query(default=8, ge=1, le=50),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Compare the current well's history against the nearest offsets."""
    from ..services import build_offset_replay

    well = _require_well(session, well_id)
    return build_offset_replay(
        session,
        well,
        radius_km=radius_km,
        event_type=event_type.upper() if event_type else None,
        top_tvd=top_tvd,
        bottom_tvd=bottom_tvd,
        limit_wells=limit_wells,
    )


@router.get("/wells/{well_id:path}/documents")
def well_documents(
    well_id: str,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=1000),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Convenience alias for ``GET /documents?well_id=…``."""
    from ..schemas import document_summary

    well = _require_well(session, well_id)
    documents = session.scalars(
        select(Document)
        .where(Document.well_id == well.id)
        .order_by(Document.doc_date.desc(), Document.id)
        .limit(limit)
    )
    return {
        "items": [
            document_summary(document, len(document.events or []), len(document.evidence or []))
            for document in documents
        ],
        "total": session.scalar(
            select(func.count()).select_from(Document).where(Document.well_id == well.id)
        )
        or 0,
        "data_provenance": "REAL_PUBLIC_DATA",
    }


@router.get("/wells/{well_id:path}/alerts")
def well_alerts(
    well_id: str,
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=1000),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Convenience alias for ``GET /alerts?well_id=…``."""
    from ..routers.alerts import alerts_payload

    _require_well(session, well_id)
    return alerts_payload(
        session, well_id=well_id, status=status, severity=severity, limit=limit
    )


@router.get("/wells/{well_id:path}/relations")
def well_relations(
    well_id: str,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the cached offset-relation rows for a well (engine introspection)."""
    _require_well(session, well_id)
    relations = session.scalars(
        select(OffsetRelation)
        .where(OffsetRelation.current_well_id == well_id)
        .order_by(OffsetRelation.relevance_score.desc())
    )
    return {
        "items": [
            {
                "offset_well_id": relation.offset_well_id,
                "distance_km": relation.distance_km,
                "relevance_score": relation.relevance_score,
                "relevance_band": relation.relevance_band,
                "similarity": {
                    "formation_similarity": relation.formation_similarity,
                    "depth_similarity": relation.depth_similarity,
                    "spatial_proximity": relation.spatial_proximity,
                    "event_similarity": relation.event_similarity,
                },
                "event_count": relation.event_count,
            }
            for relation in relations
        ]
    }


@router.get("/wells/{well_id:path}")
def get_well(well_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Full well payload including trajectory and operating context."""
    from ..schemas import well_full

    well = _require_well(session, well_id)
    return well_full(well, *well_counts(session, well.id))



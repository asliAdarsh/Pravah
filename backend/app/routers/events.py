"""Event list/detail endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import NotFoundError
from ..geo import haversine_km
from ..models import DrillingEvent, Formation, Well
from ..schemas import event_brief, event_detail, evidence_brief

router = APIRouter(tags=["events"])

DEFAULT_LIMIT = 200


@router.get("/events")
def list_events(
    well_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    formation: str | None = Query(default=None),
    tvd_min: float | None = Query(default=None, ge=0),
    tvd_max: float | None = Query(default=None, ge=0),
    severity_min: float | None = Query(default=None, ge=0, le=1),
    near_well_id: str | None = Query(default=None),
    radius_km: float | None = Query(default=None, gt=0, le=200),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """List drilling events with the contract's full filter set."""
    stmt = select(DrillingEvent)
    conditions = []
    if well_id:
        conditions.append(DrillingEvent.well_id == well_id)
    if event_type:
        conditions.append(DrillingEvent.event_type == event_type.upper())
    if formation:
        stmt = stmt.join(Formation, DrillingEvent.formation_id == Formation.id)
        conditions.append(Formation.name == formation)
    if tvd_min is not None:
        conditions.append(DrillingEvent.tvd >= tvd_min)
    if tvd_max is not None:
        conditions.append(DrillingEvent.tvd <= tvd_max)
    if severity_min is not None:
        conditions.append(DrillingEvent.severity_score >= severity_min)
    for condition in conditions:
        stmt = stmt.where(condition)

    if near_well_id and radius_km is not None:
        anchor = session.get(Well, near_well_id)
        if anchor is None:
            raise NotFoundError(f"Unknown well: {near_well_id}")
        rows = session.scalars(stmt.order_by(DrillingEvent.tvd, DrillingEvent.id))
        wells = {
            well.id: well
            for well in session.scalars(select(Well))
            if haversine_km(
                anchor.latitude, anchor.longitude, well.latitude, well.longitude
            )
            <= radius_km
        }
        filtered = [event for event in rows if event.well_id in wells]
        total = len(filtered)
        page = filtered[offset : offset + limit]
        return {
            "items": [event_detail(event) for event in page],
            "total": total,
            "limit": limit,
            "offset": offset,
            "data_provenance": "REAL_PUBLIC_DATA",
        }

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    events = session.scalars(
        stmt.order_by(DrillingEvent.tvd, DrillingEvent.id).limit(limit).offset(offset)
    )
    return {
        "items": [event_detail(event) for event in events],
        "total": total,
        "limit": limit,
        "offset": offset,
        "data_provenance": "REAL_PUBLIC_DATA",
    }


def _require_event(session: Session, event_id: str) -> DrillingEvent:
    """Return the event or raise a 404."""
    event = session.get(DrillingEvent, event_id)
    if event is None:
        raise NotFoundError(f"Unknown event: {event_id}")
    return event


@router.get("/events/{event_id}")
def get_event(
    event_id: str,
    correlation_tvd_m: float = Query(default=50.0, ge=0, le=2000),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Full event payload plus its evidence and correlated events."""
    event = _require_event(session, event_id)
    correlated = [
        other
        for other in session.scalars(
            select(DrillingEvent).where(DrillingEvent.event_type == event.event_type)
        )
        if other.id != event.id
        and other.well_id != event.well_id
        and abs(other.tvd - event.tvd) <= correlation_tvd_m
    ]
    correlated.sort(key=lambda e: (abs(e.tvd - event.tvd), e.id))
    payload = event_detail(event)
    payload["evidence"] = [evidence_brief(row) for row in (event.evidence or [])]
    payload["correlated_events"] = [event_brief(other) for other in correlated[:20]]
    return payload

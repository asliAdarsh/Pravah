"""Alert list/detail and engineer-action endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import NotFoundError, ValidationFailedError
from ..models import Alert
from ..risk import ACTION_TYPES, ALERT_STATUSES, record_action
from ..schemas import (
    AcknowledgeRequest,
    AlertBrief,
    EngineerActionOut,
    NoteRequest,
    StatusRequest,
    alert_brief,
    engineer_action_out,
)
from ..services import build_alert_detail

router = APIRouter(tags=["alerts"])

DEFAULT_LIMIT = 200


def alerts_payload(
    session: Session,
    well_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Build the alert list payload. Pure: no FastAPI objects, so the
    well-scoped alias in ``routers.wells`` can call it directly."""
    stmt = select(Alert)
    count_stmt = select(Alert)
    if well_id:
        stmt = stmt.where(Alert.current_well_id == well_id)
        count_stmt = count_stmt.where(Alert.current_well_id == well_id)
    if status:
        upper = status.upper()
        if upper not in ALERT_STATUSES:
            raise ValidationFailedError(
                f"Unknown alert status {status!r}. Expected one of {list(ALERT_STATUSES)}."
            )
        stmt = stmt.where(Alert.status == upper)
        count_stmt = count_stmt.where(Alert.status == upper)
    if severity:
        stmt = stmt.where(Alert.severity_band == severity.upper())
        count_stmt = count_stmt.where(Alert.severity_band == severity.upper())
    alerts = session.scalars(
        stmt.order_by(Alert.risk_score.desc(), Alert.id).limit(limit).offset(offset)
    ).all()

    all_alerts = session.scalars(count_stmt).all()
    counts: dict[str, int] = {value: 0 for value in ALERT_STATUSES}
    for alert in all_alerts:
        counts[alert.status] = counts.get(alert.status, 0) + 1
    return {
        "items": [alert_brief(alert) for alert in alerts],
        "total": len(all_alerts),
        "counts": counts,
        "limit": limit,
        "offset": offset,
        "data_provenance": "REAL_PUBLIC_DATA",
    }


@router.get("/alerts", response_model=None)
def list_alerts(
    well_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """List alerts with per-status counts."""
    return alerts_payload(
        session, well_id=well_id, status=status, severity=severity, limit=limit, offset=offset
    )


def _require_alert(session: Session, alert_id: str) -> Alert:
    """Return the alert or raise a 404."""
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise NotFoundError(f"Unknown alert: {alert_id}")
    return alert


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Engineer decision panel: rule, q1..q6, factors, chain and actions."""
    alert = _require_alert(session, alert_id)
    return build_alert_detail(session, alert)


@router.post("/alerts/{alert_id}/acknowledge", response_model=None)
def acknowledge_alert(
    alert_id: str,
    body: AcknowledgeRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Acknowledge an alert.  Re-acknowledging records a second action."""
    alert = _require_alert(session, alert_id)
    from_status = alert.status
    alert.status = "ACKNOWLEDGED"
    session.flush()
    record_action(
        session,
        alert,
        engineer=body.engineer,
        action_type="ACKNOWLEDGE",
        note=body.note,
        from_status=from_status,
        to_status="ACKNOWLEDGED",
    )
    session.commit()
    session.refresh(alert)
    return {
        "alert": alert_brief(alert),
        "actions": [engineer_action_out(action) for action in alert.actions],
        "data_provenance": "REAL_PUBLIC_DATA",
    }

@router.post(
    "/alerts/{alert_id}/notes",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
)
def add_note(
    alert_id: str,
    body: NoteRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Attach a free-text note to an alert."""
    alert = _require_alert(session, alert_id)
    action = record_action(
        session,
        alert,
        engineer=body.engineer,
        action_type="NOTE",
        note=body.note,
    )
    session.commit()
    session.refresh(alert)
    return {
        **engineer_action_out(action),
        "alert": alert_brief(alert),
        "data_provenance": "REAL_PUBLIC_DATA",
    }


@router.post("/alerts/{alert_id}/status", response_model=None)
def change_status(
    alert_id: str,
    body: StatusRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Change an alert's status and record the transition in the audit trail."""
    alert = _require_alert(session, alert_id)
    if body.status not in ALERT_STATUSES:
        raise ValidationFailedError(
            f"Unknown status {body.status!r}. Expected one of {list(ALERT_STATUSES)}."
        )
    from_status = alert.status
    alert.status = body.status
    session.flush()
    record_action(
        session,
        alert,
        engineer=body.engineer,
        action_type="STATUS_CHANGE",
        note=body.note,
        from_status=from_status,
        to_status=body.status,
    )
    session.commit()
    session.refresh(alert)
    return {
        "alert": alert_brief(alert),
        "actions": [engineer_action_out(action) for action in alert.actions],
        "data_provenance": "REAL_PUBLIC_DATA",
    }


@router.get("/alerts/{alert_id}/actions", response_model=None)
def list_actions(
    alert_id: str,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """List an alert's engineer audit trail."""
    alert = _require_alert(session, alert_id)
    return {
        "items": [engineer_action_out(action) for action in alert.actions],
        "action_types": list(ACTION_TYPES),
    }

"""Telemetry, anomaly and backtest endpoints.

Thin HTTP layer only: every query parameter is validated by pydantic, an
unknown well becomes a 404, and the body is exactly the dict the corresponding
service returned. No business logic lives here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..anomaly import DEFAULT_WINDOW, detect_well
from ..backtest import run_backtest
from ..db import get_db
from ..errors import NotFoundError
from ..models import AnomalyAlert, Well
from ..seed_real import VOLVE_INCIDENT
from ..telemetry import DEFAULT_MAX_POINTS, channel_catalogue, snapshot, stream

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

SEVERITIES = ("MODERATE", "HIGH", "CRITICAL")


def _require_well(session: Session, well_id: str) -> Well:
    """Return the well or raise a 404."""
    well = session.get(Well, well_id)
    if well is None:
        raise NotFoundError(f"Well {well_id} does not exist")
    return well


def _split_channels(raw: list[str] | None) -> list[str] | None:
    """Accept repeated params and comma-separated values interchangeably."""
    if not raw:
        return None
    out: list[str] = []
    for entry in raw:
        out.extend(part.strip() for part in entry.split(",") if part.strip())
    return out or None


@router.get("/{well_id:path}/channels", response_model=None)
def get_channels(well_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Every channel recorded for the well, with units and real sample counts."""
    _require_well(session, well_id)
    try:
        return channel_catalogue(session, well_id)
    except LookupError as exc:  # pragma: no cover - guarded above
        raise NotFoundError(f"Well {exc} does not exist") from exc


@router.get("/{well_id:path}/stream", response_model=None)
def get_stream(
    well_id: str,
    channels: list[str] | None = Query(default=None),
    from_row: int | None = Query(default=None, ge=0),
    to_row: int | None = Query(default=None, ge=0),
    max_points: int = Query(default=DEFAULT_MAX_POINTS, ge=1, le=20000),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Depth-windowed series, envelope-downsampled so no spike is strided away."""
    _require_well(session, well_id)
    if from_row is not None and to_row is not None and to_row < from_row:
        return stream(
            session,
            well_id,
            _split_channels(channels),
            from_row=to_row,
            to_row=from_row,
            max_points=max_points,
        )
    return stream(
        session,
        well_id,
        _split_channels(channels),
        from_row=from_row,
        to_row=to_row,
        max_points=max_points,
    )


@router.get("/{well_id:path}/snapshot", response_model=None)
def get_snapshot(
    well_id: str,
    at_row: int | None = Query(default=None, ge=0),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Latest real value of every watched channel plus its most recent anomaly."""
    _require_well(session, well_id)
    return snapshot(session, well_id, at_row=at_row)


@router.get("/{well_id:path}/alerts", response_model=None)
def get_alerts(
    well_id: str,
    hazard: str = Query(default="stuck_pipe"),
    window: int = Query(default=DEFAULT_WINDOW, ge=5, le=2000),
    severity: str | None = Query(default=None),
    detector: str | None = Query(default=None),
    from_md: float | None = Query(default=None, ge=0),
    to_md: float | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=5000),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Re-run the causal detectors and return the alerts they raise.

    Detection is a pure function of the stored telemetry, so this endpoint is
    reproducible: the same well, hazard and window always yield the same alerts.
    """
    _require_well(session, well_id)
    if severity is not None and severity.upper() not in SEVERITIES:
        raise NotFoundError(f"Unknown severity {severity}")
    if detector is not None and detector not in ("z_score", "cusum"):
        raise NotFoundError(f"Unknown detector {detector}")

    payload = detect_well(session, well_id, hazard, window=window)
    alerts = payload["alerts"]
    if severity is not None:
        alerts = [a for a in alerts if a["severity"] == severity.upper()]
    if detector is not None:
        alerts = [a for a in alerts if a["detector"] == detector]
    if from_md is not None:
        alerts = [a for a in alerts if a["md"] >= from_md]
    if to_md is not None:
        alerts = [a for a in alerts if a["md"] <= to_md]

    total = len(alerts)
    return {
        "well_id": well_id,
        "hazard": payload["hazard"],
        "window": payload["window"],
        "channels_watched": payload["channels_watched"],
        "channels_evaluated": payload["channels_evaluated"],
        "channels_skipped": payload["channels_skipped"],
        "gap_policy": payload["gap_policy"],
        "total": total,
        "returned": min(total, limit),
        "alerts": alerts[:limit],
    }


@router.get("/{well_id:path}/alerts/stored", response_model=None)
def get_stored_alerts(
    well_id: str,
    limit: int = Query(default=200, ge=1, le=5000),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Anomaly alerts previously persisted to the database."""
    _require_well(session, well_id)
    rows = session.scalars(
        select(AnomalyAlert)
        .where(AnomalyAlert.well_id == well_id)
        .order_by(AnomalyAlert.md, AnomalyAlert.id)
        .limit(limit)
    )
    alerts = list(rows)
    return {
        "well_id": well_id,
        "count": len(alerts),
        "alerts": [
            {
                "id": a.id,
                "row_index": a.row_index,
                "md": a.md,
                "hazard": a.hazard,
                "channel": a.channel,
                "detector": a.detector,
                "severity": a.severity,
                "value": a.value,
                "baseline_mean": a.baseline_mean,
                "baseline_std": a.baseline_std,
                "z_score": a.z_score,
                "cusum_s_plus": a.cusum_s_plus,
                "cusum_s_minus": a.cusum_s_minus,
                "threshold": a.threshold,
                "message": a.message,
            }
            for a in alerts
        ],
    }


@router.get("/{well_id:path}/backtest", response_model=None)
def get_backtest(
    well_id: str,
    window: int = Query(default=DEFAULT_WINDOW, ge=5, le=2000),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """The stored walk-forward validation for this well and hazard.

    Reading must not recompute: the walk over ~16,700 samples takes several
    seconds, and the result is deterministic, so a stored run is served and the
    caller re-runs with POST when it wants a fresh one.
    """
    from sqlalchemy import select

    from ..models import BacktestRun

    _require_well(session, well_id)
    stored = session.scalar(
        select(BacktestRun)
        .where(
            BacktestRun.well_id == well_id,
            BacktestRun.incident_hazard == VOLVE_INCIDENT["hazard"],
        )
        .order_by(BacktestRun.created_at.desc())
        .limit(1)
    )
    if stored is not None and stored.window == window:
        if stored.payload:
            return {**stored.payload, "stored": True}
        return run_backtest(session, well_id, dict(VOLVE_INCIDENT), window=window, persist=False)
    return run_backtest(session, well_id, dict(VOLVE_INCIDENT), window=window, persist=False)


@router.post("/{well_id:path}/backtest/run", response_model=None)
def post_backtest(
    well_id: str,
    window: int = Query(default=DEFAULT_WINDOW, ge=5, le=2000),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Run the backtest and persist the result deterministically.

    The run id is a hash of (well, hazard, incident depth, incident row), so
    repeating the call updates the same row instead of accumulating duplicates.
    """
    _require_well(session, well_id)
    return run_backtest(session, well_id, dict(VOLVE_INCIDENT), window=window, persist=True)

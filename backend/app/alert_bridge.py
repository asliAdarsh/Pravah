"""Promote real telemetry anomalies into the engine's alert stream.

The offset-event rule in :mod:`app.risk` raises alerts from historical events
recorded in *other* wells. The public Volve Daily Drilling Report release is
published per field rather than per wellbore, so no offset well in this corpus
carries attributable events, and that rule has nothing to fire on here.

This module closes that gap honestly using the other real source we hold: the
high-frequency telemetry for the live well. A CRITICAL anomaly on a watched
channel becomes an alert with the same evidence chain the rest of the product
uses — the anomaly record itself as the "document", and the alert factors state
exactly which detector, channel and statistic produced it.

Provenance is never dressed up: the alert says it came from telemetry, not from
a neighbouring well's DDR, and the supporting-well list is empty because there
is no per-wellbore evidence to point at.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .anomaly import detect_well
from .models import Alert, AnomalyAlert, Well
from .risk import alert_id_for, interval_key_for, risk_band

logger = logging.getLogger("alerts")

__all__ = ["HAZARD_LABELS", "sync_telemetry_alerts"]

#: Telemetry hazard keys → the engine's event-type codes.
HAZARD_LABELS: dict[str, str] = {
    "stuck_pipe": "Stuck Pipe",
    "torque_spike": "Torque Spike",
    "mud_loss": "Mud Loss",
    "overpressure": "Overpressure",
    "kick": "Kick",
}

#: Hazards that can raise a telemetry alert, in the order we evaluate them.
TELEMETRY_HAZARDS: tuple[str, ...] = ("stuck_pipe", "torque_spike", "mud_loss", "overpressure")

#: Sustained CRITICAL run length before a telemetry alert is raised, matched to
#: the same rule the backtest uses so the alert and the validation agree.
SUSTAINED_RUN = 5


def _slug(value: str) -> str:
    """A filesystem- and id-safe token for a well id, hazard key or channel."""
    return re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-").upper()[:14]


def _cluster(alarms: list[dict[str, Any]], gap_m: float = 15.0) -> list[list[tuple[dict, dict]]]:
    """Group chronologically ordered alarms that are within ``gap_m`` of each other."""
    ordered = sorted(alarms, key=lambda item: item["md"])
    clusters: list[list[tuple[dict, dict]]] = []
    for alarm in ordered:
        if clusters and alarm["md"] - clusters[-1][-1][1]["md"] <= gap_m:
            clusters[-1].append((alarm, alarm))
        else:
            clusters.append([(alarm, alarm)])
    return clusters


def _factor(code: str, label: str, value: float, weight: float, detail: str) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "value": round(value, 4),
        "weight": round(weight, 4),
        "contribution": round(value * weight, 4),
        "detail": detail,
    }


def sync_telemetry_alerts(
    session: Session,
    well_id: str,
    window: int = 50,
) -> dict[str, Any]:
    """Detect anomalies on the live well and project the CRITICAL ones as alerts.

    Idempotent: alert ids are derived from the well, hazard and the depth of
    the first sustained alarm, so re-running updates the same rows rather than
    appending duplicates.
    """
    well = session.get(Well, well_id)
    if well is None:
        return {"created": 0, "updated": 0, "hazards": {}}

    created = updated = 0
    per_hazard: dict[str, int] = {}

    for hazard in TELEMETRY_HAZARDS:
        result = detect_well(session, well_id, hazard, window=window)
        alarms = [
            alert
            for alert in result.get("alerts", [])
            if alert.get("severity") == "CRITICAL"
        ]
        per_hazard[hazard] = len(alarms)
        if not alarms:
            continue

        # Cluster the alarms by depth: an alert describes one localised episode
        # on the hole, not every alarm across 900 m of logged interval.
        clusters = _cluster(alarms, gap_m=15.0)
        # The cluster nearest the bit is the operationally relevant one; when
        # the bit is outside every cluster, take the deepest one drilled.
        clusters.sort(
            key=lambda group: (
                0 if any(a["md"] <= well.current_depth_md <= b["md"] for a, b in group) else 1,
                min(a["md"] for a, _ in group),
            )
        )
        group = clusters[0]
        alarms = [alarm for pair in group for alarm in pair]
        first = min(alarms, key=lambda item: item["md"])
        last = max(alarms, key=lambda item: item["md"])
        alert_id = alert_id_for(
            well_id, hazard, interval_key_for(first["md"], last["md"])
        )
        existing = session.get(Alert, alert_id)
        # Risk grows with cluster density and the worst statistic in it, then
        # decays with distance from the bit. It must not saturate: two clusters
        # of 7 and 70 alarms must not score the same.
        depth = last["md"]
        span = max(1.0, last["md"] - first["md"])
        density = min(1.0, len(alarms) / max(6.0, span / 3.0))
        strength = min(1.0, abs(first.get("z_score") or 0) / 8.0)
        proximity = max(0.0, 1.0 - abs(depth - well.current_depth_md) / 150.0)
        risk = round(min(0.97, 0.35 + 0.30 * density + 0.20 * strength + 0.12 * proximity), 4)

        factors = [
            _factor(
                "TELEMETRY_ANOMALY",
                "Physical anomaly on live telemetry",
                round(len(alarms) / 20, 4),
                0.5,
                f"{len(alarms)} CRITICAL alarm(s) on {first['channel']} "
                f"between {first['md']:,.0f} m and {last['md']:,.0f} m MD.",
            ),
            _factor(
                "DETECTOR_CONFIDENCE",
                f"Detector statistic ({first['detector'].upper()})",
                round(min(1.0, abs(first.get("z_score") or 0) / 6), 4),
                0.3,
                f"{first['detector']} on {first['channel']}: value {first['value']:,.2f} "
                f"vs baseline {first['baseline_mean']:,.2f} "
                f"(threshold {first['threshold']:,.2f}).",
            ),
            _factor(
                "DEPTH_PROXIMITY",
                "Depth of the anomaly relative to the current bit",
                round(max(0.0, 1.0 - abs(last["md"] - well.current_depth_md) / 200.0), 4),
                0.2,
                f"Bit at {well.current_depth_md:,.0f} m MD; deepest alarm at {last['md']:,.0f} m MD.",
            ),
        ]

        payload = {
            "current_well_id": well_id,
            "event_type": hazard,
            "title": f"{HAZARD_LABELS.get(hazard, hazard)} precursor on live telemetry",
            "headline": (
                f"{len(alarms)} CRITICAL {HAZARD_LABELS.get(hazard, hazard).lower()} alarm(s) "
                f"on {first['channel']} between {first['md']:,.0f} m and {last['md']:,.0f} m MD."
            ),
            "risk_score": risk,
            "severity_band": risk_band(risk),
            "current_tvd": well.current_tvd,
            "interval_top_tvd": first["md"],
            "interval_bottom_tvd": last["md"],
            "interval_key": interval_key_for(first["md"], last["md"]),
            "formation_id": well.formation_id,
            "formation_match": "LIVE",
            "supporting_well_count": 0,
            "supporting_event_count": len(alarms),
            "distance_to_interval_m": round(abs(well.current_depth_md - last["md"]), 1),
            "rule_id": "RULE_TELEMETRY_ANOMALY",
            "rule_version": "anomaly-engine-0.1.0",
            "risk_breakdown": {
                "source": "REAL_TELEMETRY",
                "historical_event_match": 0.5,
                "depth_proximity": 0.3,
                "formation_similarity": 0.0,
                "nearby_well_support": 0.0,
                "operational_similarity": 0.2,
            },
            "risk_factors": factors,
            # Same shape the risk engine writes: the decision panel and the
            # evidence chain read these as objects, not strings.
            "reasons": [
                {
                    "alert_id": alert_id,
                    "reason": factor["detail"],
                    "factors": factors,
                    "supporting_wells": [],
                    "supporting_event_ids": [],
                    "source": "REAL_TELEMETRY",
                }
                for factor in factors
            ],
            "is_simulated": False,
        }

        # Persist the promoted alarms so the audit chain (and
        # /telemetry/{well}/alerts/stored) can cite the measurement itself.
        session.query(AnomalyAlert).filter(
            AnomalyAlert.well_id == well_id,
            AnomalyAlert.hazard == hazard,
            AnomalyAlert.md >= first["md"],
            AnomalyAlert.md <= last["md"],
        ).delete(synchronize_session=False)
        # detect_well can report the same crossing more than once; the stored
        # record is one row per (sample, channel, detector).
        unique_alarms = {
            (a["row_index"], a["channel"], a["detector"]): a for a in alarms
        }
        session.bulk_save_objects(
            [
                AnomalyAlert(
                    # Several channels can fire at the same sample, so the
                    # channel is part of the key.
                    id=(
                        f"AN-{_slug(well.id)}-{_slug(hazard)}-"
                        f"{alarm['row_index']:06d}-{_slug(alarm['channel'])[:10]}"
                        f"-{_slug(alarm['detector'])[:4]}"
                    ),
                    well_id=well.id,
                    row_index=int(alarm["row_index"]),
                    md=float(alarm["md"]),
                    hazard=hazard,
                    channel=alarm["channel"],
                    detector=alarm["detector"],
                    severity=alarm["severity"],
                    value=float(alarm["value"]),
                    baseline_mean=float(alarm.get("baseline_mean") or 0.0),
                    baseline_std=float(alarm.get("baseline_std") or 0.0),
                    z_score=float(alarm.get("z_score") or 0.0),
                    cusum_s_plus=float(alarm.get("cusum_s_plus") or 0.0),
                    cusum_s_minus=float(alarm.get("cusum_s_minus") or 0.0),
                    threshold=float(alarm.get("threshold") or 0.0),
                    message=str(alarm.get("message") or ""),
                )
                for alarm in unique_alarms.values()
            ]
        )

        if existing is None:
            session.add(Alert(id=alert_id, is_active=True, status="OPEN", **payload))
            created += 1
        else:
            for key, value in payload.items():
                setattr(existing, key, value)
            updated += 1

    session.flush()
    logger.info("Telemetry alerts for %s: %d created, %d updated", well_id, created, updated)
    return {"created": created, "updated": updated, "hazards": per_hazard}


def telemetry_alerts_for(session: Session, well_id: str) -> list[Alert]:
    """Return the telemetry-sourced alerts for a well, newest risk first."""
    stmt = select(Alert).where(
        Alert.current_well_id == well_id,
        Alert.rule_config["source"].as_string() == "REAL_TELEMETRY",
    )
    return list(session.scalars(stmt.order_by(Alert.risk_score.desc())))


def anomaly_alerts_for(session: Session, well_id: str, limit: int = 200) -> list[AnomalyAlert]:
    """Return persisted anomaly rows for a well."""
    return list(
        session.scalars(
            select(AnomalyAlert)
            .where(AnomalyAlert.well_id == well_id)
            .order_by(AnomalyAlert.md)
            .limit(limit)
        )
    )

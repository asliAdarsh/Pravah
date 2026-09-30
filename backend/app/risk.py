"""Proactive, explainable alert engine.

The engine answers *"at my current depth, what did comparable nearby wells do, and
what should I consider?"* — as a rule, not a model.  Every number it emits is
carried with the factor that produced it, so the UI renders "WHY THIS" from data.

Rule
----
For a current well ``C`` at TVD ``T`` and each registered event type ``E``:

1. Take the offset wells whose cached relevance is ``>= min_relevance``.
2. Collect their ``E`` events whose TVD falls inside a growing interval around
   ``T``.  The interval starts at ``T ± tvd_tolerance_m``; once a supporting
   event is found, the interval expands to the extent of the supporting events
   (one growth pass, so a cluster spanning 1,482–1,510 m is captured whole).
3. Fire the rule when ``len(supporting_wells) >= min_support_wells`` and, if
   ``formation_match_required``, the supporting events sit in the same formation
   band as the current position (``SAME``) or an immediately adjacent one
   (``ADJACENT``).
4. Score risk from five weighted components and map the total to a severity band.

Risk formula
------------
::

    risk = w_h * historical_event_match
         + w_p * depth_proximity
         + w_f * formation_similarity
         + w_n * nearby_well_support
         + w_o * operational_similarity

All five components are similarities in ``[0, 1]``; weights are normalised
defensively so the total always lands in ``[0, 1]``.

Determinism and reconciliation
------------------------------
An alert's id is derived from ``(current_well_id, event_type, interval_key)``, and
``interval_key`` is quantised to 5 m.  Recomputing a well therefore updates the
existing row rather than creating a duplicate.  Alerts whose supporting cluster
no longer exists are deactivated, never deleted, so the audit trail survives.
"""

from __future__ import annotations

import hashlib
from typing import Any, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import DATA_PROVENANCE, ENGINE_VERSION
from .event_types import EVENT_TYPE_REGISTRY, is_alertable, severity_weight
from .models import (
    Alert,
    AlertEventLink,
    AppConfig,
    Document,
    DrillingEvent,
    EngineerAction,
    Evidence,
    Formation,
    OffsetRelation,
    Well,
    utcnow,
)

__all__ = [
    "DEFAULT_RISK_CONFIG",
    "RISK_WEIGHT_KEYS",
    "SEVERITY_BANDS",
    "SEVERITY_ORDER",
    "alert_id_for",
    "build_alert",
    "build_decision_panel",
    "compute_risk",
    "get_risk_config",
    "recompute_all_alerts",
    "recompute_well_alerts",
    "risk_band",
    "set_risk_config",
]

METHOD = "PROTOTYPE_HEURISTIC"
DISCLAIMER = "Heuristic output. Not an operational instruction."

RISK_WEIGHT_KEYS: tuple[str, ...] = (
    "historical_event_match",
    "depth_proximity",
    "formation_similarity",
    "nearby_well_support",
    "operational_similarity",
)

DEFAULT_RISK_WEIGHTS: dict[str, float] = {
    "historical_event_match": 0.35,
    "depth_proximity": 0.25,
    "formation_similarity": 0.20,
    "nearby_well_support": 0.15,
    "operational_similarity": 0.05,
}

DEFAULT_RISK_CONFIG: dict[str, Any] = {
    "min_support_wells": 2,
    "tvd_tolerance_m": 60.0,
    "min_relevance": 0.25,
    "formation_match_required": True,
    "weights": DEFAULT_RISK_WEIGHTS,
    "severity_thresholds": {"WARNING": 0.45, "HIGH": 0.60, "CRITICAL": 0.78},
}

#: Bands ordered from most to least severe; the first threshold the score meets
#: wins, with anything below ``WARNING`` landing in ``INFO``.
SEVERITY_BANDS: tuple[str, ...] = ("INFO", "WARNING", "HIGH", "CRITICAL")
SEVERITY_ORDER: dict[str, int] = {band: index for index, band in enumerate(SEVERITY_BANDS)}
BAND_COLORS: dict[str, str] = {
    "INFO": "#1D4ED8",
    "WARNING": "#B45309",
    "HIGH": "#C2410C",
    "CRITICAL": "#B91C1C",
}
#: Alert status enum.
ALERT_STATUSES: tuple[str, ...] = ("OPEN", "ACKNOWLEDGED", "DISMISSED", "CLOSED")
#: Action type enum.
ACTION_TYPES: tuple[str, ...] = ("ACKNOWLEDGE", "NOTE", "STATUS_CHANGE", "REVIEW")

INTERVAL_QUANTUM_M = 5.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp ``value`` into ``[low, high]``."""
    return max(low, min(high, value))


def normalize_risk_weights(weights: dict[str, Any] | None) -> dict[str, float]:
    """Return a complete, non-negative, sum-1 risk weight dict.

    Mirrors :func:`app.relevance.normalize_weights` so a malformed client payload
    can never push a risk score outside ``[0, 1]``.
    """
    merged = {key: float(DEFAULT_RISK_WEIGHTS[key]) for key in RISK_WEIGHT_KEYS}
    if weights:
        for key in RISK_WEIGHT_KEYS:
            raw = weights.get(key)
            if raw is None:
                continue
            try:
                merged[key] = max(0.0, float(raw))
            except (TypeError, ValueError):
                continue
    total = sum(merged.values())
    if total <= 0:
        return dict(DEFAULT_RISK_WEIGHTS)
    return {key: round(value / total, 6) for key, value in merged.items()}


def _clamp_config(config: dict[str, Any]) -> dict[str, Any]:
    """Coerce a config dict into a valid, fully-populated rule configuration."""
    thresholds = dict(DEFAULT_RISK_CONFIG["severity_thresholds"])
    raw_thresholds = config.get("severity_thresholds") or {}
    for band in ("WARNING", "HIGH", "CRITICAL"):
        value = raw_thresholds.get(band)
        if value is not None:
            try:
                thresholds[band] = float(value)
            except (TypeError, ValueError):
                continue
    # Bands must be strictly increasing for the mapping to be well defined.
    if not (0 < thresholds["WARNING"] < thresholds["HIGH"] < thresholds["CRITICAL"]):
        thresholds = dict(DEFAULT_RISK_CONFIG["severity_thresholds"])
    return {
        "min_support_wells": max(1, int(config.get("min_support_wells", 2))),
        "tvd_tolerance_m": max(1.0, float(config.get("tvd_tolerance_m", 60.0))),
        "min_relevance": _clamp(float(config.get("min_relevance", 0.25))),
        "formation_match_required": bool(config.get("formation_match_required", True)),
        "weights": normalize_risk_weights(config.get("weights")),
        "severity_thresholds": thresholds,
    }


def risk_band(score: float, thresholds: dict[str, float] | None = None) -> str:
    """Map a risk score in ``[0, 1]`` to a severity band."""
    limits = dict(DEFAULT_RISK_CONFIG["severity_thresholds"])
    if thresholds:
        for band in ("WARNING", "HIGH", "CRITICAL"):
            if band in thresholds:
                limits[band] = float(thresholds[band])
    if score >= limits["CRITICAL"]:
        return "CRITICAL"
    if score >= limits["HIGH"]:
        return "HIGH"
    if score >= limits["WARNING"]:
        return "WARNING"
    return "INFO"


def top_band(bands: Sequence[str]) -> str | None:
    """Return the most severe band in ``bands`` (or ``None`` when empty)."""
    if not bands:
        return None
    return max(bands, key=lambda band: SEVERITY_ORDER.get(band, -1))


def alert_id_for(current_well_id: str, event_type: str, interval_key: str) -> str:
    """Return the deterministic alert id for a ``(well, type, interval)`` triple.

    The digest makes recomputation idempotent: the same cluster always resolves
    to the same ``AL-xxxxxx`` id, so a recompute updates rather than duplicates.
    """
    digest = hashlib.sha1(
        f"{current_well_id}|{event_type}|{interval_key}".encode("utf-8")
    ).hexdigest()[:6].upper()
    return f"AL-{digest}"


def interval_key_for(top_tvd: float, bottom_tvd: float) -> str:
    """Quantise a TVD interval to :data:`INTERVAL_QUANTUM_M` for id stability."""
    # Floor, not round: a cluster whose edge lands mid-bucket must still share a
    # key with the same cluster recomputed a metre deeper.
    top = int(top_tvd // INTERVAL_QUANTUM_M)
    bottom = int(bottom_tvd // INTERVAL_QUANTUM_M)
    return f"{top}-{bottom}"


# --------------------------------------------------------------------------- #
# Configuration persistence
# --------------------------------------------------------------------------- #

CONFIG_KEY = "risk_config"


def get_risk_config(session: Session) -> dict[str, Any]:
    """Return the persisted risk configuration or the prototype defaults."""
    row = session.scalar(select(AppConfig).where(AppConfig.key == CONFIG_KEY))
    if row is None or not row.value:
        return _clamp_config(DEFAULT_RISK_CONFIG)
    return _clamp_config({**DEFAULT_RISK_CONFIG, **dict(row.value)})


def set_risk_config(
    session: Session,
    payload: dict[str, Any] | None = None,
    version: str = "1",
) -> dict[str, Any]:
    """Persist the risk configuration.

    The caller (:mod:`app.routers.config`) is responsible for validating the
    incoming payload before delegating here.
    """
    merged = _clamp_config({**DEFAULT_RISK_CONFIG, **(payload or {})})
    row = session.scalar(select(AppConfig).where(AppConfig.key == CONFIG_KEY))
    if row is None:
        session.add(
            AppConfig(
                key=CONFIG_KEY,
                value=merged,
                version=version,
                description="Engine rule thresholds — not operationally validated",
            )
        )
    else:
        row.value = merged
        row.version = version
        row.description = "Engine rule thresholds — not operationally validated"
    session.flush()
    return merged


# --------------------------------------------------------------------------- #
# Core rule
# --------------------------------------------------------------------------- #



def _grow_interval(
    events: Sequence[DrillingEvent], center_tvd: float, tolerance_m: float
) -> list[DrillingEvent]:
    """Find the supporting cluster for one event type around ``center_tvd``."""
    first_pass = [e for e in events if abs(e.tvd - center_tvd) <= tolerance_m]
    if not first_pass:
        return []
    top = min(e.tvd for e in first_pass)
    bottom = max(e.tvd for e in first_pass)
    # One growth pass: the cluster may legitimately extend beyond the tolerance.
    grown = [e for e in events if top <= e.tvd <= bottom]
    if not grown:
        return first_pass
    return sorted(grown, key=lambda e: e.tvd)


def _formation_match(
    current_formation_id: int,
    supporting: Sequence[DrillingEvent],
    formations: Sequence[Formation],
) -> str:
    """Classify the formation relationship as SAME / ADJACENT / DIFFERENT / UNKNOWN."""
    if not supporting or current_formation_id is None:
        return "UNKNOWN"
    ids = {e.formation_id for e in supporting if e.formation_id is not None}
    if not ids:
        return "UNKNOWN"
    if current_formation_id in ids:
        return "SAME"
    ordered = sorted(formations, key=lambda f: f.top_depth)
    positions = [i for i, f in enumerate(ordered) if f.id in ids]
    current_index = next(
        (i for i, f in enumerate(ordered) if f.id == current_formation_id), None
    )
    if current_index is None:
        return "DIFFERENT"
    if any(abs(p - current_index) == 1 for p in positions):
        return "ADJACENT"
    return "DIFFERENT"


def compute_risk(
    current_well: Well,
    supporting_events: Sequence[DrillingEvent],
    supporting_offsets: Sequence[OffsetRelation],
    config: dict[str, Any],
    formation_match: str,
    current_formation_id: int | None,
) -> dict[str, Any]:
    """Score one candidate alert and return its breakdown and factors.

    Args:
        current_well: The well being drilled.
        supporting_events: Offset events that triggered the rule.
        supporting_offsets: The offset relations those events came from.
        config: Clamped rule configuration.
        formation_match: ``SAME``/``ADJACENT``/``DIFFERENT``/``UNKNOWN``.
        current_formation_id: Formation at the current depth.

    Returns:
        ``{"total", "breakdown", "factors", "interval", "distance_to_interval_m"}``.
    """
    weights = config["weights"]
    current_tvd = current_well.current_tvd

    top = min((e.tvd for e in supporting_events), default=current_tvd)
    bottom = max((e.tvd for e in supporting_events), default=current_tvd)
    distance_to_interval = 0.0
    if current_tvd < top:
        distance_to_interval = top - current_tvd
    elif current_tvd > bottom:
        distance_to_interval = current_tvd - bottom
    distance_to_interval = round(distance_to_interval, 1)

    # 1. How severe was the history?
    type_weight = severity_weight(supporting_events[0].event_type) if supporting_events else 0.5
    raw_match = sum(e.severity_score for e in supporting_events) / len(supporting_events)
    historical_event_match = round(_clamp(raw_match * type_weight), 4)

    # 2. How close is the cluster to the bit?
    tolerance = float(config["tvd_tolerance_m"])
    depth_proximity = round(_clamp(1.0 - distance_to_interval / tolerance), 4)

    # 3. Is it the same rock?
    formation_similarity = {
        "SAME": 1.0,
        "ADJACENT": 0.5,
        "DIFFERENT": 0.1,
        "UNKNOWN": 0.0,
    }.get(formation_match, 0.0)

    # 4. How many independent wells agree?
    well_count = len({e.well_id for e in supporting_events})
    nearby_well_support = round(_clamp(well_count / 4.0), 4)

    # 5. Are we running comparable mud?
    comparable_offsets = [
        offset
        for offset in supporting_offsets
        if session_offset_mud_weight(offset) is not None
    ]
    if current_well.mud_weight_ppg <= 0 or not comparable_offsets:
        operational_similarity = 0.0
        operational_detail = (
            "No comparable mud programme in the supporting offset wells, so this "
            "factor contributes 0.00."
        )
    else:
        deltas = [
            abs(mud_weight - current_well.mud_weight_ppg)
            for mud_weight in (
                session_offset_mud_weight(offset) for offset in comparable_offsets
            )
            if mud_weight is not None
        ]
        mean_delta = sum(deltas) / len(deltas)
        operational_similarity = round(_clamp(1.0 - mean_delta / 2.0), 4)
        operational_detail = (
            f"Current mud weight {current_well.mud_weight_ppg:.1f} ppg against "
            f"{len(comparable_offsets)} supporting well(s); mean difference "
            f"{mean_delta:.2f} ppg."
        )

    breakdown = {
        "historical_event_match": historical_event_match,
        "depth_proximity": depth_proximity,
        "formation_similarity": formation_similarity,
        "nearby_well_support": nearby_well_support,
        "operational_similarity": operational_similarity,
    }
    total = round(sum(weights[key] * value for key, value in breakdown.items()), 4)

    factors = [
        _factor(
            "HISTORICAL_EVENT_MATCH",
            "Severity of comparable history",
            historical_event_match,
            weights["historical_event_match"],
            (
                f"{len(supporting_events)} comparable event(s) averaging "
                f"{raw_match:.2f} severity, type weight {type_weight:.2f}"
            ),
        ),
        _factor(
            "DEPTH_PROXIMITY",
            "Depth proximity to the current bit",
            depth_proximity,
            weights["depth_proximity"],
            (
                f"Cluster interval {top:,.0f}–{bottom:,.0f} m TVD; current TVD "
                f"{current_tvd:,.0f} m is {distance_to_interval:,.0f} m from the interval"
            ),
        ),
        _factor(
            "FORMATION_MATCH",
            "Formation match at the current depth",
            formation_similarity,
            weights["formation_similarity"],
            f"Formation relationship at the supporting depths: {formation_match}",
        ),
        _factor(
            "NEARBY_WELL_SUPPORT",
            "Independent nearby well support",
            nearby_well_support,
            weights["nearby_well_support"],
            f"{well_count} distinct offset well(s) recorded the same hazard",
        ),
        _factor(
            "OPERATIONAL_SIMILARITY",
            "Mud programme similarity",
            operational_similarity,
            weights["operational_similarity"],
            operational_detail,
        ),
    ]

    return {
        "total": total,
        "breakdown": breakdown,
        "factors": factors,
        "interval": {"top_tvd": round(top, 1), "bottom_tvd": round(bottom, 1)},
        "distance_to_interval_m": distance_to_interval,
    }


def _factor(code: str, label: str, value: float, weight: float, detail: str) -> dict[str, Any]:
    """Build one explainability factor record."""
    return {
        "code": code,
        "label": label,
        "value": round(float(value), 4),
        "weight": round(float(weight), 4),
        "contribution": round(float(value) * float(weight), 4),
        "detail": detail,
    }


def session_offset_mud_weight(offset: OffsetRelation) -> float:
    """Return the offset well's mud weight (0.0 when unknown)."""
    well = offset.offset_well
    return float(well.mud_weight_ppg) if well is not None and well.mud_weight_ppg else 0.0


def build_alert(
    session: Session,
    current_well: Well,
    event_type: str,
    supporting_events: Sequence[DrillingEvent],
    supporting_offsets: Sequence[OffsetRelation],
    config: dict[str, Any],
    formations: Sequence[Formation],
) -> dict[str, Any] | None:
    """Evaluate one ``(well, event_type)`` candidate and build its alert payload.

    Returns ``None`` when the rule does not fire, including when ``event_type`` is
    not alertable — this is the second of two guards, so a direct
    :func:`build_alert` call cannot bypass the registry gate either.
    """
    if not is_alertable(event_type):
        return None
    if not supporting_events:
        return None
    well_ids = {event.well_id for event in supporting_events}
    if len(well_ids) < int(config["min_support_wells"]):
        return None

    current_formation_id = current_well.formation_id
    formation_match = _formation_match(
        current_formation_id if current_formation_id is not None else -1,
        supporting_events,
        formations,
    )
    if config["formation_match_required"] and formation_match not in ("SAME", "ADJACENT"):
        return None

    risk = compute_risk(
        current_well,
        supporting_events,
        supporting_offsets,
        config,
        formation_match,
        current_formation_id,
    )
    band = risk_band(risk["total"], config["severity_thresholds"])
    interval = risk["interval"]
    key = interval_key_for(interval["top_tvd"], interval["bottom_tvd"])
    spec = EVENT_TYPE_REGISTRY.get(event_type)
    label = spec.label if spec else event_type.replace("_", " ").title()
    formation = next(
        (
            f
            for f in formations
            if f.id == (supporting_events[0].formation_id if supporting_events else None)
        ),
        None,
    )
    formation_name = formation.name if formation else "Unassigned"
    top = interval["top_tvd"]
    bottom = interval["bottom_tvd"]
    current_tvd = current_well.current_tvd

    headline = (
        f"{len(well_ids)} nearby well(s) recorded {label} between {top:,.0f} and "
        f"{bottom:,.0f} m TVD in the {formation_name} Formation; the current hole is at "
        f"{current_tvd:,.0f} m TVD."
    )
    title = (
        f"{label} history at {top:,.0f}–{bottom:,.0f} m TVD across "
        f"{len(well_ids)} offset well(s)"
    )
    reason = (
        f"Rule {f'RULE_{event_type}'} fired: {len(supporting_events)} {label} event(s) "
        f"in {len(well_ids)} offset well(s) fall within {config['tvd_tolerance_m']:.0f} m of "
        f"the current {current_tvd:,.0f} m TVD, in the {formation_name} Formation "
        f"({formation_match}). Risk {risk['total']:.2f} → band {band}."
    )
    return {
        "event_type": event_type,
        "event_label": label,
        "title": title,
        "headline": headline,
        "severity_band": band,
        "risk_score": risk["total"],
        "current_tvd": current_tvd,
        "interval": interval,
        "formation_id": formation.id if formation else None,
        "formation_match": formation_match,
        "supporting_well_count": len(well_ids),
        "supporting_event_count": len(supporting_events),
        "distance_to_interval_m": risk["distance_to_interval_m"],
        "risk_breakdown": risk["breakdown"],
        "risk_factors": risk["factors"],
        "reasons": [
            {
                "alert_id": "",
                "reason": reason,
                "factors": risk["factors"],
                "supporting_wells": sorted(well_ids),
                "supporting_event_ids": [e.id for e in supporting_events],
            }
        ],
        "interval_key": key,
        "rule_id": f"RULE_{event_type}",
        "rule_version": ENGINE_VERSION,
        "supporting_events": list(supporting_events),
        "supporting_offset_ids": sorted(well_ids),
    }


def recompute_well_alerts(
    session: Session,
    well_id: str,
    config: dict[str, Any] | None = None,
    event_types: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Recompute and reconcile every alert for one well.

    Existing alerts whose cluster is still supported are updated in place
    (preserving their status and audit trail); alerts that no longer fire are
    deactivated rather than deleted.

    Returns:
        ``{"offset_relations_updated", "alerts_created", "alerts_updated",
        "alerts", "deactivated"}``.
    """
    rule_config = config or get_risk_config(session)
    current_well = session.get(Well, well_id)
    if current_well is None:
        raise KeyError(f"Unknown well: {well_id}")

    relations = list(
        session.scalars(
            select(OffsetRelation)
            .where(
                OffsetRelation.current_well_id == well_id,
                OffsetRelation.relevance_score >= float(rule_config["min_relevance"]),
            )
            .order_by(OffsetRelation.relevance_score.desc())
        )
    )
    relation_by_well = {relation.offset_well_id: relation for relation in relations}
    offset_ids = list(relation_by_well)
    events_by_well: dict[str, list[DrillingEvent]] = {}
    if offset_ids:
        for event in session.scalars(
            select(DrillingEvent)
            .where(DrillingEvent.well_id.in_(offset_ids))
            .order_by(DrillingEvent.tvd, DrillingEvent.id)
        ):
            events_by_well.setdefault(event.well_id, []).append(event)
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))

    # Only hazard types can raise a proactive alert.  ``FORMATION_TRANSITION`` is
    # stratigraphy: it still drives the timeline, the formation column, search and
    # the offset replay, but alerting on "the rock changed" is noise.  The gate
    # lives in the event-type registry, so a new non-hazard type is one line.
    candidates = [
        code for code in (event_types or sorted(EVENT_TYPE_REGISTRY)) if is_alertable(code)
    ]
    seen_ids: set[str] = set()
    preexisting = {
        alert.id
        for alert in session.scalars(select(Alert).where(Alert.current_well_id == well_id))
    }
    created = 0

    for event_type in candidates:
        pooled: list[DrillingEvent] = []
        for well_id_offset in offset_ids:
            matching = [e for e in events_by_well.get(well_id_offset, []) if e.event_type == event_type]
            pooled.extend(matching)
        supporting = _grow_interval(
            pooled, current_well.current_tvd, float(rule_config["tvd_tolerance_m"])
        )
        if not supporting:
            continue
        supporting_offsets = [
            relation_by_well[e.well_id] for e in supporting if e.well_id in relation_by_well
        ]
        alert_payload = build_alert(
            session,
            current_well,
            event_type,
            supporting,
            supporting_offsets,
            rule_config,
            formations,
        )
        if alert_payload is None:
            continue
        alert = _upsert_alert(session, current_well, alert_payload, rule_config)
        if alert.id not in preexisting:
            created += 1
        seen_ids.add(alert.id)

    existing = {
        alert.id: alert
        for alert in session.scalars(select(Alert).where(Alert.current_well_id == well_id))
    }
    deactivated = 0
    for alert_id, alert in existing.items():
        if alert_id not in seen_ids and alert.is_active:
            alert.is_active = False
            alert.updated_at = utcnow()
            deactivated += 1
    session.flush()

    alerts = list(
        session.scalars(
            select(Alert)
            .where(Alert.current_well_id == well_id, Alert.is_active.is_(True))
            .order_by(Alert.risk_score.desc(), Alert.id)
        )
    )
    return {
        "offset_relations_updated": len(relations),
        "alerts_created": created,
        "alerts_updated": len(alerts) - created,
        "alerts": alerts,
        "deactivated": deactivated,
    }


def _upsert_alert(
    session: Session,
    current_well: Well,
    payload: dict[str, Any],
    config: dict[str, Any],
) -> Alert:
    """Insert or update the alert row for a candidate payload.

    Reconciliation is by deterministic id, so a recompute never duplicates an
    alert.  Status, actions and ``created_at`` are preserved for existing rows.
    """
    alert_id = alert_id_for(current_well.id, payload["event_type"], payload["interval_key"])
    reasons = []
    for reason in payload["reasons"]:
        reason = dict(reason)
        reason["alert_id"] = alert_id
        reasons.append(reason)

    alert = session.get(Alert, alert_id)
    if alert is None:
        alert = Alert(id=alert_id, current_well_id=current_well.id, created_at=utcnow())
        session.add(alert)
    alert.event_type = payload["event_type"]
    alert.title = payload["title"]
    alert.headline = payload["headline"]
    alert.severity_band = payload["severity_band"]
    alert.risk_score = payload["risk_score"]
    alert.current_tvd = payload["current_tvd"]
    alert.interval_top_tvd = payload["interval"]["top_tvd"]
    alert.interval_bottom_tvd = payload["interval"]["bottom_tvd"]
    alert.formation_id = payload["formation_id"]
    alert.formation_match = payload["formation_match"]
    alert.supporting_well_count = payload["supporting_well_count"]
    alert.supporting_event_count = payload["supporting_event_count"]
    alert.distance_to_interval_m = payload["distance_to_interval_m"]
    alert.risk_breakdown = payload["risk_breakdown"]
    alert.risk_factors = payload["risk_factors"]
    alert.reasons = reasons
    alert.interval_key = payload["interval_key"]
    alert.rule_id = payload["rule_id"]
    alert.rule_version = payload["rule_version"]
    alert.is_active = True
    alert.updated_at = utcnow()
    session.flush()

    session.execute(delete(AlertEventLink).where(AlertEventLink.alert_id == alert.id))
    for event in payload["supporting_events"]:
        distance = abs(event.tvd - alert.current_tvd)
        session.add(
            AlertEventLink(
                alert_id=alert.id,
                event_id=event.id,
                well_id=event.well_id,
                weight=event.severity_score,
                tvd=event.tvd,
                distance_m=round(distance, 1),
            )
        )
    session.flush()
    return alert


def recompute_all_alerts(
    session: Session, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Recompute alerts for every active (or supplied) well.

    Returns:
        A summary with per-well counts and the total number of active alerts.
    """
    rule_config = config or get_risk_config(session)
    well_ids = list(
        session.scalars(select(Well.id).where(Well.is_active.is_(True)).order_by(Well.id))
    )
    if not well_ids:
        well_ids = list(session.scalars(select(Well.id).order_by(Well.id)))
    total = 0
    for well_id in well_ids:
        result = recompute_well_alerts(session, well_id, rule_config)
        total += len(result["alerts"])
    return {
        "wells_processed": len(well_ids),
        "alerts": total,
    }


# --------------------------------------------------------------------------- #
# Decision panel (q1..q6)
# --------------------------------------------------------------------------- #

def _well_summary(session: Session, well: Well, well_id: str) -> dict[str, Any]:
    """Return a ``WellSummary`` for the q3 panel; never ``None``.

    The UI reads ``.well.name`` directly, so a missing relation must still
    produce a well object rather than null.
    """
    from .services import summarize_well

    if well is None:
        return {
            "id": well_id,
            "name": well_id,
            "field": None,
            "block": None,
            "latitude": None,
            "longitude": None,
            "status": None,
            "current_depth_md": None,
            "current_tvd": None,
            "current_formation": None,
            "operator": None,
            "well_type": None,
            "spud_date": None,
            "water_depth_m": None,
            "is_active": False,
            "offset_well_count": None,
            "relevant_event_count": 0,
            "top_alert_severity": None,
            "data_provenance": DATA_PROVENANCE,
        }
    return summarize_well(session, well)



def _event_refs(events: Sequence[DrillingEvent]) -> list[dict[str, Any]]:
    """Return compact event references for the decision panel."""
    return [
        {
            "event_id": event.id,
            "well_id": event.well_id,
            "event_type": event.event_type,
            "md": event.md,
            "tvd": event.tvd,
            "severity": event.severity,
            "document_id": event.document_id,
        }
        for event in events
    ]


def _document_ref(document: Document | None) -> str:
    """Return a human document reference such as ``DDR-17 p.43`` or ``(no page)``."""
    if document is None:
        return "no document reference in the record"
    page = next(
        (e.page for e in (document.evidence or []) if e.page is not None),
        None,
    )
    if page is None:
        return f"{document.doc_type} {document.id} (page not available)"
    return f"{document.doc_type} {document.id} p.{page}"


def build_decision_panel(session: Session, alert: Alert) -> dict[str, Any]:
    """Build the ``q1..q6`` engineer decision payload for one alert.

    Every generated sentence carries an explicit ``provenance``.  Nothing here
    calls a model: ``q6`` items that are not sourced from a document are marked
    ``SYSTEM_RULE`` and phrased as things to review, not as instructions.
    """
    config = get_risk_config(session)
    links = list(alert.event_links)
    supporting_events = [link.event for link in links if link.event is not None]
    supporting_wells = sorted({event.well_id for event in supporting_events})
    relations = {
        relation.offset_well_id: relation
        for relation in session.scalars(
            select(OffsetRelation).where(
                OffsetRelation.current_well_id == alert.current_well_id,
                OffsetRelation.offset_well_id.in_(supporting_wells) if supporting_wells else False,
            )
        )
    }
    formation_name = alert.formation.name if alert.formation is not None else "Unassigned"
    spec = EVENT_TYPE_REGISTRY.get(alert.event_type)
    event_label = spec.label if spec else alert.event_type.replace("_", " ").title()
    top = alert.interval_top_tvd
    bottom = alert.interval_bottom_tvd

    # q1 — what happened.  The contract fields come first; the extra keys are
    # additive conveniences for the UI, not replacements.
    q1_text = (
        f"Across {alert.supporting_well_count} offset well(s) there are "
        f"{alert.supporting_event_count} recorded {event_label} event(s) between "
        f"{top:,.0f} and {bottom:,.0f} m TVD in the {formation_name} Formation. "
        f"The current hole in {alert.well.name if alert.well else alert.current_well_id} "
        f"is at {alert.current_tvd:,.0f} m TVD, "
        f"{alert.distance_to_interval_m:,.0f} m from that interval."
    )
    q1 = {
        "headline": alert.headline,
        "detail": q1_text,
        "event_ids": [event.id for event in supporting_events],
        "current_tvd": alert.current_tvd,
        "formation": formation_name,
        "formation_match": alert.formation_match,
        "text": q1_text,
        "event_type": alert.event_type,
        "event_label": event_label,
        "interval": {"top_tvd": top, "bottom_tvd": bottom},
        "supporting_event_count": alert.supporting_event_count,
        "supporting_well_count": alert.supporting_well_count,
        "severity_band": alert.severity_band,
        "risk_score": alert.risk_score,
        "events": _event_refs(supporting_events),
        "provenance": "SYSTEM_RULE",
    }

    # q2 — why relevant now
    q2 = [
        {
            "text": (
                f"Risk score {alert.risk_score:.2f} maps to severity band "
                f"{alert.severity_band} under the engine thresholds."
            ),
            "code": "RISK_SCORE",
            "provenance": "SYSTEM_RULE",
        }
    ]
    for factor in alert.risk_factors:
        q2.append(
            {
                "text": f"{factor['label']}: {factor['detail']}",
                "code": factor["code"],
                "value": factor["value"],
                "weight": factor["weight"],
                "contribution": factor["contribution"],
                "provenance": "SYSTEM_RULE",
            }
        )
    for well_id, relation in sorted(relations.items()):
        q2.append(
            {
                "text": (
                    f"{well_id} is relevant because {relation.why_relevant[0]}"
                    if relation.why_relevant
                    else f"{well_id} is within the configured relevance threshold."
                ),
                "code": "OFFSET_RELEVANCE",
                "well_id": well_id,
                "relevance_score": relation.relevance_score,
                "relevance_band": relation.relevance_band,
                "provenance": "SYSTEM_RULE",
            }
        )

    # q3 — supporting wells.  The contract's OffsetWell-lite: the UI reads
    # ``.well.name`` and renders ``factors[]``, so both must be present.
    q3: list[dict[str, Any]] = []
    for well_id in supporting_wells:
        relation = relations.get(well_id)
        offset_well = relation.offset_well if relation is not None else session.get(Well, well_id)
        well_events = [event for event in supporting_events if event.well_id == well_id]
        q3.append(
            {
                "well": _well_summary(session, offset_well, well_id),
                "well_id": well_id,
                "distance_km": relation.distance_km if relation is not None else None,
                "relevance_score": relation.relevance_score if relation is not None else None,
                "relevance_band": relation.relevance_band if relation is not None else None,
                "why_relevant": list(relation.why_relevant or []) if relation is not None else [],
                "factors": list(relation.factors or []) if relation is not None else [],
                "similarity": (
                    {
                        "formation_similarity": relation.formation_similarity,
                        "depth_similarity": relation.depth_similarity,
                        "spatial_proximity": relation.spatial_proximity,
                        "event_similarity": relation.event_similarity,
                    }
                    if relation is not None
                    else {}
                ),
                "event_count": len(well_events),
                "event_ids": [event.id for event in well_events],
                "events": _event_refs(well_events),
            }
        )

    # q4 — evidence
    q4: list[dict[str, Any]] = []
    for event in supporting_events:
        for evidence in event.evidence or []:
            document = evidence.document or event.document
            q4.append(
                {
                    "evidence_id": evidence.id,
                    "event_id": event.id,
                    "well_id": event.well_id,
                    "well_name": event.well.name if event.well else event.well_id,
                    "page": evidence.page,
                    "section": evidence.section,
                    "text_span": evidence.text_span,
                    "confidence": evidence.confidence,
                    "extraction_method": evidence.extraction_method,
                    "document_id": document.id if document else None,
                    "document_ref": _document_ref(document),
                    "alert_id": alert.id,
                }
            )

    # q5 — historical mitigations, quoted from the source records
    q5 = [
        {
            "text": event.mitigation,
            "source_event_id": event.id,
            "source_document": _document_ref(event.document),
            "provenance": "SOURCE_DOCUMENT",
        }
        for event in supporting_events
        if event.mitigation
    ]

    # q6 — what to review, derived from the rule (never from a model here)
    q6: list[dict[str, Any]] = [
        {
            "text": (
                f"Review the {alert.supporting_event_count} {event_label} event(s) "
                f"recorded between {top:,.0f} and {bottom:,.0f} m TVD in "
                f"{', '.join(supporting_wells)}."
            ),
            "provenance": "SYSTEM_RULE",
            "code": "REVIEW_HISTORY",
        },
    ]
    if alert.well is not None:
        q6.append(
            {
                "text": (
                    f"Confirm the current {alert.well.mud_weight_ppg:.1f} ppg mud "
                    f"weight and section programme before entering the {formation_name} "
                    f"interval at {top:,.0f}–{bottom:,.0f} m TVD."
                ),
                "provenance": "SYSTEM_RULE",
                "code": "REVIEW_MUD_PROGRAMME",
            }
        )
    if alert.supporting_event_count >= 3:
        q6.append(
            {
                "text": (
                    f"{alert.supporting_well_count} independent wells recorded "
                    f"{event_label} in the same interval; consider treating this as a "
                    f"recurring hazard rather than an isolated event."
                ),
                "provenance": "SYSTEM_RULE",
                "code": "REVIEW_RECURRENCE",
            }
        )
    for mitigation in q5[:3]:
        q6.append(
            {
                "text": mitigation["text"],
                "provenance": "SOURCE_DOCUMENT",
                "code": "REVIEW_MITIGATION",
                "source_event_id": mitigation["source_event_id"],
            }
        )

    return {
        "q1_what_happened": q1,
        "q2_why_relevant_now": q2,
        "q3_supporting_wells": q3,
        "q4_evidence": q4,
        "q5_historical_mitigation": q5,
        "q6_what_to_review": q6,
    }


# --------------------------------------------------------------------------- #
# Engineer actions
# --------------------------------------------------------------------------- #


def next_action_id(session: Session) -> str:
    """Return the next deterministic ``EA-nnnn`` identifier."""
    count = session.scalar(select(func.count()).select_from(EngineerAction)) or 0
    return f"EA-{count + 1:04d}"


def record_action(
    session: Session,
    alert: Alert,
    engineer: str,
    action_type: str,
    note: str = "",
    from_status: str | None = None,
    to_status: str | None = None,
) -> EngineerAction:
    """Append an audit-trail action to an alert."""
    action = EngineerAction(
        id=next_action_id(session),
        alert_id=alert.id,
        engineer=engineer,
        action_type=action_type,
        note=note,
        from_status=from_status,
        to_status=to_status,
    )
    session.add(action)
    session.flush()
    return action


def count_evidence_for_events(session: Session, event_ids: Sequence[str]) -> int:
    """Return the number of distinct events that have at least one evidence row."""
    if not event_ids:
        return 0
    rows = session.execute(
        select(Evidence.event_id).where(Evidence.event_id.in_(event_ids)).distinct()
    ).scalars()
    return len(set(rows))

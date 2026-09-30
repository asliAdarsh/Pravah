"""Service layer assembling the contract's composite read models.

Routers stay validation-and-delegation; the logic that combines relevance,
events, alerts and evidence into one payload lives here.  Everything it returns
is a plain dict, so the routers never re-derive an arithmetic value.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import DATA_PROVENANCE, ENGINE_VERSION
from .event_types import EVENT_TYPE_REGISTRY, non_alertable_event_types
from .models import (
    AnomalyAlert,
    TelemetrySample,
    Alert,
    AlertEventLink,
    Document,
    DrillingEvent,
    Evidence,
    Formation,
    OffsetRelation,
    Well,
)
from .relevance import get_relevance_config, source_availability
from .risk import METHOD, top_band
from .schemas import (
    alert_brief,
    document_brief,
    event_brief,
    event_detail,
    evidence_brief,
    offset_well_out,
    well_full,
    well_summary,
)

__all__ = [
    "build_alert_detail",
    "build_demo_scenario",
    "build_evidence_chain",
    "build_meta",
    "build_nearby",
    "build_offset_replay",
    "build_timeline",
    "summarize_well",
    "well_counts",
]


def _count(session: Session, model: Any) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def well_counts(session: Session, well_id: str) -> tuple[int, int, str | None]:
    """Return ``(offset_well_count, relevant_event_count, top_alert_severity)``."""
    offset_count = _count(
        session,
        select(OffsetRelation)
        .where(OffsetRelation.current_well_id == well_id)
        .subquery(),
    )
    relevant_events = _count(
        session,
        select(DrillingEvent)
        .where(
            DrillingEvent.well_id.in_(
                select(OffsetRelation.offset_well_id).where(
                    OffsetRelation.current_well_id == well_id
                )
            )
        )
        .subquery(),
    )
    bands = [
        row[0]
        for row in session.execute(
            select(Alert.severity_band).where(
                Alert.current_well_id == well_id, Alert.is_active.is_(True)
            )
        )
    ]
    return offset_count, relevant_events, top_band(bands)


def summarize_well(session: Session, well: Well) -> dict[str, Any]:
    """Return a ``WellSummary`` with its aggregate counts filled in."""
    counts = well_counts(session, well.id)
    return well_summary(well, *counts)


def build_meta(
    session: Session, current_well_id: str | None = None
) -> dict[str, Any]:
    """Build the ``GET /api/v1/meta`` payload."""
    from .config import APP_NAME, APP_VERSION, DATASET_LABEL
    from .datasources import load_source_registry
    from .seed_real import VOLVE_TELEMETRY_WELL
    from .event_types import EVENT_TYPE_REGISTRY, non_alertable_event_types
    from .llm import llm_status
    from .risk import get_risk_config
    from .schemas import RELEVANCE_BAND_META, SEVERITY_BAND_META

    counts = {
        "wells": _count(session, Well),
        "formations": _count(session, Formation),
        "events": _count(session, DrillingEvent),
        "documents": _count(session, Document),
        "alerts": _count(session, Alert),
        "evidence": _count(session, Evidence),
        "offset_relations": _count(session, OffsetRelation),
        "telemetry_samples": _count(session, TelemetrySample),
        "anomaly_alerts": _count(session, AnomalyAlert),
    }
    if current_well_id is None:
        current = session.scalar(
            select(Well).where(Well.is_active.is_(True)).limit(1)
        )
    else:
        current = session.get(Well, current_well_id)
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "dataset_label": DATASET_LABEL,
        "data_provenance": DATA_PROVENANCE,
        "engine_version": ENGINE_VERSION,
        "counts": counts,
        "event_types": [spec.to_dict() for spec in EVENT_TYPE_REGISTRY.values()],
        "non_alertable_event_types": non_alertable_event_types(),
        "severity_bands": SEVERITY_BAND_META,
        "relevance_bands": RELEVANCE_BAND_META,
        "relevance_config": get_relevance_config(session),
        "risk_config": get_risk_config(session),
        "llm": llm_status(),
        "data_sources": load_source_registry(),
        "demo": {
            "current_well_id": current.id if current else VOLVE_TELEMETRY_WELL,
            "scenario": (
                f"{current.name} is drilling at {current.current_tvd:,.0f} m TVD in the "
                f"{current.formation.name if current.formation else 'unassigned'} Formation. "
                f"Compare against the nearest offset wells to see what comparable history "
                f"exists at this depth."
                if current
                else "Demo well not found."
            ),
        },
        "method": METHOD,
    }


# --------------------------------------------------------------------------- #
# Nearby offsets
# --------------------------------------------------------------------------- #


def _relevant_events_for(
    session: Session,
    relation: OffsetRelation,
    current_tvd: float,
    window_m: float = 400.0,
    event_type: str | None = None,
    limit: int = 10,
) -> list[DrillingEvent]:
    """Return an offset well's events closest to the current TVD."""
    stmt = select(DrillingEvent).where(DrillingEvent.well_id == relation.offset_well_id)
    if event_type:
        stmt = stmt.where(DrillingEvent.event_type == event_type)
    else:
        stmt = stmt.where(
            DrillingEvent.tvd >= current_tvd - window_m,
            DrillingEvent.tvd <= current_tvd + window_m,
        )
    stmt = stmt.order_by(DrillingEvent.tvd, DrillingEvent.id).limit(limit)
    return list(session.scalars(stmt))


def build_nearby(
    session: Session,
    current_well: Well,
    radius_km: float | None = None,
    min_relevance: float | None = None,
    event_type: str | None = None,
    formation: str | None = None,
    status: str | None = None,
    depth_min_md: float | None = None,
    depth_max_md: float | None = None,
) -> dict[str, Any]:
    """Build the ``GET /wells/{id}/nearby`` payload from the relation cache."""
    config = get_relevance_config(session)
    radius = float(radius_km if radius_km is not None else config["radius_km"])
    threshold = float(
        min_relevance if min_relevance is not None else config["min_relevance"]
    )
    stmt = select(OffsetRelation).where(
        OffsetRelation.current_well_id == current_well.id,
        OffsetRelation.relevance_score >= threshold,
    )
    if status:
        stmt = stmt.where(
            OffsetRelation.offset_well_id.in_(select(Well.id).where(Well.status == status))
        )
    relations = [r for r in session.scalars(stmt.order_by(OffsetRelation.relevance_score.desc()))]
    relations = [r for r in relations if r.distance_km <= radius]

    formation_id = None
    if formation:
        found = session.scalar(select(Formation).where(Formation.name == formation))
        formation_id = found.id if found else -1

    items: list[dict[str, Any]] = []
    for relation in relations:
        well = relation.offset_well
        if formation_id is not None:
            events = list(
                session.scalars(
                    select(DrillingEvent)
                    .join(Formation, DrillingEvent.formation_id == Formation.id)
                    .where(
                        DrillingEvent.well_id == well.id, Formation.id == formation_id
                    )
                    .order_by(DrillingEvent.tvd)
                    .limit(20)
                )
            )
            if not events:
                continue
        elif depth_min_md is not None or depth_max_md is not None:
            events = list(
                session.scalars(
                    select(DrillingEvent).where(
                        DrillingEvent.well_id == well.id,
                        *(
                            [DrillingEvent.md >= depth_min_md]
                            if depth_min_md is not None
                            else []
                        ),
                        *(
                            [DrillingEvent.md <= depth_max_md]
                            if depth_max_md is not None
                            else []
                        ),
                    )
                )
            )
            if not events:
                continue
        else:
            events = []
        relevant = _relevant_events_for(
            session, relation, current_well.current_tvd, event_type=event_type
        )
        document_count = _count(
            session,
            select(Document).where(Document.well_id == well.id).subquery(),
        )
        items.append(
            offset_well_out(
                relation,
                relevant_events=relevant,
                document_count=document_count,
                source=source_availability(session, relevant or events),
            )
        )
    return {
        "current_well": summarize_well(session, current_well),
        "radius_km": radius,
        "min_relevance": threshold,
        "weights": config["weights"],
        "method": METHOD,
        "items": items,
        "data_provenance": DATA_PROVENANCE,
    }


# --------------------------------------------------------------------------- #
# Timeline
# --------------------------------------------------------------------------- #


def build_timeline(
    session: Session,
    current_well: Well,
    window_md: float = 300.0,
    event_type: str | None = None,
) -> dict[str, Any]:
    """Build the depth-aware merged timeline across current and offset wells."""
    top_md = max(0.0, current_well.current_depth_md - window_md)
    bottom_md = current_well.current_depth_md + window_md
    entries: list[dict[str, Any]] = []

    def add_events(well: Well, origin: str) -> None:
        stmt = select(DrillingEvent).where(
            DrillingEvent.well_id == well.id,
            DrillingEvent.md >= top_md,
            DrillingEvent.md <= bottom_md,
        )
        if event_type:
            stmt = stmt.where(DrillingEvent.event_type == event_type)
        for event in session.scalars(stmt.order_by(DrillingEvent.md, DrillingEvent.id)):
            delta = round(event.md - current_well.current_depth_md, 1)
            if delta < 0:
                note = f"{abs(delta):.0f} m shallower than current depth"
            elif delta > 0:
                note = f"{delta:.0f} m deeper than current depth"
            else:
                note = "at the current depth"
            entries.append(
                {
                    "id": event.id,
                    "kind": "drilling_event",
                    "origin": origin,
                    "well_id": well.id,
                    "well_name": well.name,
                    "event_type": event.event_type,
                    "event_label": event_brief(event)["event_label"],
                    "md": event.md,
                    "tvd": event.tvd,
                    "formation": event.formation.name if event.formation else None,
                    "severity": event.severity,
                    "severity_score": event.severity_score,
                    "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                    "description": event.description,
                    "mitigation": event.mitigation,
                    "delta_from_current_md": delta,
                    "relevance_note": note,
                    "document_id": event.document_id,
                    "evidence_count": len(event.evidence or []),
                }
            )

    add_events(current_well, "CURRENT")
    relations = list(
        session.scalars(
            select(OffsetRelation)
            .where(OffsetRelation.current_well_id == current_well.id)
            .order_by(OffsetRelation.relevance_score.desc())
            .limit(6)
        )
    )
    for relation in relations:
        add_events(relation.offset_well, "OFFSET")

    # Formation transitions crossing the window.
    for formation in session.scalars(
        select(Formation).order_by(Formation.top_depth)
    ):
        if not (top_md - 200 <= formation.top_depth <= bottom_md + 200):
            continue
        entries.append(
            {
                "id": f"FT-{formation.code}-{int(formation.top_depth)}",
                "kind": "formation_transition",
                "origin": "FORMATIONS",
                "well_id": None,
                "well_name": formation.name,
                "event_type": "FORMATION_TRANSITION",
                "event_label": f"Top of {formation.name}",
                "md": formation.top_depth,
                "tvd": formation.top_depth,
                "formation": formation.name,
                "severity": "LOW",
                "severity_score": 0.2,
                "occurred_at": None,
                "description": formation.description,
                "mitigation": "",
                "delta_from_current_md": round(formation.top_depth - current_well.current_depth_md, 1),
                "relevance_note": "Mapped formation top",
                "document_id": None,
                "evidence_count": 0,
            }
        )

    entries.append(
        {
            "id": f"CM-{current_well.id}",
            "kind": "current_marker",
            "origin": "CURRENT",
            "well_id": current_well.id,
            "well_name": current_well.name,
            "event_type": "CURRENT_DEPTH",
            "event_label": "Current depth",
            "md": current_well.current_depth_md,
            "tvd": current_well.current_tvd,
            "formation": current_well.formation.name if current_well.formation else None,
            "severity": "LOW",
            "severity_score": 0.0,
            "occurred_at": None,
            "description": f"Current hole position for {current_well.name}.",
            "mitigation": "",
            "delta_from_current_md": 0.0,
            "relevance_note": "YOU ARE HERE",
            "document_id": None,
            "evidence_count": 0,
        }
    )
    entries.sort(key=lambda e: (e["md"], e["kind"] != "current_marker"))
    return {
        "current_well": summarize_well(session, current_well),
        "window": {
            "top_md": top_md,
            "bottom_md": bottom_md,
            "tvd_at_current": current_well.current_tvd,
        },
        "current_marker": {
            "md": current_well.current_depth_md,
            "tvd": current_well.current_tvd,
            "formation": current_well.formation.name if current_well.formation else None,
            "label": "YOU ARE HERE",
        },
        "entries": entries,
        "method": METHOD,
        "data_provenance": DATA_PROVENANCE,
    }


# --------------------------------------------------------------------------- #
# Evidence chain
# --------------------------------------------------------------------------- #


def build_evidence_chain(session: Session, event: DrillingEvent) -> dict[str, Any]:
    """Build the ``GET /evidence/{event_id}`` audit chain.

    Alert → Reason → Event → Well → Document → Evidence.  ``page`` is passed
    through untouched: a missing page is reported as ``null``, never guessed.
    """
    alerts = list(
        session.scalars(
            select(Alert)
            .join(AlertEventLink, Alert.id == AlertEventLink.alert_id)
            .where(AlertEventLink.event_id == event.id)
            .order_by(Alert.created_at, Alert.id)
        )
    )
    reasons: list[dict[str, Any]] = []
    for alert in alerts:
        for reason in alert.reasons or []:
            if event.id in (reason.get("supporting_event_ids") or []):
                reasons.append(
                    {
                        "alert_id": alert.id,
                        "reason": reason.get("reason", ""),
                        "factors": reason.get("factors", []),
                    }
                )
    evidence_rows = list(event.evidence or [])
    siblings = [
        other
        for other in session.scalars(
            select(DrillingEvent).where(DrillingEvent.well_id == event.well_id)
        )
        if other.id != event.id
    ]
    siblings.sort(key=lambda e: e.md)
    previous = next((e for e in reversed(siblings) if e.md < event.md), None)
    following = next((e for e in siblings if e.md > event.md), None)
    excerpt = evidence_rows[0].text_span if evidence_rows else (event.document.excerpt if event.document else "")
    return {
        "event": event_detail(event),
        "chain": {
            "alert_ids": [alert.id for alert in alerts],
            "reasons": reasons,
            "well": summarize_well(session, event.well),
            "document": document_brief(event.document),
            "evidence": [evidence_brief(row) for row in evidence_rows],
        },
        "document": document_brief(event.document),
        "context": {
            "previous_event": event_brief(previous) if previous else None,
            "next_event": event_brief(following) if following else None,
            "document_excerpt": excerpt,
        },
        "evidence": [evidence_brief(row) for row in evidence_rows],
        "data_provenance": DATA_PROVENANCE,
    }


# --------------------------------------------------------------------------- #
# Offset replay
# --------------------------------------------------------------------------- #


def build_offset_replay(
    session: Session,
    current_well: Well,
    radius_km: float | None = None,
    event_type: str | None = None,
    top_tvd: float | None = None,
    bottom_tvd: float | None = None,
    limit_wells: int = 8,
) -> dict[str, Any]:
    """Build the ``GET /offset-replay/{well_id}`` payload."""
    config = get_relevance_config(session)
    radius = float(radius_km if radius_km is not None else config["radius_km"])
    top = float(top_tvd if top_tvd is not None else max(0.0, current_well.current_tvd - 300))
    bottom = float(
        bottom_tvd if bottom_tvd is not None else current_well.current_tvd + 300
    )
    relations = [
        r
        for r in session.scalars(
            select(OffsetRelation)
            .where(OffsetRelation.current_well_id == current_well.id)
            .order_by(OffsetRelation.relevance_score.desc())
        )
        if r.distance_km <= radius
    ][:limit_wells]

    current_formation = current_well.formation
    offset_payloads: list[dict[str, Any]] = []
    hazard_index: dict[str, dict[str, Any]] = {}

    for relation in relations:
        well = relation.offset_well
        stmt = select(DrillingEvent).where(
            DrillingEvent.well_id == well.id,
            DrillingEvent.tvd >= top,
            DrillingEvent.tvd <= bottom,
        )
        if event_type:
            stmt = stmt.where(DrillingEvent.event_type == event_type)
        events = list(session.scalars(stmt.order_by(DrillingEvent.tvd, DrillingEvent.id)))
        for event in events:
            bucket = hazard_index.setdefault(
                event.event_type,
                {
                    "event_type": event.event_type,
                    "event_label": event_brief(event)["event_label"],
                    "well_count": 0,
                    "event_count": 0,
                    "tvd_interval": {"top": event.tvd, "bottom": event.tvd},
                    "depth_below_current_m": None,
                    "wells": [],
                },
            )
            bucket["event_count"] += 1
            bucket["tvd_interval"]["top"] = min(bucket["tvd_interval"]["top"], event.tvd)
            bucket["tvd_interval"]["bottom"] = max(bucket["tvd_interval"]["bottom"], event.tvd)
            if well.id not in bucket["wells"]:
                bucket["wells"].append(well.id)
                bucket["well_count"] = len(bucket["wells"])
        offset_payloads.append(
            {
                "well": well_summary(well),
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
                "events": [event_detail(event) for event in events],
                "formation_alignment": (
                    "SAME"
                    if current_formation is not None
                    and current_formation.name
                    in {
                        f.name
                        for f in session.scalars(
                            select(Formation).where(
                                Formation.bottom_depth > top, Formation.top_depth < bottom
                            )
                        )
                    }
                    else "ADJACENT"
                ),
            }
        )

    current_stmt = select(DrillingEvent).where(
        DrillingEvent.well_id == current_well.id,
        DrillingEvent.tvd >= top,
        DrillingEvent.tvd <= bottom,
    )
    if event_type:
        current_stmt = current_stmt.where(DrillingEvent.event_type == event_type)
    current_events = list(session.scalars(current_stmt.order_by(DrillingEvent.tvd)))

    for bucket in hazard_index.values():
        if bucket["tvd_interval"]["top"] > current_well.current_tvd:
            bucket["depth_below_current_m"] = round(
                bucket["tvd_interval"]["top"] - current_well.current_tvd, 1
            )
    recurring = sorted(
        hazard_index.values(), key=lambda b: (-b["well_count"], -b["event_count"])
    )

    alerts = list(
        session.scalars(
            select(Alert)
            .where(Alert.current_well_id == current_well.id, Alert.is_active.is_(True))
            .order_by(Alert.risk_score.desc())
        )
    )
    return {
        "current_well": well_full(current_well, *well_counts(session, current_well.id)),
        "window": {"top_tvd": top, "bottom_tvd": bottom},
        "formation_column": [
            {
                "id": formation.id,
                "name": formation.name,
                "top_depth": formation.top_depth,
                "bottom_depth": formation.bottom_depth,
                "lithology": formation.lithology,
                "current": bool(
                    current_formation is not None and formation.id == current_formation.id
                ),
            }
            for formation in session.scalars(select(Formation).order_by(Formation.top_depth))
        ],
        "offset_wells": offset_payloads,
        "current_events": [event_detail(event) for event in current_events],
        "recurring_hazards": recurring,
        "alerts": [alert_brief(alert) for alert in alerts],
        "method": METHOD,
        "data_provenance": DATA_PROVENANCE,
    }


# --------------------------------------------------------------------------- #
# Alert detail
# --------------------------------------------------------------------------- #


def build_alert_detail(session: Session, alert: Alert) -> dict[str, Any]:
    """Build the richest endpoint: the engineer decision panel."""
    from .risk import DISCLAIMER, build_decision_panel, get_risk_config
    from .llm import synthesize
    from .schemas import engineer_action_out

    config = get_risk_config(session)
    decision = build_decision_panel(session, alert)
    links = list(alert.event_links)
    supporting_events = [link.event for link in links if link.event is not None]

    chain = [{"step": "ALERT", "ref": alert.id, "detail": alert.title}]
    for reason in alert.reasons or []:
        chain.append(
            {
                "step": "REASON",
                "ref": alert.id,
                "detail": reason.get("reason", ""),
            }
        )
    for event in supporting_events:
        chain.append(
            {
                "step": "EVENT",
                "ref": event.id,
                "detail": f"{event.well_id} {event.event_type} at {event.tvd:,.0f} m TVD",
            }
        )
    for well_id in sorted({event.well_id for event in supporting_events}):
        chain.append(
            {
                "step": "WELL",
                "ref": well_id,
                "detail": (session.get(Well, well_id).name if session.get(Well, well_id) else well_id),
            }
        )
    for event in supporting_events:
        if event.document is not None:
            chain.append(
                {
                    "step": "DOCUMENT",
                    "ref": event.document.id,
                    "detail": event.document.title,
                }
            )
    # A telemetry-sourced alert has no document behind it: its evidence is the
    # measured channel. Name that channel and its depth so the chain is still
    # traceable to a record rather than ending at the reason.
    if (alert.risk_breakdown or {}).get("source") == "REAL_TELEMETRY":
        from .models import AnomalyAlert

        top = session.scalar(
            select(AnomalyAlert)
            .where(
                AnomalyAlert.well_id == alert.current_well_id,
                AnomalyAlert.hazard == alert.event_type,
                AnomalyAlert.md <= (alert.interval_bottom_tvd or 0.0),
                AnomalyAlert.md >= (alert.interval_top_tvd or 0.0),
            )
            .order_by(AnomalyAlert.md)
            .limit(1)
        )
        if top is not None:
            chain.append(
                {
                    "step": "TELEMETRY",
                    "ref": top.id,
                    "detail": (
                        f"{top.channel} at {top.md:,.0f} m MD — {top.detector.upper()}, "
                        f"value {top.value:,.2f} vs baseline {top.baseline_mean:,.2f} "
                        f"(threshold {top.threshold:,.2f})"
                    ),
                }
            )
        well_row = session.get(Well, alert.current_well_id)
        if well_row is not None:
            chain.append(
                {
                    "step": "WELL",
                    "ref": well_row.id,
                    "detail": f"{well_row.name} — live telemetry record",
                }
            )
    for event in supporting_events:
        for evidence in event.evidence or []:
            chain.append(
                {
                    "step": "EVIDENCE",
                    "ref": evidence.id,
                    "detail": (
                        f"{evidence.section or 'section unknown'}"
                        + (f", page {evidence.page}" if evidence.page is not None else ", page not available")
                    ),
                }
            )

    summary = synthesize(
        query=alert.title,
        records=[
            {
                "event_type": event.event_type,
                "well_id": event.well_id,
                "tvd": event.tvd,
                "severity": event.severity,
                "mitigation": event.mitigation,
            }
            for event in supporting_events
        ],
        template_text=(
            f"{alert.supporting_well_count} nearby well(s) recorded "
            f"{EVENT_TYPE_REGISTRY[alert.event_type].label if alert.event_type in EVENT_TYPE_REGISTRY else alert.event_type} "
            f"between {alert.interval_top_tvd:,.0f} and {alert.interval_bottom_tvd:,.0f} m TVD; "
            f"the current hole is at {alert.current_tvd:,.0f} m TVD in the "
            f"{alert.formation.name if alert.formation else 'unassigned'} Formation. "
            f"Risk score {alert.risk_score:.2f} maps to band {alert.severity_band}."
        ),
        citations=[
            {
                "event_id": event.id,
                "document_id": event.document_id,
                "page": next(
                    (e.page for e in (event.evidence or []) if e.page is not None), None
                ),
                "label": f"{event.well_id} {event.event_type} @ {event.tvd:,.0f} m TVD",
            }
            for event in supporting_events[:5]
        ],
    )

    return {
        "alert": alert_brief(alert),
        "rule": {
            "rule_id": alert.rule_id,
            "version": alert.rule_version,
            "method": METHOD,
            "config": config,
        },
        "decision": decision,
        "risk_factors": alert.risk_factors or [],
        "risk_breakdown": alert.risk_breakdown or {},
        "supporting_events": [event_detail(event) for event in supporting_events],
        "evidence_chain": chain,
        "actions": [engineer_action_out(action) for action in alert.actions],
        "generated_summary": {
            **summary.to_dict(),
            "disclaimer": DISCLAIMER,
        },
        "data_provenance": DATA_PROVENANCE,
    }


def build_demo_scenario(session: Session, current_well_id: str | None = None) -> dict[str, Any]:
    """Build the one-call demo bundle for a judge's first paint."""
    from .schemas import RELEVANCE_BAND_META, document_summary

    current = (
        session.get(Well, current_well_id)
        if current_well_id
        else session.scalar(select(Well).where(Well.is_active.is_(True)).limit(1))
    )
    if current is None:
        raise KeyError(current_well_id)
    nearby = build_nearby(session, current)
    replay = build_offset_replay(session, current)
    alerts = list(
        session.scalars(
            select(Alert)
            .where(Alert.current_well_id == current.id, Alert.is_active.is_(True))
            .order_by(Alert.risk_score.desc())
        )
    )
    documents = list(
        session.scalars(
            select(Document).where(Document.well_id == current.id).order_by(Document.doc_date.desc())
        )
    )
    return {
        "scenario": build_meta(session, current.id)["demo"],
        "current_well": well_full(current, *well_counts(session, current.id)),
        "nearby_wells": nearby,
        "replay": replay,
        "alerts": [alert_brief(alert) for alert in alerts],
        "documents": [
            document_summary(
                document,
                len(document.events or []),
                len(document.evidence or []),
            )
            for document in documents
        ],
        "event_types": build_meta(session, current.id)["event_types"],
        "severity_bands": build_meta(session, current.id)["severity_bands"],
        "relevance_bands": RELEVANCE_BAND_META,
        "data_provenance": DATA_PROVENANCE,
    }

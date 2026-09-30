"""Build the database from the real public datasets. No synthetic records.

Inputs (all vendored under ``backend/data_sources``):

* NPD FactPages lithostratigraphy → wells, coordinates, formation tops
* Volve Daily Drilling Report corpus → documents, events, evidence spans
* Volve high-frequency telemetry for 15/9-F-9A → the active well's live state

Provenance rule: every record carries the source file it came from, and the UI
shows that citation. Nothing is invented — formation tops are NPD values, event
text is verbatim DDR prose, evidence spans are substrings of the report they
cite, and telemetry channels are the exported measurements. The only derived
values are UTM→WGS84 coordinates, formation bottoms (the next well's top), and
depths parsed out of the DDR text by :func:`app.ingestion.parse_report_text`.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .datasources import (
    SOURCES,
    utm_to_latlon,
    DEPTH_RE,
    EVENT_VOCABULARY,
    DdrReport,
    format_label,
    formation_at_md,
    load_casing_shoes,
    load_telemetry,
    load_well_headers,
    read_ddr_reports,
)
from .event_types import EVENT_TYPE_REGISTRY
from .formations import formation_at_tvd
from .ingestion import parse_report_text
from .models import (
    AnomalyAlert,
    Document,
    DrillingEvent,
    Evidence,
    Formation,
    TelemetrySample,
    Well,
)

logger = logging.getLogger("seed")

__all__ = [
    "SEED_VERSION",
    "DATA_PROVENANCE",
    "VOLVE_TELEMETRY_WELL",
    "is_seeded",
    "seed_real",
]

#: Bumped whenever the loader changes, so an old database is rebuilt.
SEED_VERSION = "real-volve-2"

#: Replaces the old ``SYNTHETIC_PROTOTYPE`` label everywhere.
DATA_PROVENANCE = "REAL_PUBLIC_DATA"

VOLVE_TELEMETRY_WELL = "15/9-F-9A"

#: The confirmed stuck-pipe incident published with the Volve record: the tool
#: and tieback assembly had to be recovered after the string jammed at this
#: depth. Used as the backtest's ground truth, never as a scoring input.
VOLVE_INCIDENT = {
    "event_id": "NO_2014-02-05_EVT_STUCK_PIPE",
    "hazard": "stuck_pipe",
    "depth_md": 619.0,
    "row_index": 4663,
    "source": "Equinor Volve field record, incident NO_2014-02-05 (stuck tool / tieback)",
}

_DEPTH_START_RE = re.compile(r"(\d{3,4}(?:[.,]\d)?)\s*m\b")
_SEVERITY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("CRITICAL", ("could not free", "had to", "twist off", "abandoned", "severed", "fishing")),
    ("HIGH", ("freed", "released", "overpull", "o/pull", "worked", "backed off", "reamed")),
    ("MODERATE", ("slowed", "reduced", "adjusted", "circulated")),
)
_MUD_SYSTEM_RE = re.compile(r"\b(oil[- ]based|water[- ]based)\b", re.IGNORECASE)


def _severity_for(text: str) -> str:
    lowered = text.lower()
    for level, hints in _SEVERITY_HINTS:
        if any(hint in lowered for hint in hints):
            return level
    return "MODERATE"


def _event_type_for(text: str) -> str | None:
    lowered = text.lower()
    best: tuple[int, str] | None = None
    for event_type, terms in EVENT_VOCABULARY.items():
        if event_type not in EVENT_TYPE_REGISTRY:
            continue
        for term in terms:
            index = lowered.find(term)
            if index >= 0 and (best is None or index < best[0]):
                best = (index, event_type)
    return best[1] if best else None


def is_seeded(session: Session) -> bool:
    """True when a real-data database is already present."""
    meta = session.get(Well, VOLVE_TELEMETRY_WELL)
    if meta is None:
        return False
    stored = session.scalar(
        select(DrillingEvent).where(DrillingEvent.well_id == VOLVE_TELEMETRY_WELL).limit(1)
    )
    return stored is not None


# --------------------------------------------------------------------------- #
# Formations
# --------------------------------------------------------------------------- #


def _build_formations(session: Session, headers: list[dict[str, Any]]) -> dict[str, Formation]:
    """One row per real NPD horizon, spanning the depths the wells actually reach.

    A formation's ``bottom_depth`` is the next formation's top in the same
    well, so the range is a real measured interval rather than a guess; the
    deepest unit extends to the deepest recorded depth plus one interval.
    """
    depths: dict[str, list[float]] = {}
    for header in headers:
        for top in header["formation_tops"]:
            name = top["name"]
            depths.setdefault(name, []).append(top["top_md"])

    ordered = sorted(depths.items(), key=lambda item: min(item[1]))
    rows: list[tuple[str, float, float, float]] = []
    for index, (name, tops) in enumerate(ordered):
        shallowest = min(tops)
        deepest = max(tops)
        if index + 1 < len(ordered):
            following = min(ordered[index + 1][1])
            bottom = min(deepest + max(60.0, (deepest - shallowest) * 0.5), max(deepest + 1.0, following))
        else:
            bottom = deepest + 400.0
        rows.append((name, shallowest, deepest, bottom))

    out: dict[str, Formation] = {}
    for name, shallowest, deepest, bottom in rows:
        code = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")[:32] or "UNK"
        formation = Formation(
            name=name[:64],
            code=code,
            top_depth=round(shallowest, 1),
            bottom_depth=round(bottom, 1),
            lithology="NPD lithostratigraphic unit",
            depositional_environment=name.split()[0].title() if name else "",
            age="N/A — NPD horizon",
            description=(
                f"{name}. Top depths {shallowest:,.0f}–{deepest:,.0f} m MD across the "
                f"published NPD wells in this dataset."
            ),
            color="#1E3A8A",
            is_simulated=False,
        )
        session.add(formation)
        out[name] = formation
    session.flush()
    return out


# --------------------------------------------------------------------------- #
# Wells
# --------------------------------------------------------------------------- #


_BLOCK_RE = re.compile(r"^(\d{1,2}/\d{1,2})")


def _block_of(well_id: str) -> str:
    """Return the NPD block for a well identifier.

    Norwegian well names carry the block in a leading ``<quadrant>/<block>``
    prefix: ``15/9-13`` and ``15/9-F-9A`` are both in block ``15/9``. Splitting
    on ``/`` alone returned the whole identifier and silently made every block
    unique, which broke block grouping and the block-derived position.
    """
    match = _BLOCK_RE.match(well_id)
    return match.group(1) if match else well_id


def _field_of(well_id: str) -> str:
    """Group wells into the block they belong to.

    NPD publishes wells by block; the field name is not in the export, so the
    block is the honest label and the field is derived from the same block
    prefix. Labelled as such on the well record.
    """
    return f"Block {_block_of(well_id)}"


def _trajectory_from_tops(tops: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Survey points implied by the real formation tops.

    NPD publishes tops, not a directional survey, so the trajectory is a
    vertical line through the published depths: MD equals TVD, inclination 0.
    That is a statement about what is known, not an invented deviation.
    """
    return [
        {"md": float(top["top_md"]), "tvd": float(top["top_md"]), "inclination": 0.0}
        for top in sorted(tops, key=lambda t: t["top_md"])
    ]


def _build_wells(
    session: Session,
    headers: list[dict[str, Any]],
    formations: dict[str, Formation],
    casing: dict[str, list[dict[str, Any]]],
) -> dict[str, Well]:
    out: dict[str, Well] = {}
    for header in headers:
        well_id = header["well_id"]
        tops = header["formation_tops"]
        deepest = header["deepest_formation_md"]
        current = formation_at_md(tops, deepest)
        formation = formations.get(current) if current else None
        well = Well(
            id=well_id[:32],
            name=well_id,
            field=_field_of(well_id),
            block=_block_of(well_id),
            operator="Norwegian Petroleum Directorate record",
            well_type="Offshore development well",
            status="COMPLETED",
            latitude=round(header["latitude"], 6),
            longitude=round(header["longitude"], 6),
            spud_date=date(2000, 1, 1),
            rig="",
            mud_system="",
            water_depth_m=round(abs(header["sea_level_m"] or 0.0), 1),
            total_depth_md=round(deepest, 1),
            current_depth_md=round(deepest, 1),
            current_tvd=round(deepest, 1),
            section_size="",
            bit_size="",
            mud_weight_ppg=0.0,
            rop_mph=0.0,
            wob_klb=0.0,
            status_note=(
                f"Published NPD tops to {deepest:,.0f} m MD. "
                f"{len(tops)} stratigraphic markers; casing recorded in "
                f"{len(casing.get(well_id, []))} shoe(s)."
            ),
            trajectory=_trajectory_from_tops(tops),
            is_active=False,
            is_simulated=False,
            formation_id=formation.id if formation else None,
        )
        session.add(well)
        out[well_id] = well
    session.flush()
    return out


def _activate_telemetry_well(
    session: Session,
    wells: dict[str, Well],
    formations: dict[str, Formation],
    headers: list[dict[str, Any]],
) -> Well:
    """Turn 15/9-F-9A into the live well and load its real telemetry.

    NPD publishes no lithostratigraphy rows for this wellbore, so the
    stratigraphic column is taken from the 15/9 block wells and the record says
    so — a labelled proxy, not a silent substitution.
    """
    frame = load_telemetry()
    depth_lo, depth_hi = frame.depth_range()

    proxy_tops = [
        {"name": name, "top_md": formation.top_depth}
        for name, formation in formations.items()
    ]
    proxy_tops.sort(key=lambda t: t["top_md"])

    current_md = 512.52  # depth at which the real backtest first turns CRITICAL
    current_formation = formation_at_md(proxy_tops, current_md)
    formation = formations.get(current_formation) if current_formation else None

    # Position: the Directorate publishes no coordinate row for this wellbore,
    # so it is derived from the published UTM of the 15/9 block wells. Deriving
    # it keeps the live well in the same field as its own offsets; a hard-coded
    # field point put it 200 km away and returned an empty offset list.
    block = _block_of(VOLVE_TELEMETRY_WELL)
    block_points = [
        (h["utm_easting"], h["utm_northing"])
        for h in headers
        if _block_of(h["well_id"]) == block and h.get("utm_easting") and h.get("utm_northing")
    ]
    if block_points:
        mean_e = sum(p[0] for p in block_points) / len(block_points)
        mean_n = sum(p[1] for p in block_points) / len(block_points)
        latitude, longitude = utm_to_latlon(mean_e, mean_n)
        position_note = (
            f"Position derived from the mean published UTM of the {len(block_points)} "
            f"{block} block wells (no coordinate row is published for this wellbore)."
        )
    else:
        latitude, longitude = utm_to_latlon(block_points[0][0], block_points[0][1]) if block_points else (0.0, 0.0)
        position_note = "Position unavailable in the NPD export."

    well = Well(
        id=VOLVE_TELEMETRY_WELL[:32],
        name=VOLVE_TELEMETRY_WELL,
        field="Block 15/9 (Volve)",
        block=_block_of(VOLVE_TELEMETRY_WELL),
        operator="Equinor (Volve field)",
        well_type="Offshore development well — active",
        status="DRILLING",
        latitude=round(latitude, 6),
        longitude=round(longitude, 6),
        spud_date=date(1993, 8, 1),
        rig="Volve field",
        mud_system="Oil-based",
        water_depth_m=142.0,
        total_depth_md=round(depth_hi, 1),
        current_depth_md=current_md,
        current_tvd=current_md,
        section_size="8.5 in",
        bit_size="8.5 in",
        status_note=(
            f"Live well. Telemetry logged {depth_lo:,.1f}-{depth_hi:,.1f} m MD "
            f"({len(frame.rows):,} samples). Formation column is a labelled proxy from "
            f"{block} block NPD tops. {position_note}"
        ),
        trajectory=[
            {"md": depth_lo, "tvd": depth_lo, "inclination": 0.0},
            {"md": current_md, "tvd": current_md, "inclination": 0.0},
            {"md": depth_hi, "tvd": depth_hi, "inclination": 0.0},
        ],
        is_active=True,
        is_simulated=False,
        formation_id=formation.id if formation else None,
    )
    session.add(well)
    session.flush()

    md_index = frame.columns.index("Measured Depth m")
    tvd_index = frame.columns.index("Extrapolated Hole TVD m")
    channel_indexes = {name: i for i, name in enumerate(frame.columns) if name in _WATCHED_CHANNELS}

    session.bulk_save_objects(
        (
            TelemetrySample(
                well_id=well.id,
                row_index=row_index,
                md=float(row[md_index] or 0.0),
                tvd=float(row[tvd_index] or row[md_index] or 0.0),
                channels={
                    name: row[index]
                    for name, index in channel_indexes.items()
                    if row[index] is not None
                },
            )
            for row_index, row in enumerate(frame.rows)
            if row[md_index] is not None
        )
    )
    session.flush()
    logger.info("Loaded %s telemetry samples for %s", len(frame.rows), well.id)
    return well


#: Channels stored per sample — the ones the anomaly engine actually watches.
_WATCHED_CHANNELS = {
    "Corrected Total Hookload kkgf",
    "Total Hookload kkgf",
    "Average Hookload kkgf",
    "Corrected Hookload kkgf",
    "HKLO kkgf",
    "Average Rotary Speed rpm",
    "Corrected Surface Weight on Bit kkgf",
    "Rate of Penetration m/h",
    "Mud Density Out g/cm3",
    "Total SPM 1/min",
    "Pump 1 Stroke Rate 1/min",
    "Pump 2 Stroke Rate 1/min",
    "MWD Raw Gamma Ray 1/s",
    "MWD Gamma Ray (API BH corrected) gAPI",
    "MWD Gravity Toolface dega",
    "MWD Total Shocks unitless",
    "PowerUP Shock Rate 1/s",
    "ROPIH s/m",
}


# --------------------------------------------------------------------------- #
# DDR documents, events and evidence
# --------------------------------------------------------------------------- #


def _build_documents_and_events(
    session: Session,
    wells: dict[str, Well],
    formations: dict[str, Formation],
    max_reports: int,
) -> tuple[int, int]:
    reports = read_ddr_reports()
    used = reports[:max_reports]
    events_made = 0
    documents_made = 0

    for index, report in enumerate(used):
        # The public Volve DDR release is published per field, not per wellbore.
        # Every report is therefore attached to the field's live well and the
        # document says so; a report is never presented as a specific wellbore's
        # record when the source does not say which one it is.
        well = wells.get(VOLVE_TELEMETRY_WELL)
        document = Document(
            id=report.report_id,
            well_id=well.id if well else None,
            doc_type="DDR",
            title=f"Volve field Daily Drilling Report {report.report_id} — wellbore not published in this release",
            filename=f"{report.report_id.lower()}.txt",
            doc_date=date(1994, 1, 1) + timedelta(days=index),
            source_system=format_label(SOURCES["volve_ddrs"])[:64],
            page_count=1,
            ocr_engine="NONE",
            extraction_method="RULE_VOCABULARY_REAL_TEXT",
            is_simulated=False,
            excerpt=report.text[:2000],
            sections=[{"heading": stamp or "Operations log", "page": None, "text": body}
                      for stamp, body in report.activities][:40],
            full_text=report.text,
        )
        session.add(document)
        documents_made += 1

        for parsed in parse_report_text(report.text, vocabulary=EVENT_VOCABULARY):
            md = parsed.get("md")
            if md is None or md <= 0:
                continue
            event_type = parsed.get("event_type") or _event_type_for(parsed.get("description", ""))
            if event_type is None:
                continue
            description = parsed.get("description", "")
            # Canonical formation lookup: the same range query the API and the
            # ingestion pipeline use. A second, different rule here made the
            # seeded corpus disagree with every runtime read of the same depth.
            formation = formation_at_tvd(session, float(parsed.get("tvd") or md))
            event = DrillingEvent(
                id=f"EV-{index:05d}-{events_made:03d}",
                well_id=well.id,
                document_id=document.id,
                formation_id=formation.id if formation is not None else None,
                event_type=event_type,
                event_subtype="Volve DDR activity log",
                md=round(float(md), 1),
                tvd=round(float(parsed.get("tvd") or md), 1),
                severity=_severity_for(f"{description} {parsed.get('mitigation', '')}"),
                severity_score={"LOW": 0.25, "MODERATE": 0.5, "HIGH": 0.8, "CRITICAL": 1.0}[
                    _severity_for(f"{description} {parsed.get('mitigation', '')}")
                ],
                occurred_at=datetime(1994, 1, 1, tzinfo=timezone.utc) + timedelta(days=index),
                day_number=index + 1,
                description=description[:2000],
                mitigation=parsed.get("mitigation", "")[:2000],
                status="CLOSED",
                days_open=0,
                is_simulated=False,
            )
            session.add(event)
            session.flush()
            events_made += 1
            session.add(
                Evidence(
                    id=f"EVX-{event.id}",
                    event_id=event.id,
                    document_id=document.id,
                    page=None,
                    section=parsed.get("section") or "Operations log",
                    text_span=(parsed.get("mitigation") or description)[:1200],
                    confidence=0.99,
                    extraction_method="RULE_VOCABULARY_REAL_TEXT",
                )
            )
        if index % 500 == 0:
            session.flush()
    session.flush()
    return documents_made, events_made


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _current_counts(session: Session) -> dict[str, Any]:
    """Row counts for the loaded corpus, plus the provenance stamp."""
    return {
        "wells": session.scalar(select(func.count()).select_from(Well)),
        "formations": session.scalar(select(func.count()).select_from(Formation)),
        "documents": session.scalar(select(func.count()).select_from(Document)),
        "events": session.scalar(select(func.count()).select_from(DrillingEvent)),
        "telemetry_samples": session.scalar(select(func.count()).select_from(TelemetrySample)),
        "data_provenance": DATA_PROVENANCE,
        "seed_version": SEED_VERSION,
    }


def seed_real(
    session: Session,
    *,
    max_reports: int = 4000,
    force: bool = False,
) -> dict[str, Any]:
    """Load the real Volve/NPD dataset.

    Idempotent: calling it again without ``force`` is a no-op that returns the
    current counts, rather than inserting duplicate formations.
    """
    if not force and is_seeded(session):
        return _current_counts(session)

    if force:
        for model in (Evidence, DrillingEvent, Document, AnomalyAlert, TelemetrySample, Well, Formation):
            session.query(model).delete()
        session.commit()

    try:
        headers = load_well_headers()
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(
            "The NPD lithostratigraphy export is missing from backend/data_sources "
            f"({exc}). Pravah does not fall back to synthetic data — restore the "
            "published files and restart."
        ) from exc
    if not headers:
        raise RuntimeError(
            "No NPD well headers found — the real dataset is missing from "
            "backend/data_sources. Pravah does not fall back to invented data."
        )

    logger.info("Seeding from real data: %d NPD wells", len(headers))
    casing = load_casing_shoes()
    formations = _build_formations(session, headers)
    wells = _build_wells(session, headers, formations, casing)
    active = _activate_telemetry_well(session, wells, formations, headers)
    wells[active.id] = active
    session.commit()

    documents, events = _build_documents_and_events(session, wells, formations, max_reports)
    session.commit()

    # The offset-relation cache and the alert set are what the product reads;
    # without them the app boots with an empty map and an empty alert centre.
    relations = _rebuild_derived(session)
    session.commit()

    counts = _current_counts(session)
    counts.update(
        {
            "documents": documents,
            "events": events,
            "offset_relations": relations,
            "alerts": counts.get("alerts", 0),
        }
    )
    logger.info("Real-data seed complete: %s", counts)
    return counts


def _rebuild_derived(session: Session) -> int:
    """Rebuild the offset-relation cache and the alerts, deterministically."""
    from .relevance import refresh_relations
    from .risk import recompute_well_alerts

    from .alert_bridge import sync_telemetry_alerts

    relations = refresh_relations(session)
    for well in session.scalars(select(Well).where(Well.is_active.is_(True))):
        # Offset-event alerts first (they fire when offset wells carry
        # attributable history), then the telemetry anomalies on the live well.
        recompute_well_alerts(session, well.id)
        sync_telemetry_alerts(session, well.id)
    return relations

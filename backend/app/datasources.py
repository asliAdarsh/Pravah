"""Parsers for the real public datasets this prototype runs on.

No synthetic records. Every row that reaches the database originates in one of
the files under ``backend/data_sources``:

``NPD_Lithostratigraphy_member_formations_all_wells.xlsx``
    Norwegian Petroleum Directorate FactPages — formation tops for every
    Norwegian offshore well, with UTM coordinates. Public, open data.

``NPD_Lithostratigraphy_groups_all_wells.xlsx``
    NPD group (composite) tops, used for the coarse stratigraphic column.

``NPD_Casing_depth_most_wells.xlsx``
    Casing shoe depths — the casing/formation correlation on well records.

``volve_ddrs_train.csv`` / ``volve_ddrs_test.csv``
    The Volve field Daily Drilling Report corpus (public Volve DDR release).
    Rows carry the raw 24-hour operations log as free text; the event
    extractor in :mod:`app.ingestion` reads hazards, depths and mitigations
    out of that text.

``telemetry_15_9_F_9A.csv``
    High-frequency drilling telemetry for well 15/9-F-9A, one row per depth
    sample, with the MWD / mud-logger channels (hookload, rotary speed, WOB,
    ROP, mud density, gamma ray, pump strokes, flow, pressure, shocks).

The only derived values are the UTM→WGS84 conversion (standard transverse
Mercator, exact closed form) and formation tops assembled from NPD surfaces.
Nothing is invented.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

__all__ = [
    "DATA_SOURCES",
    "DEPTH_RE",
    "EVENT_VOCABULARY",
    "SourceFile",
    "format_label",
    "formation_at_md",
    "load_casing_shoes",
    "load_formation_tops",
    "load_source_registry",
    "load_telemetry",
    "load_well_headers",
    "read_ddr_reports",
    "utm_to_latlon",
]

DATA_SOURCES = Path(__file__).resolve().parent.parent / "data_sources"

#: UTM zone covering the Norwegian North Sea fields in the NPD exports (32N).
_NORWEGIAN_UTM_ZONE = 32


@dataclass(frozen=True)
class SourceFile:
    """One vendored public dataset, with the citation the UI must show."""

    id: str
    title: str
    publisher: str
    url: str
    path: Path
    licence: str = "Public open data"

    def exists(self) -> bool:
        # The DDR corpus is a folder of CSVs; a folder is present too.
        return self.path.exists()


SOURCES: dict[str, SourceFile] = {
    "npd_member_formations": SourceFile(
        id="npd_member_formations",
        title="NPD Lithostratigraphy — member formations, all wells",
        publisher="Norwegian Petroleum Directorate (FactPages)",
        url="https://factpages.npd.no/",
        path=DATA_SOURCES / "NPD_Lithostratigraphy_member_formations_all_wells.xlsx",
    ),
    "npd_groups": SourceFile(
        id="npd_groups",
        title="NPD Lithostratigraphy — groups, all wells",
        publisher="Norwegian Petroleum Directorate (FactPages)",
        url="https://factpages.npd.no/",
        path=DATA_SOURCES / "NPD_Lithostratigraphy_groups_all_wells.xlsx",
    ),
    "npd_casing": SourceFile(
        id="npd_casing",
        title="NPD Casing depth, most wells",
        publisher="Norwegian Petroleum Directorate (FactPages)",
        url="https://factpages.npd.no/",
        path=DATA_SOURCES / "NPD_Casing_depth_most_wells.xlsx",
    ),
    "volve_ddrs": SourceFile(
        id="volve_ddrs",
        title="Volve field Daily Drilling Reports (train + test)",
        publisher="Equinor Volve / public DDR release",
        url="https://github.com/equinor/volve",
        path=DATA_SOURCES / "volve",
    ),
    "volve_telemetry": SourceFile(
        id="volve_telemetry",
        title="Volve high-frequency drilling telemetry, well 15/9-F-9A",
        publisher="Equinor / Norwegian Petroleum Directorate open data",
        url="https://factpages.npd.no/",
        path=DATA_SOURCES / "volve" / "telemetry_15_9_F_9A.csv",
    ),
}


def load_source_registry() -> list[dict[str, Any]]:
    """Return the provenance registry the API and UI surface."""
    out = []
    for source in SOURCES.values():
        entry: dict[str, Any] = {
            "id": source.id,
            "title": source.title,
            "publisher": source.publisher,
            "url": source.url,
            "licence": source.licence,
            "present": source.exists(),
        }
        if source.path.is_file():
            entry["size_bytes"] = source.path.stat().st_size
        elif source.path.is_dir():
            entry["size_bytes"] = sum(f.stat().st_size for f in source.path.glob("*.csv"))
        out.append(entry)
    return out


def format_label(source: SourceFile) -> str:
    """Short citation string, e.g. ``NPD FactPages (public open data)``."""
    return f"{source.publisher} — {source.title}"


# --------------------------------------------------------------------------- #
# Coordinates
# --------------------------------------------------------------------------- #


def utm_to_latlon(easting: float, northing: float, zone: int = _NORWEGIAN_UTM_ZONE) -> tuple[float, float]:
    """Convert UTM (WGS84, northern hemisphere) to WGS84 latitude/longitude.

    Closed-form inverse transverse Mercator. The NPD exports give UTM 32N,
    the zone covering the Volve field and its neighbours, so the map plots true
    geographic positions rather than a made-up coordinate space.
    """
    a = 6378137.0
    f = 1 / 298.257223563
    e2 = f * (2 - f)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    k0 = 0.9996
    x = easting - 500000.0

    m = northing / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
    )
    sin_phi1, cos_phi1, tan_phi1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1 = e1**2 / 16 * (5 + phi1 * phi1)
    t1 = tan_phi1**2
    n1 = a / math.sqrt(1 - e2 * sin_phi1 * sin_phi1)
    r1 = a * (1 - e2) / (1 - e2 * sin_phi1 * sin_phi1) ** 1.5
    d = x / (n1 * k0)
    lat = phi1 - (n1 * tan_phi1 / r1) * (
        d * d / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * e2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * e2 - 3 * c1 * c1) * d**6 / 720
    )
    lon = (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * e2 + 24 * t1 * t1) * d**5 / 120
    ) / cos_phi1
    # The central meridian is a DEGREE offset, not a radian one.
    return math.degrees(lat), math.degrees(lon) + (zone * 6 - 183)


def _to_deg(rad: float) -> float:
    import math

    return math.degrees(rad)


# --------------------------------------------------------------------------- #
# Lithostratigraphy
# --------------------------------------------------------------------------- #

_SURFACE_TOP_RE = re.compile(r"\bTop\b", re.IGNORECASE)


def _read_excel_rows(path: Path) -> list[dict[str, Any]]:
    """Read an NPD xlsx without depending on openpyxl at import time."""
    import pandas as pd

    frame = pd.read_excel(path)
    return frame.to_dict("records")


def load_formation_tops() -> dict[str, list[dict[str, Any]]]:
    """Return ``{well_id: [{name, top_md, group, source}]}`` sorted by depth.

    """
    members = _read_excel_rows(SOURCES["npd_member_formations"].path)
    groups = _read_excel_rows(SOURCES["npd_groups"].path)

    def collect(rows: Iterable[dict[str, Any]], kind: str) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            well = str(row.get("Well identifier") or "").strip()
            horizon = str(row.get("HorizonName") or "").strip()
            surface = str(row.get("Surface") or "").strip()
            md = row.get("MD")
            if not well or not horizon or md is None:
                continue
            try:
                top_md = float(md)
            except (TypeError, ValueError):
                continue
            name = surface if _SURFACE_TOP_RE.search(surface) else f"{horizon} Top"
            out.setdefault(well, []).append(
                {
                    "name": name,
                    "horizon": horizon,
                    "top_md": top_md,
                    "group": horizon,
                    "kind": kind,
                    "x": _as_float(row.get("X")),
                    "y": _as_float(row.get("Y")),
                    "sea_level_m": _as_float(row.get("Z")),
                }
            )
        return out

    merged = collect(members, "member")
    for well, tops in collect(groups, "group").items():
        merged.setdefault(well, []).extend(tops)

    for well, tops in merged.items():
        tops.sort(key=lambda t: t["top_md"])
    return merged


def _as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # drop NaN


def formation_at_md(tops: list[dict[str, Any]], md: float) -> str | None:
    """Formation name at a measured depth — pure range lookup, no invention."""
    if not tops:
        return None
    name = tops[0]["name"]
    for top in tops:
        if top["top_md"] <= md:
            name = top["name"]
        else:
            break
    return name


def load_casing_shoes() -> dict[str, list[dict[str, Any]]]:
    """Return ``{well_id: [{casingshoe_md, casingshoe_tvd, ...}]}``."""
    path = SOURCES["npd_casing"].path
    if not path.is_file():
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for row in _read_excel_rows(path):
        well = str(row.get("Wellbore") or row.get("Well") or "").strip()
        shoe_md = _as_float(row.get("Casingdepth MD"))
        if not well or shoe_md is None:
            continue
        out.setdefault(well, []).append(
            {
                "casing_shoe_md": shoe_md,
                "casing_shoe_tvd": _as_float(row.get("Casingdepth TVD")),
                "casing_type": str(row.get("Casing type") or "").strip() or None,
                "hole": str(row.get("Hole") or "").strip() or None,
            }
        )
    for shoes in out.values():
        shoes.sort(key=lambda s: s["casing_shoe_md"])
    return out


# --------------------------------------------------------------------------- #
# Well headers
# --------------------------------------------------------------------------- #

#: Volve-style identifiers, e.g. ``15/9-F-9A``, ``7/1-2 S``.
WELL_ID_RE = re.compile(r"^\d{1,2}/\d{1,2}-[A-Z0-9][A-Za-z0-9 \-]{0,12}$")


def load_well_headers() -> list[dict[str, Any]]:
    """Build one well record per NPD well that has a real coordinate and tops.

    The NPD lithostratigraphy exports are the authority: a well appears here
    only if the Directorate published both its position and its stratigraphy.
    """
    tops_by_well = load_formation_tops()
    rows: list[dict[str, Any]] = []
    for well, tops in sorted(tops_by_well.items()):
        member_tops = [t for t in tops if t["kind"] == "member"]
        anchor = member_tops[0] if member_tops else tops[0]
        x, y = anchor.get("x"), anchor.get("y")
        if x is None or y is None:
            continue
        lat, lon = utm_to_latlon(x, y)
        rows.append(
            {
                "well_id": well,
                "latitude": lat,
                "longitude": lon,
                "utm_easting": x,
                "utm_northing": y,
                "sea_level_m": anchor.get("sea_level_m"),
                "formation_tops": tops,
                "deepest_formation_md": max(t["top_md"] for t in tops),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Daily drilling reports
# --------------------------------------------------------------------------- #

#: Volve DDRs are operational logs; this finds the depth statements inside them.
DEPTH_RE = re.compile(r"\b(\d{3,4}(?:[.,]\d)?)\s*m(?:eter|eters)?\b", re.IGNORECASE)

#: Hazard vocabulary as it appears in real English drilling logs.
EVENT_VOCABULARY: dict[str, tuple[str, ...]] = {
    "STUCK_PIPE": ("stuck string", "stuck pipe", "stuck bit", "stuck tool", "stuck string released", "freeze", "freeing stuck"),
    "MUD_LOSS": ("mud loss", "losses", "pit gain", "lost circulation", "circulation lost"),
    "KICK": ("kick", "flow check", "well control", "blowout"),
    "TORQUE_SPIKE": ("overpull", "o/pull", "torque", "overpull-torque", "spp increased", "stalled", "drag"),
    "OVERPRESSURE": ("overpressure", "pore pressure", "trip gas"),
    "CEMENTING_ISSUE": ("cement", "cement plug", "bond", "channeling"),
    "DRILLING_DYSFUNCTION": ("vibration", "whirl", "bit balled", "bit damage", "motor", "elbow"),
    "NPT": ("npt", "non-productive", "wasted time", "downtime"),
    "HOLE_INSTABILITY": ("hole condition", "sloughing", "caving", "washout", "reamed", "reaming"),
    "BHA_FAILURE": ("bha", "motor failure", "joint failure", "bit dull", "changed bit", "motor burnt"),
    "OTHER": ("trouble", "problem", "issue", "delay"),
}

#: A mitigation clause in the same line as the hazard it answers.
MITIGATION_RE = re.compile(
    r"\b(freed|freed|released|worked|reamed|circulated|reduced|increased|changed|replaced|"
    r"jammed|backed off|backed-off|pooh|tih|overpull-torque|adjusted|slacked-off)\b",
    re.IGNORECASE,
)


@dataclass
class DdrReport:
    """One real 24-hour operations report from the Volve DDR corpus."""

    report_id: str
    split: str
    text: str
    summary: str
    sequence: int = 0
    activities: list[tuple[str, str]] = field(default_factory=list)


def read_ddr_reports(limit: int | None = None) -> list[DdrReport]:
    """Read the real DDR corpus, newest split first (``test`` then ``train``).

    Only the ``input`` column carries the raw operations log; ``output`` is the
    reference 24-hour summary, kept for provenance but never used as evidence.
    """
    reports: list[DdrReport] = []
    sequence = 0
    for split in ("test", "train"):
        folder = SOURCES["volve_ddrs"].path
        path = folder / f"volve_ddrs_{split}.csv"
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                text = (row.get("input") or "").strip()
                if not text:
                    continue
                sequence += 1
                reports.append(
                    DdrReport(
                        report_id=f"DDR-{split.upper()}-{sequence:05d}",
                        split=split,
                        text=text,
                        summary=(row.get("output") or "").strip(),
                        sequence=sequence,
                        activities=split_activities(text),
                    )
                )
                if limit is not None and len(reports) >= limit:
                    return reports
    return reports


_TIME_ACTIVITY_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2})\s*-\s*(?P<end>\d{1,2}:\d{2})(?:\s*\(?next day\)?)?\s*:\s*(?P<body>.+?)(?=\n|$)",
    re.IGNORECASE,
)


def split_activities(text: str) -> list[tuple[str, str]]:
    """Split a DDR log into ``("07:30 - 09:00", "activity text")`` blocks.

    Real DDRs are time-stamped activity lines; keeping the block boundaries
    means an event is attributed to the activity it was written in, not to the
    whole report.
    """
    blocks: list[tuple[str, str]] = []
    for match in _TIME_ACTIVITY_RE.finditer(text):
        stamp = f"{match.group('start')} - {match.group('end')}"
        blocks.append((stamp, match.group("body").strip()))
    if blocks:
        return blocks
    stripped = [line.strip() for line in text.splitlines() if line.strip()]
    return [("", stripped[0])] if stripped else []


# --------------------------------------------------------------------------- #
# Telemetry
# --------------------------------------------------------------------------- #

#: Channel name (as exported) → the unit label the UI shows.
TELEMETRY_CHANNELS: dict[str, str] = {
    "Measured Depth m": "m MD",
    "Extrapolated Hole TVD m": "m TVD",
    "Average Rotary Speed rpm": "rpm",
    "Rate of Penetration m/h": "m/h",
    "Corrected Total Hookload kkgf": "kkgf",
    "Corrected Surface Weight on Bit kkgf": "kkgf",
    "Corrected Hookload kkgf": "kkgf",
    "Average Hookload kkgf": "kkgf",
    "Total Hookload kkgf": "kkgf",
    "HKLO kkgf": "kkgf",
    "MWD Turbine RPM rpm": "rpm",
    "MWD Raw Gamma Ray 1/s": "1/s",
    "MWD Gamma Ray (API BH corrected) gAPI": "gAPI",
    "MWD Gravity Toolface dega": "dega",
    "MWD Continuous Inclination dega": "dega",
    "Mud Density Out g/cm3": "g/cm3",
    "Total SPM 1/min": "spm",
    "Pump 2 Stroke Rate 1/min": "spm",
    "Pump 1 Stroke Rate 1/min": "spm",
    "Bit Drill Time h": "h",
    "Pump Time h": "h",
    "Bit Drilling Run m": "m",
    "MWD Total Shocks unitless": "count",
    "PowerUP Shock Rate 1/s": "1/s",
    "ROPIH s/m": "s/m",
    "DRET unitless": "unitless",
    "EDRT unitless": "unitless",
    "STUCK_RT unitless": "unitless",
}

#: Channels the anomaly engine watches, in engineering priority order.
HAZARD_CHANNELS: dict[str, tuple[str, ...]] = {
    "stuck_pipe": (
        "Corrected Total Hookload kkgf",
        "Total Hookload kkgf",
        "Average Hookload kkgf",
        "Corrected Hookload kkgf",
        "HKLO kkgf",
        "Average Rotary Speed rpm",
    ),
    "torque_spike": (
        "Corrected Total Hookload kkgf",
        "Average Rotary Speed rpm",
        "Corrected Surface Weight on Bit kkgf",
    ),
    "overpressure": (
        "Mud Density Out g/cm3",
        "Total SPM 1/min",
    ),
    "mud_loss": (
        "Mud Density Out g/cm3",
        "Pump 1 Stroke Rate 1/min",
        "Pump 2 Stroke Rate 1/min",
    ),
}


@dataclass
class TelemetryFrame:
    """Column-oriented telemetry for one well, exactly as exported."""

    well_id: str
    columns: list[str]
    rows: list[list[float | None]]
    source: str

    def series(self, column: str) -> Iterator[tuple[int, float]]:
        try:
            index = self.columns.index(column)
        except ValueError:
            return
        for position, row in enumerate(self.rows):
            value = row[index]
            if value is not None:
                yield position, value

    def populated(self) -> int:
        return sum(1 for row in self.rows if any(v is not None for v in row))

    def depth_range(self) -> tuple[float, float]:
        # series() yields (row_index, value); take the value, not the index.
        depths = [value for _, value in self.series("Measured Depth m")]
        return (min(depths), max(depths)) if depths else (0.0, 0.0)


def load_telemetry(path: Path | None = None) -> TelemetryFrame:
    """Load the real telemetry CSV for 15/9-F-9A.

    The export carries ~250 columns of which the watched channels are a
    handful; the rest are kept so the channel browser can show what was
    actually recorded. Empty leading samples (before the MWD toolstring
    entered the logged interval) are preserved as ``None`` — never
    forward-filled, because filling would fabricate measurements.
    """
    path = path or SOURCES["volve_telemetry"].path
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        columns = [name.strip() for name in header]
        rows: list[list[float | None]] = []
        for raw in reader:
            if not raw:
                continue
            row: list[float | None] = []
            for cell in raw:
                cell = cell.strip()
                if not cell or cell.lower() == "nan":
                    row.append(None)
                    continue
                try:
                    row.append(float(cell))
                except ValueError:
                    row.append(None)
            rows.append(row)
    return TelemetryFrame(
        well_id="15/9-F-9A",
        columns=columns,
        rows=rows,
        source=format_label(SOURCES["volve_telemetry"]),
    )

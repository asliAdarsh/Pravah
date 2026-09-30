"""Read-side services over the real telemetry corpus.

Three views of one well's 16,670-sample MWD / mud-logger record:

:func:`channel_catalogue`
    Which channels exist, in what unit, how many real measurements each one
    carries and over what depth range.

:func:`stream`
    A depth-windowed series for charting, downsampled with a **min/max
    envelope**. Naive striding (``rows[::n]``) silently throws away the very
    spikes an anomaly engine exists to find — a one-sample excursion between
    two strided rows vanishes. The envelope keeps the extremes of every bucket,
    so a spike survives downsampling.

:func:`stream`
    The current operating state: the latest real value of every watched
    channel plus the most recent anomaly on it.

Honesty rules
-------------
* Channels absent from a depth are reported as absent. The service never
  interpolates, forward-fills or substitutes a placeholder — a chart gap means
  "no measurement here", and saying so is the whole point of the panel.
* ``to_channel`` and the catalogue report the *observed* populated count, which
  is far below the row count: 11,067 of 16,670 rows carry no hookload at all.
* Nothing here is modelled. Every value is a value the toolstring measured.
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .datasources import HAZARD_CHANNELS, TELEMETRY_CHANNELS
from .models import AnomalyAlert, TelemetrySample, Well

__all__ = [
    "DEFAULT_MAX_POINTS",
    "channel_catalogue",
    "snapshot",
    "stream",
    "well_depth_bounds",
]

#: Cap applied when the caller does not ask for a specific density.
DEFAULT_MAX_POINTS = 600

#: Rows examined per database round trip. Purely a batching detail.
_FETCH_BATCH = 2000


def well_depth_bounds(session: Session, well_id: str) -> tuple[int, int, float, float]:
    """Return ``(min_row, max_row, min_md, max_md)`` for a well's telemetry.

    Returns ``(0, -1, 0.0, 0.0)`` when the well has no telemetry at all, which
    lets callers report emptiness instead of raising on a missing range.
    """
    rows = session.execute(
        select(
            TelemetrySample.row_index,
            TelemetrySample.md,
        )
        .where(TelemetrySample.well_id == well_id)
        .order_by(TelemetrySample.row_index)
    )
    min_row: int | None = None
    max_row: int | None = None
    min_md = math.inf
    max_md = -math.inf
    for row_index, md in rows:
        if min_row is None:
            min_row = row_index
        max_row = row_index
        min_md = min(min_md, md)
        max_md = max(max_md, md)
    if min_row is None or max_row is None:
        return 0, -1, 0.0, 0.0
    return min_row, max_row, min_md, max_md


def _require_well(session: Session, well_id: str) -> Well:
    """Return the well or raise ``LookupError`` for the router to map to 404."""
    well = session.get(Well, well_id)
    if well is None:
        raise LookupError(well_id)
    return well


def channel_catalogue(session: Session, well_id: str) -> dict[str, Any]:
    """Describe every channel recorded for a well.

    Returns the exported unit label, the number of real measurements, and the
    depth range over which the channel actually carries data — which for most
    channels is far narrower than the well's full logged interval.
    """
    _require_well(session, well_id)

    depth: dict[int, float] = {}
    counts: dict[str, int] = {}
    first_row: dict[str, int] = {}
    last_row: dict[str, int] = {}
    min_md: dict[str, float] = {}
    max_md: dict[str, float] = {}

    statement = (
        select(TelemetrySample.row_index, TelemetrySample.md, TelemetrySample.channels)
        .where(TelemetrySample.well_id == well_id)
        .order_by(TelemetrySample.row_index)
    )
    for row_index, md, channels in session.execute(statement):
        depth[row_index] = md
        for name, value in (channels or {}).items():
            if value is None:
                continue
            counts[name] = counts.get(name, 0) + 1
            first_row.setdefault(name, row_index)
            last_row[name] = row_index
            if name not in min_md or md < min_md[name]:
                min_md[name] = md
            if name not in max_md or md > max_md[name]:
                max_md[name] = md

    watched = {channel for channels in HAZARD_CHANNELS.values() for channel in channels}
    entries = [
        {
            "channel": name,
            "unit": TELEMETRY_CHANNELS.get(name, ""),
            "samples": counts[name],
            "first_row_index": first_row[name],
            "last_row_index": last_row[name],
            "min_md": min_md[name],
            "max_md": max_md[name],
            "watched_for_hazards": sorted(
                hazard for hazard, names in HAZARD_CHANNELS.items() if name in names
            ),
        }
        for name in sorted(counts)
    ]
    populated_rows = len(depth)
    total_samples = sum(counts.values())

    return {
        "well_id": well_id,
        "rows": populated_rows,
        "channel_count": len(entries),
        "total_channel_samples": total_samples,
        "depth_range_md": [min(depth.values()), max(depth.values())] if depth else [0.0, 0.0],
        "channels": entries,
        "unwatched_channel_count": sum(
            1 for e in entries if e["channel"] not in watched
        ),
        "note": (
            "Populated counts are observed measurements. Rows without a reading "
            "for a channel are absent data and are never interpolated."
        ),
    }


def _load_window(
    session: Session, well_id: str, from_row: int, to_row: int
) -> list[tuple[int, float, float, dict[str, Any]]]:
    """Return the samples in ``[from_row, to_row]`` in export order."""
    statement = (
        select(
            TelemetrySample.row_index,
            TelemetrySample.md,
            TelemetrySample.tvd,
            TelemetrySample.channels,
        )
        .where(
            TelemetrySample.well_id == well_id,
            TelemetrySample.row_index >= from_row,
            TelemetrySample.row_index <= to_row,
        )
        .order_by(TelemetrySample.row_index)
    )
    return [
        (row_index, md, tvd, channels or {})
        for row_index, md, tvd, channels in session.execute(statement)
    ]


def stream(
    session: Session,
    well_id: str,
    channels: list[str] | None = None,
    from_row: int | None = None,
    to_row: int | None = None,
    max_points: int = DEFAULT_MAX_POINTS,
) -> dict[str, Any]:
    """Return a depth-windowed, envelope-downsampled series for charting.

    Downsampling is a min/max envelope: the window is split into buckets and the
    minimum and maximum of each requested channel inside every bucket are both
    retained. A single-sample excursion therefore always survives, which naive
    fixed-interval striding would discard.

    Args:
        session: Open database session.
        well_id: Well to read.
        channels: Channels to return. Defaults to every channel with data.
        from_row: First ``row_index`` inclusive. Defaults to the well's first.
        to_row: Last ``row_index`` inclusive. Defaults to the well's last.
        max_points: Approximate upper bound on returned points per channel.

    Returns:
        ``points`` (each ``{row_index, md, tvd, channels}``) plus the window,
        the resolved channel list and an honest note about what was dropped.
    """
    _require_well(session, well_id)
    min_row, max_row, min_md, max_md = well_depth_bounds(session, well_id)
    if max_row < min_row:
        return {
            "well_id": well_id,
            "points": [],
            "channels": [],
            "from_row": from_row,
            "to_row": to_row,
            "max_points": max_points,
            "rows_in_window": 0,
            "method": "min_max_envelope",
            "note": "This well has no telemetry samples.",
        }

    start = min_row if from_row is None else max(from_row, min_row)
    end = max_row if to_row is None else min(to_row, max_row)
    if end < start:
        start, end = min_row, max_row

    window = _load_window(session, well_id, start, end)
    if not window:
        return {
            "well_id": well_id,
            "points": [],
            "channels": [],
            "from_row": start,
            "to_row": end,
            "max_points": max_points,
            "rows_in_window": 0,
            "method": "min_max_envelope",
            "note": "No samples in the requested row range.",
        }

    available: set[str] = set()
    for _, _, _, sample_channels in window:
        available.update(sample_channels)

    requested = [c for c in (channels or sorted(available)) if c in available]
    dropped_unknown = [c for c in (channels or []) if c not in available]
    if not requested:
        requested = sorted(available)

    # Bucket count chosen so the union of per-channel extremes stays near the
    # budget. Each channel contributes at most two points per bucket.
    bucket_count = max(1, max_points // max(2 * len(requested), 1))
    bucket_count = min(bucket_count, len(window))
    edges = [round(i * len(window) / bucket_count) for i in range(bucket_count + 1)]

    keep: set[int] = set()
    for index in range(bucket_count):
        low, high = edges[index], edges[index + 1]
        if low >= high:
            continue
        segment = window[low:high]
        for name in requested:
            lo_row: int | None = None
            hi_row: int | None = None
            lo_val = math.inf
            hi_val = -math.inf
            for row_index, _, _, sample_channels in segment:
                value = sample_channels.get(name)
                if value is None:
                    continue
                if value < lo_val:
                    lo_val, lo_row = value, row_index
                if value > hi_val:
                    hi_val, hi_row = value, row_index
            if lo_row is not None:
                keep.add(lo_row)
            if hi_row is not None:
                keep.add(hi_row)

    # Always retain the window endpoints so the chart's depth axis is anchored.
    keep.add(window[0][0])
    keep.add(window[-1][0])

    points = [
        {
            "row_index": row_index,
            "md": md,
            "tvd": tvd,
            "channels": {name: sample_channels[name] for name in requested if name in sample_channels},
        }
        for row_index, md, tvd, sample_channels in window
        if row_index in keep
    ]

    #: Per channel, the depths at which the instrument actually reported, and
    #: the row range it covers. A value missing from a returned point is
    #: ambiguous on its own — it can mean "this bucket's extreme was not this
    #: row" rather than "nothing was measured" — so the client is told where the
    #: real measurements are instead of being left to infer a gap.
    coverage: dict[str, dict[str, Any]] = {}
    for name in requested:
        measured = [md for _, md, _, sample_channels in window if name in sample_channels]
        coverage[name] = {
            "samples_in_window": len(measured),
            "first_md": round(min(measured), 1) if measured else None,
            "last_md": round(max(measured), 1) if measured else None,
            "measured": bool(measured),
        }

    return {
        "well_id": well_id,
        "points": points,
        "channels": requested,
        "from_row": start,
        "to_row": end,
        "max_points": max_points,
        "rows_in_window": len(window),
        "points_returned": len(points),
        "method": "min_max_envelope",
        "buckets": bucket_count,
        "depth_range_md": [min_md, max_md],
        "dropped_channels": dropped_unknown,
        "channel_coverage": coverage,
        "note": (
            "Min/max envelope downsampling: the extremes of every bucket are kept, "
            "so a single-sample spike is never strided away. A channel missing from "
            "a returned point is NOT necessarily an unmeasured interval — under "
            "envelope downsampling it usually means this row was not that channel's "
            "bucket extreme. Use channel_coverage[].first_md/last_md for where the "
            "channel was actually reported. Nothing is zero-filled or interpolated."
        ),
    }


def snapshot(session: Session, well_id: str, at_row: int | None = None) -> dict[str, Any]:
    """Latest real value of every watched channel, with its latest anomaly.

    Args:
        session: Open database session.
        well_id: Well to summarise.
        at_row: Evaluate the state as of this ``row_index``. ``None`` means the
            most recent sample available.

    Returns:
        Per-channel latest value, depth, and most recent alert where one exists.
    """
    well = _require_well(session, well_id)
    min_row, max_row, _, _ = well_depth_bounds(session, well_id)
    if max_row < min_row:
        return {
            "well_id": well_id,
            "at_row": at_row,
            "channels": [],
            "note": "This well has no telemetry samples.",
        }

    row = max_row if at_row is None else min(at_row, max_row)

    # Walk backwards: the first sample at or before `row` carrying a channel is
    # that channel's latest real measurement. No value is invented for a
    # channel that was never measured at or before this depth.
    statement = (
        select(TelemetrySample.row_index, TelemetrySample.md, TelemetrySample.channels)
        .where(
            TelemetrySample.well_id == well_id,
            TelemetrySample.row_index <= row,
        )
        .order_by(TelemetrySample.row_index.desc())
        .limit(2000)
    )
    latest: dict[str, dict[str, Any]] = {}
    anchor_md: float | None = None
    anchor_row: int | None = None
    for row_index, md, sample_channels in session.execute(statement):
        if anchor_row is None:
            anchor_row, anchor_md = row_index, md
        for name, value in (sample_channels or {}).items():
            if name in latest or value is None:
                continue
            latest[name] = {"value": value, "md": md, "row_index": row_index}

    alerts = session.scalars(
        select(AnomalyAlert)
        .where(
            AnomalyAlert.well_id == well_id,
            AnomalyAlert.row_index <= row,
        )
        .order_by(AnomalyAlert.row_index.desc(), AnomalyAlert.channel)
    )
    newest_alert: dict[str, AnomalyAlert] = {}
    for alert in alerts:
        newest_alert.setdefault(alert.channel, alert)

    channels = []
    for name in sorted(latest):
        measurement = latest[name]
        alert = newest_alert.get(name)
        channels.append(
            {
                "channel": name,
                "unit": TELEMETRY_CHANNELS.get(name, ""),
                "value": measurement["value"],
                "md": measurement["md"],
                "row_index": measurement["row_index"],
                "stale": measurement["row_index"] != anchor_row,
                "hazards": sorted(
                    hazard for hazard, names in HAZARD_CHANNELS.items() if name in names
                ),
                "last_alert": (
                    {
                        "id": alert.id,
                        "row_index": alert.row_index,
                        "md": alert.md,
                        "detector": alert.detector,
                        "severity": alert.severity,
                        "z_score": alert.z_score,
                        "message": alert.message,
                    }
                    if alert is not None
                    else None
                ),
            }
        )

    missing = sorted(
        {
            name
            for names in HAZARD_CHANNELS.values()
            for name in names
            if name not in latest
        }
    )

    return {
        "well_id": well_id,
        "well_name": well.name,
        "current_depth_md": well.current_depth_md,
        "at_row": row,
        "anchor_row_index": anchor_row,
        "anchor_md": anchor_md,
        "channels": channels,
        "channels_without_data": missing,
        "note": (
            "Latest real measurement at or before the requested row. "
            "'stale' marks a channel whose newest reading is not from the "
            "anchor sample; channels with no reading at all are listed in "
            "channels_without_data rather than shown as zero."
        ),
    }

"""Causal physical anomaly detection over the real MWD / mud-logger channels.

The two detectors implemented here are the classical control-chart pair applied
to drilling telemetry:

``z_score``
    A rolling-window z-score. The baseline is the mean and standard deviation of
    the ``window`` samples that *precede* the current one — never including it,
    so a genuine step change is not diluted by its own contribution.

``cusum``
    A two-sided tabular (Shewhart-CUSUM) chart. ``k`` is the slack in process
    units and ``h`` the decision interval. The reference ``mu``/``sigma`` are
    taken from the same trailing window, and the accumulated sums ``S+``/``S-``
    are recursive state carried from sample to sample.

Causality contract
------------------
Every number in an alert is a function of samples at or before that alert's
``row_index``. There is no back-fill, no resampling and no cross-channel
look-ahead anywhere in this module. :mod:`app.backtest` proves it empirically
(truncation equivalence) and ``tests/test_telemetry_backtest.py`` proves it
unit-by-unit.

Gap handling
------------
11,067 of the 16,670 real samples carry no hookload reading at all. Those rows
are **skipped**, and a skipped row is a genuine break in the record: the
accumulators (``S+``/``S-``) are *reset* at a gap rather than carried across it.
Carrying a stale sum across a gap would silently assert that the process
behaved continuously through an interval for which no measurement exists, which
is exactly the kind of fabrication this project refuses. The rolling window
likewise advances only over samples that actually carry a value.

Honesty rules that follow from the data
----------------------------------------
* ``HAZARD_CHANNELS`` lists six channels for ``stuck_pipe``; only five of them
  carry enough real samples to support a rolling baseline. The service reports
  which channels were evaluated and which were skipped, and why — it never
  silently narrows the watch list.
* Every number returned is a measured value or an arithmetic function of
  measured values. Nothing is modelled, smoothed or filled in.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .datasources import HAZARD_CHANNELS
from .models import TelemetrySample

__all__ = [
    "CUSUM_H_FACTOR",
    "CUSUM_K_FACTOR",
    "DEFAULT_WINDOW",
    "SUSTAINED_RUN",
    "Z_THRESHOLD",
    "ChannelEvaluation",
    "cusum_severity",
    "cusum_step",
    "detect_channel",
    "detect_well",
    "severity_for",
    "sustained_runs",
    "watched_channels",
]

#: Number of preceding samples that form the rolling baseline.
DEFAULT_WINDOW = 50

#: A sample is anomalous when its z-score against the trailing window reaches
#: this magnitude. Three sigma is the conventional control-chart limit.
Z_THRESHOLD = 3.0

#: CUSUM allowance ``k = CUSUM_K_FACTOR * sigma``. Half a sigma is the standard
#: choice: it makes the chart sensitive to shifts of roughly one sigma while
#: ignoring the ordinary drift of a noisy channel.
CUSUM_K_FACTOR = 0.5

#: CUSUM decision interval ``h = CUSUM_H_FACTOR * sigma``.
CUSUM_H_FACTOR = 5.0

#: Severity ladder for a z-score magnitude. The ORM stores a 16-character
#: severity string, so the bands are deliberately short.
SEVERITY_BANDS: tuple[tuple[float, str], ...] = (
    (10.0, "CRITICAL"),
    (6.0, "HIGH"),
    (Z_THRESHOLD, "MODERATE"),
)
def cusum_severity(excursion_ratio: float) -> str:
    """Map a CUSUM excursion ``max(S+, S-)/h`` onto the severity ladder.

    ``excursion_ratio`` is 1.0 at the instant the decision interval is
    crossed. A ratio of 1 is a marginal signal; the further past ``h`` the sum
    ran before the alarm, the larger and more persistent the shift.
    """
    if excursion_ratio >= 5.0:
        return "CRITICAL"
    if excursion_ratio >= 2.0:
        return "HIGH"
    return "MODERATE"



#: A run of this many consecutive CRITICAL alerts counts as *sustained*.
SUSTAINED_RUN = 5

#: Relative tolerance below which a rolling standard deviation is treated as
#: zero. A channel parked at a constant value (e.g. ``HKLO kkgf``, which holds
#: 24.9476 for all 210 of its real samples) yields an undefined z-score; we
#: report nothing rather than dividing by a vanishing denominator.
_STD_EPSILON = 1e-9


@dataclass(frozen=True)
class ChannelEvaluation:
    """What happened when one channel was run through the detectors.

    Attributes:
        channel: Exported channel name.
        samples: Real measurements found for the channel.
        alerts: Alerts raised (empty when the channel has too little data).
        first_row: First ``row_index`` carrying a value, or ``None``.
        last_row: Last ``row_index`` carrying a value, or ``None``.
        reason: Why the channel produced nothing, when it produced nothing.
    """

    channel: str
    samples: int
    alerts: int
    first_row: int | None
    last_row: int | None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON shape the API and the backtest audit consume."""
        return {
            "channel": self.channel,
            "samples": self.samples,
            "alerts": self.alerts,
            "first_row_index": self.first_row,
            "last_row_index": self.last_row,
            "skipped_reason": self.reason,
        }


def severity_for(z_score: float) -> str:
    """Map a z-score magnitude onto the severity ladder."""
    magnitude = abs(z_score)
    for floor, label in SEVERITY_BANDS:
        if magnitude >= floor:
            return label
    return "INFO"


def cusum_step(
    value: float,
    s_plus: float,
    s_minus: float,
    mu: float,
    sigma: float,
) -> tuple[float, float, float, float, bool]:
    """Advance the CUSUM recursion by one observation.

    Implements ``S+ = max(0, S+ + (x - mu) - k)`` and
    ``S- = max(0, S- - (x - mu) + k)`` with ``k = 0.5*sigma`` and
    ``h = 5.0*sigma``.

    Args:
        value: The observation.
        s_plus: Accumulated upper sum carried in from the previous sample.
        s_minus: Accumulated lower sum carried in from the previous sample.
        mu: Process centre from the trailing baseline.
        sigma: Process spread from the trailing baseline.

    Returns:
        ``(s_plus, s_minus, k, h, alarm)`` where ``alarm`` is True when either
        sum has crossed the decision interval.
    """
    k = CUSUM_K_FACTOR * sigma
    h = CUSUM_H_FACTOR * sigma
    deviation = value - mu
    s_plus = max(0.0, s_plus + deviation - k)
    s_minus = max(0.0, s_minus - deviation + k)
    return s_plus, s_minus, k, h, (s_plus > h or s_minus > h)


def _rolling_stats(window: Sequence[float]) -> tuple[float, float]:
    """Return ``(mean, population_stddev)`` of the trailing window."""
    count = len(window)
    mean = math.fsum(window) / count
    variance = math.fsum((x - mean) ** 2 for x in window) / count
    return mean, math.sqrt(variance)


def _channel_samples(
    session: Session, well_id: str, channel: str
) -> list[tuple[int, float, float]]:
    """Return ``[(row_index, md, value)]`` for one channel, in export order.

    Rows that do not carry the channel are omitted entirely — the returned list
    is the measured record, not a resampled one.
    """
    rows = session.scalars(
        select(TelemetrySample)
        .where(TelemetrySample.well_id == well_id)
        .order_by(TelemetrySample.row_index)
    )
    out: list[tuple[int, float, float]] = []
    for row in rows:
        value = row.channels.get(channel)
        if value is None:
            continue
        try:
            out.append((row.row_index, float(row.md), float(value)))
        except (TypeError, ValueError):
            # A non-numeric cell is absent data, not a measurement.
            continue
    return out


def detect_channel(
    samples: Sequence[tuple[int, float, float]],
    channel: str,
    hazard: str,
    well_id: str,
    window: int = DEFAULT_WINDOW,
) -> tuple[list[dict[str, Any]], ChannelEvaluation]:
    """Run both detectors over one channel's real measurements.

    Args:
        samples: ``[(row_index, md, value)]`` in ascending ``row_index`` order.
        channel: Exported channel name, for labelling.
        hazard: Hazard key the channel is watched for.
        well_id: Owning well, for labelling.
        window: Number of preceding samples forming the baseline.

    Returns:
        ``(alerts, evaluation)``. Each alert is a plain dict shaped like an
        :class:`~app.models.AnomalyAlert` row.
    """
    count = len(samples)
    first_row = samples[0][0] if samples else None
    last_row = samples[-1][0] if samples else None

    if count < window:
        reason = (
            f"only {count} real samples; a causal baseline needs {window} "
            "preceding measurements and no resampling is permitted"
        )
        return [], ChannelEvaluation(channel, count, 0, first_row, last_row, reason)

    alerts: list[dict[str, Any]] = []
    history: list[float] = []
    s_plus = 0.0
    s_minus = 0.0
    previous_row: int | None = None

    for row_index, md, value in samples:
        if previous_row is not None and row_index != previous_row + 1:
            # A gap in the real record. The CUSUM sums are reset *before* this
            # sample is scored, so no stale accumulator can influence a decision
            # made across an interval for which no measurement exists.
            s_plus = 0.0
            s_minus = 0.0

        if len(history) >= window:
            baseline = history[-window:]
            mean, sigma = _rolling_stats(baseline)

            if sigma > _STD_EPSILON:
                z_score = (value - mean) / sigma
                if abs(z_score) >= Z_THRESHOLD:
                    alerts.append(
                        {
                            "well_id": well_id,
                            "row_index": row_index,
                            "md": md,
                            "hazard": hazard,
                            "channel": channel,
                            "detector": "z_score",
                            "severity": severity_for(z_score),
                            "value": value,
                            "baseline_mean": mean,
                            "baseline_std": sigma,
                            "z_score": z_score,
                            "cusum_s_plus": 0.0,
                            "cusum_s_minus": 0.0,
                            "threshold": Z_THRESHOLD,
                            "message": (
                                f"{channel} at {md:,.1f} m MD is "
                                f"{abs(z_score):.1f} sigma from its trailing "
                                f"{window}-sample mean of {mean:,.3f}"
                            ),
                        }
                    )

                s_plus, s_minus, k, h, alarm = cusum_step(value, s_plus, s_minus, mean, sigma)
                if alarm:
                    alerts.append(
                        {
                            "well_id": well_id,
                            "row_index": row_index,
                            "md": md,
                            "hazard": hazard,
                            "channel": channel,
                            "detector": "cusum",
                            "severity": cusum_severity(max(s_plus, s_minus) / h),
                            "value": value,
                            "baseline_mean": mean,
                            "baseline_std": sigma,
                            "z_score": (value - mean) / sigma,
                            "cusum_s_plus": s_plus,
                            "cusum_s_minus": s_minus,
                            "threshold": h,
                            "message": (
                                f"{channel} at {md:,.1f} m MD crossed the CUSUM "
                                f"decision interval h={h:,.3f} "
                                f"(S+={s_plus:,.3f}, S-={s_minus:,.3f}, k={k:,.3f})"
                            ),
                        }
                    )
                    s_plus = 0.0
                    s_minus = 0.0

        history.append(value)
        previous_row = row_index

    alerts.sort(key=lambda a: (a["row_index"], a["channel"], a["detector"]))
    return alerts, ChannelEvaluation(channel, count, len(alerts), first_row, last_row)


def watched_channels(hazard: str) -> tuple[str, ...]:
    """Channels the engine watches for ``hazard``, in engineering priority."""
    return HAZARD_CHANNELS.get(hazard, ())


def detect_well(
    session: Session,
    well_id: str,
    hazard: str,
    window: int = DEFAULT_WINDOW,
) -> dict[str, Any]:
    """Detect anomalies on every watched channel of one well.

    Args:
        session: Open database session.
        well_id: Well whose telemetry is analysed.
        hazard: Hazard key, e.g. ``"stuck_pipe"``.
        window: Number of preceding samples forming the rolling baseline.

    Returns:
        A payload with ``alerts`` (chronological), a per-channel
        ``channels`` breakdown, and the detector configuration used.
    """
    channels = watched_channels(hazard)
    alerts: list[dict[str, Any]] = []
    evaluations: list[ChannelEvaluation] = []

    for channel in channels:
        samples = _channel_samples(session, well_id, channel)
        channel_alerts, evaluation = detect_channel(
            samples, channel, hazard, well_id, window=window
        )
        alerts.extend(channel_alerts)
        evaluations.append(evaluation)

    alerts.sort(key=lambda a: (a["row_index"], a["channel"], a["detector"]))
    evaluated = [e for e in evaluations if e.reason is None]
    return {
        "well_id": well_id,
        "hazard": hazard,
        "window": window,
        "channels_watched": list(channels),
        "channels_evaluated": [e.as_dict() for e in evaluations],
        "channels_skipped": [e.channel for e in evaluations if e.reason is not None],
        "evaluated_channel_count": len(evaluated),
        "alert_count": len(alerts),
        "alerts": alerts,
        "gap_policy": (
            "Samples with no reading are skipped. A gap resets the CUSUM sums "
            "rather than carrying a stale accumulator across an unmeasured "
            "interval. Nothing is resampled or forward-filled."
        ),
    }


def sustained_runs(
    alerts: Iterable[dict[str, Any]],
    run_length: int = SUSTAINED_RUN,
) -> list[list[dict[str, Any]]]:
    """Group consecutive-row CRITICAL alerts into runs of at least ``run_length``.

    "Consecutive" means adjacent ``row_index`` values on the same channel, so a
    burst on one channel cannot be stitched to an unrelated burst on another.
    """
    by_channel: dict[str, list[dict[str, Any]]] = {}
    for alert in alerts:
        if alert["severity"] == "CRITICAL":
            by_channel.setdefault(alert["channel"], []).append(alert)

    runs: list[list[dict[str, Any]]] = []
    for channel_alerts in by_channel.values():
        channel_alerts.sort(key=lambda a: a["row_index"])
        current: list[dict[str, Any]] = []
        for alert in channel_alerts:
            if current and alert["row_index"] == current[-1]["row_index"] + 1:
                current.append(alert)
                continue
            if len(current) >= run_length:
                runs.append(current)
            current = [alert]
        if len(current) >= run_length:
            runs.append(current)
    runs.sort(key=lambda run: run[0]["row_index"])
    return runs

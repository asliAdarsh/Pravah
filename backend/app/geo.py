"""Geodesy and trajectory helpers.

Prototype simplification: all distance maths is done in Python with the haversine
formula on a spherical earth.  The schema keeps plain ``latitude``/``longitude``
columns so it stays portable to PostgreSQL/PostGIS without a migration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

__all__ = [
    "EARTH_RADIUS_KM",
    "TrajectoryPoint",
    "bearing_deg",
    "haversine_km",
    "sample_trajectory",
    "trajectory_from_dicts",
    "trajectory_max_tvd",
    "tvd_at_md",
]


def trajectory_from_dicts(
    points: Iterable[dict[str, Any]] | None,
) -> list[TrajectoryPoint]:
    """Rebuild :class:`TrajectoryPoint` values from the stored JSON column shape."""
    if not points:
        return []
    return [
        TrajectoryPoint(
            md=float(p.get("md", 0.0)),
            tvd=float(p.get("tvd", 0.0)),
            inclination=float(p.get("inclination", 0.0)),
        )
        for p in points
        if isinstance(p, dict)
    ]


EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two lat/lon points.


    Args:
        lat1: Latitude of the first point in decimal degrees.
        lon1: Longitude of the first point in decimal degrees.
        lat2: Latitude of the second point in decimal degrees.
        lon2: Longitude of the second point in decimal degrees.

    Returns:
        Distance in kilometres, rounded to three decimals.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = phi2 - phi1
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.asin(min(1.0, math.sqrt(a)))
    return round(EARTH_RADIUS_KM * c, 3)


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing in degrees (0–360) from point 1 to point 2."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)
    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(
        delta_lambda
    )
    return round((math.degrees(math.atan2(y, x)) + 360. % 360) or 0.0, 2)


@dataclass(frozen=True, slots=True)
class TrajectoryPoint:
    """One sampled point of a well trajectory."""

    md: float
    tvd: float
    inclination: float

    def to_dict(self) -> dict[str, float]:
        """Serialise for the API."""
        return {"md": self.md, "tvd": self.tvd, "inclination": self.inclination}


def sample_trajectory(
    total_md: float,
    kickoff_md: float = 0.0,
    build_rate_deg_per_30m: float = 0.0,
    hold_angle_deg: float = 0.0,
    step_m: float = 50.0,
) -> list[TrajectoryPoint]:
    """Generate a deterministic, monotonically increasing TVD curve for a well.

    The synthetic survey is deliberately simple: vertical above ``kickoff_md``,
    then a constant build rate until ``hold_angle_deg`` is reached, then held.  TVD
    is integrated with a per-step dogleg correction so ``tvd <= md`` always holds.

    Args:
        total_md: Total measured depth of the well.
        kickoff_md: Depth at which deviation build starts.
        build_rate_deg_per_30m: Build rate in degrees per 30 m.
        hold_angle_deg: Final inclination held from the build onwards.
        step_m: Sampling interval in metres.

    Returns:
        Sampled trajectory points sorted by ``md``, inclusive of ``0`` and
        ``total_md``.
    """
    if total_md <= 0:
        return [TrajectoryPoint(md=0.0, tvd=0.0, inclination=0.0)]
    step_m = max(10.0, float(step_m))
    points: list[TrajectoryPoint] = []
    md = 0.0
    inclination = 0.0
    tvd = 0.0
    while md < total_md:
        points.append(TrajectoryPoint(round(md, 2), round(tvd, 2), round(inclination, 2)))
        next_md = min(md + step_m, total_md)
        delta = next_md - md
        if md + 1e-9 < kickoff_md:
            inclination_mid = 0.0
        else:
            target = hold_angle_deg
            increment = build_rate_deg_per_30m * (delta / 30.0)
            inclination_mid = min(target, inclination + increment)
        inclination = inclination_mid
        # Integrate the dogleg: TVD gain is reduced by cos(avg inclination).
        tvd += delta * math.cos(math.radians((points[-1].inclination + inclination) / 2.0))
        tvd = min(tvd, next_md)
        md = next_md
    points.append(TrajectoryPoint(round(md, 2), round(min(tvd, md), 2), round(inclination, 2)))
    # Collapse duplicates created by float rounding.
    deduped: list[TrajectoryPoint] = []
    for point in points:
        if deduped and abs(point.md - deduped[-1].md) < 1e-6:
            deduped[-1] = point
        else:
            deduped.append(point)
    return deduped


def tvd_at_md(points: list[TrajectoryPoint], md: float) -> float:
    """Linearly interpolate TVD at ``md`` from a sampled trajectory.

    Depths above the first or below the last sample clamp to the end value, so a
    caller always gets a usable number.
    """
    if not points:
        return 0.0
    if md <= points[0].md:
        return points[0].tvd
    if md >= points[-1].md:
        return points[-1].tvd
    for lower, upper in zip(points, points[1:]):
        if lower.md <= md <= upper.md:
            span = upper.md - lower.md
            if span <= 1e-9:
                return lower.tvd
            ratio = (md - lower.md) / span
            return round(lower.tvd + ratio * (upper.tvd - lower.tvd), 2)
    return points[-1].tvd


def trajectory_max_tvd(points: list[TrajectoryPoint]) -> float:
    """Return the deepest TVD in a sampled trajectory."""
    return max((p.tvd for p in points), default=0.0)

"""Formation lookup by depth.

A formation is assigned to a depth by a range query against the NPD tops —
never a hand-written per-well value. Kept apart from any seeder so both the
real-data loader and the ingestion pipeline share one implementation.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Formation

__all__ = ["formation_at_tvd"]


def formation_at_tvd(session: Session, tvd: float) -> Formation | None:
    """Return the formation whose depth range contains ``tvd``.

    Boundary handling is half-open ``[top_depth, bottom_depth)`` except for the
    deepest unit, which owns its bottom edge.
    """
    formation = session.scalar(
        select(Formation)
        .where(Formation.top_depth <= tvd, Formation.bottom_depth > tvd)
        .order_by(Formation.top_depth)
        .limit(1)
    )
    if formation is not None:
        return formation
    return session.scalar(
        select(Formation).order_by(Formation.bottom_depth.desc()).limit(1)
    )

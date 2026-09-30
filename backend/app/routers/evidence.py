"""Evidence audit-chain endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import NotFoundError
from ..models import DrillingEvent
from ..services import build_evidence_chain

router = APIRouter(tags=["evidence"])


@router.get("/evidence/{event_id}")
def evidence_chain(event_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Return the full Alert → Reason → Event → Well → Document → Evidence chain.

    ``page`` is reported as ``null`` when the prototype record has no page for the
    excerpt.  A page is never invented.
    """
    event = session.get(DrillingEvent, event_id)
    if event is None:
        raise NotFoundError(f"Unknown event: {event_id}")
    return build_evidence_chain(session, event)

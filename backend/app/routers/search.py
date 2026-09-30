"""Hybrid search endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import NotFoundError
from ..models import Well
from ..schemas import (
    SearchRequest,
    alert_brief,
    document_summary,
    event_detail,
    evidence_brief,
    offset_well_out,
)
from ..search import run_search
from ..services import well_counts

router = APIRouter(tags=["search"])


@router.post("/search", response_model=None)
def search(
    body: SearchRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Run intent parsing + structured retrieval and return a cited answer.

    ``structured_results`` is the primary answer; ``synthesis`` is a secondary,
    clearly-labelled block assembled only from the retrieved records.
    """
    if body.current_well_id and session.get(Well, body.current_well_id) is None:
        raise NotFoundError(f"Unknown well: {body.current_well_id}")
    filters = body.filters.model_dump(exclude_none=True) if body.filters else {}
    result = run_search(
        session,
        query=body.query,
        current_well_id=body.current_well_id,
        filters=filters,
        limit=body.limit,
    )
    structured = result["structured_results"]
    return {
        "query": result["query"],
        "current_well_id": result["current_well_id"],
        "parsed_intent": result["parsed_intent"],
        "retrieval": result["retrieval"],
        "structured_results": {
            "events": [event_detail(event) for event in structured["events"]],
            "wells": [
                offset_well_out(
                    relation,
                    relevant_events=[],
                    document_count=relation.event_count,
                )
                for relation in structured["wells"]
            ],
            "documents": [
                document_summary(
                    document,
                    len(document.events or []),
                    len(document.evidence or []),
                )
                for document in structured["documents"]
            ],
            "mitigations": structured["mitigations"],
            "evidence": [evidence_brief(row) for row in structured["evidence"]],
            "alerts": [alert_brief(alert) for alert in structured["alerts"]],
        },
        "synthesis": result["synthesis"],
        "result_count": result["result_count"],
        "data_provenance": result["data_provenance"],
    }


@router.get("/search/parse", response_model=None)
def parse_only(
    query: str = Query(min_length=1, max_length=2000),
    radius_km: float = Query(default=8.0, gt=0, le=200),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return just the parsed intent — useful for debugging the NL front end."""
    from ..search import parse_intent

    intent = parse_intent(session, query, default_radius_km=radius_km)
    return {"query": query, "parsed_intent": intent.to_dict()}

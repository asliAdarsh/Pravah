"""Graph-constrained retrieval over the real corpus.

:mod:`app.search` answers *"which rows match this question"*. This module
answers the follow-up question the graph makes possible: *"given the rows that
match, what else in the corpus is actually connected to them, and how?"*.

Nothing here generates prose. Every result is a :class:`~app.models.Evidence`
row — a literal span of a real Daily Drilling Report — reached by a real chain
of graph edges. The chain travels with the result, so the UI can show why a
snippet surfaced, and the hop count is reported rather than hidden.

Retrieval shape::

    query ──parse_intent──> ranked event seeds
                          └─ graph BFS (max_hops) ──> ReportSnippet nodes
                                                        + Event/Well/Formation
                                                          context and hop path

The structured/lexical search still runs first and only decides the seeds; the
graph is what decides reachability, so a snippet can only come back if a real
edge chain connects it to a seed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .graph import expand, labels_for, node_exists, node_id, normalise_node_id
from .models import DrillingEvent, Evidence, Well
from .search import parse_intent, run_search

__all__ = ["PROVENANCE", "retrieve"]

#: Stamped on every result so the UI never has to guess how it was found.
PROVENANCE = "GRAPH_TRAVERSAL"

#: Cap on how many ranked events are used as traversal seeds.
_SEED_CAP = 8

#: How many reachable snippets to examine per result the caller asked for.
#: Some candidates drop out (the backing event is gone, the span is empty), so
#: the traversal looks further ahead than it returns.
_SNIPPET_CANDIDATE_FACTOR = 6



def _event_context(session: Session, event_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Read the event rows behind a batch of snippet results in one query."""
    if not event_ids:
        return {}
    events = {
        row.id: row
        for row in session.scalars(
            select(DrillingEvent).where(DrillingEvent.id.in_(event_ids))
        )
    }
    well_ids = {row.well_id for row in events.values() if row.well_id}
    wells = {
        row.id: row
        for row in session.scalars(select(Well).where(Well.id.in_(well_ids)))
    }
    context: dict[str, dict[str, Any]] = {}
    for event_id, event in events.items():
        well = wells.get(event.well_id)
        formation = event.formation
        context[event_id] = {
            "event": {
                "id": event.id,
                "event_type": event.event_type,
                "md": event.md,
                "tvd": event.tvd,
                "severity": event.severity,
                "status": event.status,
                "day_number": event.day_number,
                "description": event.description,
                "well_id": event.well_id,
                "document_id": event.document_id,
                "is_simulated": bool(event.is_simulated),
            },
            "well": (
                {
                    "id": well.id,
                    "name": well.name,
                    "field": well.field,
                    "block": well.block,
                    "operator": well.operator,
                    "well_type": well.well_type,
                    "status": well.status,
                    "is_simulated": bool(well.is_simulated),
                }
                if well is not None
                else None
            ),
            "formation": (
                {
                    "code": formation.code,
                    "name": formation.name,
                    "top_depth": formation.top_depth,
                    "bottom_depth": formation.bottom_depth,
                    "lithology": formation.lithology,
                }
                if formation is not None
                else None
            ),
        }
    return context


def _snippet_payload(
    row: Evidence,
    record: dict[str, Any],
    context: dict[str, dict[str, Any]],
    seed_rank: int,
    labels: list[str],
) -> dict[str, Any] | None:
    """Assemble one result, or ``None`` when it cannot be backed by a real row.

    ``row`` is the ``Evidence`` row the graph node was built from, re-read from
    the database, so a result is only ever emitted for a span the corpus
    currently holds.
    """
    details = context.get(row.event_id)
    if details is None or not (row.text_span or "").strip():
        return None
    return {
        "provenance": PROVENANCE,
        "hops": record["hops"],
        "seed": record["seed"],
        "seed_rank": seed_rank,
        "path": list(record["path"]),
        "path_steps": list(record["steps"]),
        "path_labels": labels,
        "snippet": {
            "evidence_id": row.id,
            "event_id": row.event_id,
            "document_id": row.document_id,
            "page": row.page,
            "section": row.section,
            "text": row.text_span,
            "confidence": row.confidence,
            "extraction_method": row.extraction_method,
            "is_simulated": bool(row.is_simulated),
        },
        **details,
    }


def retrieve(
    session: Session,
    query: str,
    root_id: str,
    max_hops: int = 3,
    limit: int = 10,
) -> dict[str, Any]:
    """Retrieve real report snippets reachable from the top-ranked event seeds.

    Args:
        session: Open SQLAlchemy session.
        query: Natural-language question, parsed by :func:`app.search.parse_intent`.
        root_id: The well or event the retrieval is anchored to, as a graph node
            id (``WELL:15/9-F-9A``). Also used as the traversal root when the
            query yields no ranked events.
        max_hops: How far the traversal may travel from each seed.
        limit: Maximum number of snippets returned.

    Returns:
        ``{"query", "root", "max_hops", "parsed_intent", "seeds", "results",
        "result_count", "provenance", "data_provenance"}``. Each result is a
        stored ``Evidence`` row plus its graph path.

    Raises:
        KeyError: when ``root_id`` is not a node in the graph.
    """
    if max_hops < 1:
        raise ValueError("max_hops must be >= 1")
    if limit < 1:
        raise ValueError("limit must be >= 1")

    try:
        root_id = normalise_node_id(root_id)
    except ValueError:
        raise KeyError(root_id) from None
    if not node_exists(session, root_id):
        raise KeyError(root_id)

    intent = parse_intent(session, query)
    current_well_id = root_id.split(":", 1)[1] if root_id.startswith("WELL:") else None

    ranked = run_search(
        session,
        query=query,
        current_well_id=current_well_id,
        limit=_SEED_CAP,
    )
    seed_events = list(ranked["structured_results"]["events"][:_SEED_CAP])
    if seed_events:
        seed_mode = "RANKED_EVENT_SEEDS"
        seeds = [
            {
                "node": node_id("Event", event.id),
                "event_id": event.id,
                "rank": rank,
                "event_type": event.event_type,
                "md": event.md,
                "tvd": event.tvd,
            }
            for rank, event in enumerate(seed_events)
        ]
    else:
        # Nothing matched the question textually. Say so, and traverse from the
        # requested root rather than inventing a relevance ranking.
        seed_mode = "ROOT_FALLBACK"
        seeds = [
            {
                "node": root_id,
                "event_id": None,
                "rank": 0,
                "event_type": None,
                "md": None,
                "tvd": None,
            }
        ]

    reached = expand(
        session,
        [seed["node"] for seed in seeds],
        max_hops=max_hops,
        limit=limit * _SNIPPET_CANDIDATE_FACTOR,
        node_type="ReportSnippet",
    )
    if not reached:
        return {
            "query": query,
            "root": root_id,
            "max_hops": max_hops,
            "provenance": PROVENANCE,
            "data_provenance": ranked["data_provenance"],
            "parsed_intent": intent.to_dict(),
            "seed_mode": seed_mode,
            "seeds": seeds,
            "results": [],
            "result_count": 0,
            "snippets_examined": 0,
            "explanation": (
                f"no ReportSnippet node is within {max_hops} hop(s) of the "
                f"{len(seeds)} traversal seed(s); the corpus is not connected "
                "that far from this root"
            ),
        }

    evidence_ids = [record["node"].split(":", 1)[1] for record in reached]
    evidence = {
        row.id: row
        for row in session.scalars(select(Evidence).where(Evidence.id.in_(evidence_ids)))
    }
    context = _event_context(
        session, sorted({row.event_id for row in evidence.values()})
    )

    results: list[dict[str, Any]] = []
    for record in reached:
        row = evidence.get(record["node"].split(":", 1)[1])
        if row is None:
            continue
        payload = _snippet_payload(
            row,
            record,
            context,
            record["seed_rank"],
            labels_for(session, record["path"]),
        )
        if payload is not None:
            results.append(payload)
        if len(results) >= limit:
            break

    return {
        "query": query,
        "root": root_id,
        "max_hops": max_hops,
        "provenance": PROVENANCE,
        "data_provenance": ranked["data_provenance"],
        "parsed_intent": intent.to_dict(),
        "seed_mode": seed_mode,
        "seeds": seeds,
        "results": results,
        "result_count": len(results),
        "snippets_examined": len(evidence),
    }

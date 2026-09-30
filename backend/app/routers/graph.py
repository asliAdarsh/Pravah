"""Knowledge-graph endpoints.

The router validates and delegates: every traversal, path and retrieval is done
in :mod:`app.graph` and :mod:`app.graphrag`. Unknown node ids surface as 404.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import NotFoundError
from ..graph import path_between, stats, subgraph
from ..graphrag import retrieve

router = APIRouter(prefix="/graph", tags=["graph"])


class GraphRetrieveRequest(BaseModel):
    """Body of ``POST /graph/retrieve``."""

    model_config = {"extra": "forbid"}

    query: str = Field(min_length=1, max_length=2000)
    root_id: str = Field(min_length=1, max_length=128)
    max_hops: int = Field(default=3, ge=1, le=8)
    limit: int = Field(default=10, ge=1, le=100)


@router.get("/stats", response_model=None)
def graph_stats(session: Session = Depends(get_db)) -> dict[str, Any]:
    """Node and edge counts by type for the current corpus."""
    return stats(session)


@router.get("/subgraph", response_model=None)
def graph_subgraph(
    root: str = Query(min_length=1, max_length=128, description="Node id, e.g. WELL:15/9-F-9A"),
    depth: int = Query(default=2, ge=0, le=8),
    limit: int = Query(default=200, ge=1, le=5000),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Breadth-first neighbourhood of a node, capped at ``limit`` nodes.

    Raises:
        NotFoundError: when ``root`` is not in the graph.
    """
    try:
        return subgraph(session, root, depth=depth, limit=limit)
    except KeyError:
        raise NotFoundError(f"Unknown graph node: {root}") from None


@router.get("/path", response_model=None)
def graph_path(
    source: str = Query(alias="from", min_length=1, max_length=128),
    target: str = Query(alias="to", min_length=1, max_length=128),
    max_hops: int = Query(default=4, ge=1, le=12),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Shortest edge chain between two nodes.

    Raises:
        NotFoundError: when either node is not in the graph.
    """
    try:
        return path_between(session, source, target, max_hops=max_hops)
    except KeyError as exc:
        raise NotFoundError(f"Unknown graph node: {exc.args[0]}") from None


@router.post("/retrieve", response_model=None)
def graph_retrieve(
    body: GraphRetrieveRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Graph-constrained retrieval of real report snippets.

    Raises:
        NotFoundError: when ``root_id`` is not a node in the graph.
    """
    try:
        return retrieve(
            session,
            query=body.query,
            root_id=body.root_id,
            max_hops=body.max_hops,
            limit=body.limit,
        )
    except KeyError:
        raise NotFoundError(f"Unknown graph node: {body.root_id}") from None

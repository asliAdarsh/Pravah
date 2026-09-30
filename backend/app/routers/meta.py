"""Health, meta and demo-scenario endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import APP_NAME, APP_VERSION, DATA_PROVENANCE, ENGINE_VERSION
from ..db import get_db
from ..errors import NotFoundError
from ..services import build_demo_scenario, build_meta
from ..llm import llm_status

router = APIRouter(tags=["meta"])


@router.get("/health")
def health(session: Session = Depends(get_db)) -> dict[str, Any]:
    """Liveness probe with the engine and database status."""
    try:
        session.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        database = f"error: {exc}"
    return {
        "status": "ok",
        "version": APP_VERSION,
        "engine_version": ENGINE_VERSION,
        "database": database,
        "real_data": True,
        "data_provenance": DATA_PROVENANCE,
    }


@router.get("/meta")
def meta(session: Session = Depends(get_db)) -> dict[str, Any]:
    """Registry, counts, engine configuration and LLM status."""
    payload = build_meta(session)
    payload["llm"] = llm_status()
    return payload


@router.post("/demo/scenario")
def demo_scenario(
    well_id: str | None = Query(default=None, description="Well for the scenario bundle; defaults to the active well"),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the whole demo scenario in one call (fast first paint for judges)."""
    try:
        return build_demo_scenario(session, well_id)
    except KeyError as exc:
        raise NotFoundError(f"Unknown well: {exc.args[0]}") from exc

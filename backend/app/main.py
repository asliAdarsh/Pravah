"""FastAPI application factory for Pravah.

Responsibilities kept here (and nowhere else):

* mount every router under the ``/api/v1`` prefix,
* configure CORS for the Vite dev server,
* initialise the database and seed it on startup (idempotent),
* translate domain errors into the contract's ``{"detail": ...}`` shape.

All business logic lives in the service and engine modules.
"""

from __future__ import annotations

from importlib import import_module

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import (
    APP_NAME,
    APP_VERSION,
    DATA_PROVENANCE,
    DATASET_LABEL,
    DEFAULT_DB_PATH,
    ENGINE_VERSION,
    settings,
)
from .db import database
from .errors import DomainError
from .routers import alerts as alerts_router
from .routers import config as config_router
from .routers.config import ahp_router, risk_router
from .routers import documents as documents_router
from .routers import evidence as evidence_router
from .routers import events as events_router
from .routers import meta as meta_router
from .routers import search as search_router
from .routers import wells as wells_router

__all__ = ["API_PREFIX", "app", "create_app"]

logger = logging.getLogger("pravah")

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the schema and load the real dataset when the DB is empty."""
    from .seed_real import is_seeded, seed_real

    database.create_all()
    with database.session() as session:
        if settings.reset_db:
            logger.info("PRAVAH_RESET_DB set — reloading the real dataset.")
            counts = seed_real(session, max_reports=settings.max_ddr_reports, force=True)
        elif is_seeded(session):
            logger.info("Real dataset already present — skipping load.")
            counts = None
        else:
            counts = seed_real(session, max_reports=settings.max_ddr_reports)
        if counts:
            logger.info("Loaded real dataset: %s", counts)
    yield
    database.dispose()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    application = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        description=(
            "Pravah — Enhanced Real-Time Monitoring, Analysis and Correlation "
            "with Near-Well Intelligence. "
            f"{DATASET_LABEL}, from published Norwegian Petroleum Directorate and "
            "Equinor Volve records. Relevance and risk are heuristics, not "
            "validated predictions."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for module in (
        meta_router,
        wells_router,
        events_router,
        evidence_router,
        documents_router,
        alerts_router,
        search_router,
        config_router,
    ):
        application.include_router(module.router, prefix=API_PREFIX)
    #: ``POST /api/v1/risk/recompute`` is a top-level route in the contract.
    application.include_router(risk_router, prefix=API_PREFIX)
    #: AHP weighting for offset selection.
    application.include_router(ahp_router, prefix=API_PREFIX)
    #: The telemetry and knowledge-graph surfaces are optional modules. They are
    #: mounted when importable and reported loudly when not, so a missing optional
    #: module degrades one screen instead of taking the whole API down.
    for module_name in ("telemetry", "graph"):
        try:
            module = import_module(f"{__package__}.routers.{module_name}")
        except ImportError as exc:
            logger.warning("Optional router %r not mounted: %s", module_name, exc)
            continue
        application.include_router(module.router, prefix=API_PREFIX)

    @application.exception_handler(DomainError)
    async def _domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        """Render domain errors using the contract's ``{"detail": ...}`` shape."""
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.payload()})

    @application.get("/", include_in_schema=False)
    def root() -> dict[str, Any]:
        """Point a human at the API root."""
        return {
            "app": APP_NAME,
            "version": APP_VERSION,
            "engine_version": ENGINE_VERSION,
            "data_provenance": DATA_PROVENANCE,
            "dataset_label": DATASET_LABEL,
            "api_prefix": API_PREFIX,
            "docs": "/docs",
            "health": f"{API_PREFIX}/health",
        }

    return application


app = create_app()

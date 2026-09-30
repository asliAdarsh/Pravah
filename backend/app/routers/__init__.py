"""Routers for the Pravah API.

Every router follows the same rule: **validate, then delegate**.  Business logic
lives in :mod:`app.services`, :mod:`app.relevance`, :mod:`app.risk`,
:mod:`app.search` and :mod:`app.ingestion`.
"""

from __future__ import annotations

__all__ = [
    "alerts",
    "config",
    "documents",
    "evidence",
    "events",
    "formations",
    "meta",
    "search",
    "wells",
]

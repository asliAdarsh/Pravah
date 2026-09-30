"""Document list/detail/ingest endpoints."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import IngestError, NotFoundError
from ..ingestion import available_ocr_engines, ingest_document
from ..models import Document
from ..schemas import document_detail, document_summary, event_detail

router = APIRouter(tags=["documents"])

DEFAULT_LIMIT = 200


@router.get("/documents")
def list_documents(
    well_id: str | None = Query(default=None),
    doc_type: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """List documents with their event and evidence counts."""
    stmt = select(Document)
    count_stmt = select(func.count()).select_from(Document)
    if well_id:
        stmt = stmt.where(Document.well_id == well_id)
        count_stmt = count_stmt.where(Document.well_id == well_id)
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type.upper())
        count_stmt = count_stmt.where(Document.doc_type == doc_type.upper())
    total = session.scalar(count_stmt) or 0
    documents = session.scalars(
        stmt.order_by(Document.doc_date.desc(), Document.id).limit(limit).offset(offset)
    )
    return {
        "items": [
            document_summary(
                document,
                len(document.events or []),
                len(document.evidence or []),
            )
            for document in documents
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "data_provenance": "REAL_PUBLIC_DATA",
    }


@router.get("/documents/ingest/capabilities")
def ingest_capabilities() -> dict[str, Any]:
    """Report which OCR backends can actually run in this environment."""
    return {
        "ocr_engines": available_ocr_engines(),
        "accepted_content_types": [
            "application/json",
            "text/plain",
            "application/pdf",
            "multipart/form-data",
        ],
        "note": (
            "OCR is optional. When no engine is installed the pipeline reports the "
            "ocr stage as SKIPPED with simulated=true and uses the embedded text layer."
        ),
    }


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Full document payload with excerpt and sections."""
    document = session.get(Document, doc_id)
    if document is None:
        raise NotFoundError(f"Unknown document: {doc_id}")
    return document_detail(
        document,
        len(document.events or []),
        len(document.evidence or []),
    )


@router.post("/documents/ingest", status_code=201)
async def ingest(
    request: Request,
    file: UploadFile | None = File(default=None),
    well_id: str | None = Form(default=None),
    doc_type: str | None = Form(default=None),
    doc_date: str | None = Form(default=None),
    ocr_engine: str | None = Form(default=None),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Run the ingestion pipeline over an operator-supplied document.

    Accepts either a JSON body (``{"filename", "well_id", "doc_type", "text"}``)
    or a multipart upload.  Unparseable input returns ``201`` with
    ``warnings`` and an empty ``extracted_events`` list — it never crashes.
    """
    payload: dict[str, Any] = {}
    content_type = (request.headers.get("content-type") or "").lower()
    if file is not None:
        payload = {
            "filename": file.filename or "upload",
            "well_id": well_id,
            "doc_type": doc_type or "DDR",
            "doc_date": doc_date,
            "ocr_engine": ocr_engine or "NONE",
        }
    elif "application/json" in content_type:
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise IngestError(f"Request body is not valid JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise IngestError("JSON body must be an object.")
        payload = dict(body)
        payload.setdefault("filename", "operator-upload.json")
        payload.setdefault("doc_type", "DDR")
        payload.setdefault("ocr_engine", "NONE")
    else:
        raise IngestError(
            "Send either application/json with a 'text' field, or multipart/form-data "
            "with a 'file' part."
        )

    # A JSON body must actually carry something to ingest. Accepting `{}` and
    # storing a named-but-empty document hid a failed upload behind a 201.
    if file is None and not str(payload.get("text") or "").strip():
        raise IngestError(
            "Nothing to ingest: send a non-empty 'text' field, or a multipart "
            "upload with a 'file' part."
        )

    raw_date = payload.get("doc_date")
    parsed_date: date | None = None
    if raw_date:
        try:
            parsed_date = date.fromisoformat(str(raw_date))
        except ValueError as exc:
            raise IngestError(f"doc_date must be an ISO-8601 date, got {raw_date!r}") from exc

    filename = str(payload.get("filename") or "upload")
    text = payload.get("text")
    result = ingest_document(
        session,
        filename=filename,
        text=str(text) if text is not None else None,
        payload=await file.read() if file is not None else None,
        well_id=payload.get("well_id") or None,
        doc_type=str(payload.get("doc_type") or "DDR"),
        doc_date=parsed_date,
        ocr_engine=str(payload.get("ocr_engine") or "NONE"),
        title=payload.get("title"),
    )
    document = result.document
    session.commit()
    return {
        "document": document_summary(document, len(result.events), len(result.events)),
        "pipeline": result.pipeline,
        "extracted_events": [event_detail(event) for event in result.events],
        "sections": result.sections,
        "warnings": result.warnings,
        "data_provenance": "OPERATOR_SUPPLIED_UNVERIFIED",
    }

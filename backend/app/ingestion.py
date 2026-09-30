"""Document ingestion pipeline with a pluggable OCR backend.

Stages
------
``ocr``
    Text acquisition.  A PDF goes through a pluggable OCR backend (PaddleOCR when
    importable, otherwise an explicit ``SKIPPED`` result); plain text goes through
    the embedded text layer.  When no OCR engine is available the stage reports
    ``status="SKIPPED"`` and ``simulated=True`` and says in ``detail`` that the
    text layer was used — it never pretends a model ran.
``layout``
    Line/block segmentation and section-heading detection.
``sections``
    Split the text into ``{heading, page, text}`` records.
``entities``
    Well ids, event types, depths, severities and mitigations pulled from text.
``events``
    Assemble the recognised entities into candidate event records.
``depth_normalization``
    Convert the reported depth into MD/TVD using the well's trajectory.
``formation_mapping``
    Range lookup: ``formation_at_tvd``.  Never a per-well hard-coded value.

The pipeline never raises on bad input: unparseable documents come back with
``warnings`` and ``extracted_events: []``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .event_types import EXTRACTION_VOCABULARY
from .geo import trajectory_from_dicts, tvd_at_md
from .datasources import EVENT_VOCABULARY as REAL_DDR_VOCABULARY
from .models import Document, DrillingEvent, Evidence, Formation, Well
from .formations import formation_at_tvd

__all__ = [
    "IngestionResult",
    "OcrBackend",
    "available_ocr_engines",
    "extract_pdf_text",
    "ingest_document",
    "next_document_id",
    "parse_report_text",
]

HEADING_RE = re.compile(
    r"^\s*(?:(day\s+\d+[^\n]*)|(mitigation[^\n]*)|(hole\s+condition[^\n]*)"
    r"|(summary[^\n]*)|(narrative[^\n]*)|(\d{1,2}\.\s+[A-Z][^\n]*)"
    r"|((?:daily|well)\s+\w*\s*report[^\n]*))\s*$",
    re.I,
)

# DDR/WCR narratives write the response on its own line ("MITIGATION: reduced ROP
# to ...").  Those are continuation fields of the event above them, not section
# headings, so they must not consume the event's description or depth.
INLINE_FIELD_RE = re.compile(
    r"^\s*(mitigation|remedial action|action taken|actions? taken)\s*[:\-]\s*(.+)$", re.I
)
DEPTH_RE = re.compile(
    r"(?P<md>\d[\d,]{2,})\s*m(?:d)?\s*(?:md)?", re.I
)
TVD_RE = re.compile(r"(?P<tvd>\d[\d,]{2,})\s*m\s*tvd", re.I)
SEVERITY_RE = re.compile(
    r"\b(critical|high|moderate|low)\b(?=[^.]*severity)|severity\s+(critical|high|moderate|low)",
    re.I,
)
WELL_RE = re.compile(r"\b([A-Z]{2,}[A-Z0-9]*-\d{1,3})\b")
BVL_RE = re.compile(r"bbl", re.I)


# --------------------------------------------------------------------------- #
# OCR backends
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class OcrResult:
    """The outcome of one OCR attempt."""

    text: str
    engine: str
    status: str
    detail: str
    simulated: bool


class OcrBackend:
    """Base class for pluggable OCR backends.

    Subclasses implement :meth:`recognise` and return the extracted text.
    """

    name: str = "NONE"

    def is_available(self) -> bool:
        """Return ``True`` when the backend can actually run."""
        return False

    def recognise(self, payload: bytes, filename: str) -> tuple[str, str]:
        """Return ``(text, detail)`` extracted from ``payload``."""
        raise NotImplementedError


class TextLayerBackend(OcrBackend):
    """Fallback backend that serves an already-embedded text layer."""

    name = "TEXT_LAYER"

    def is_available(self) -> bool:
        """Always available: the caller supplies the text directly."""
        return True

    def recognise(self, payload: bytes, filename: str) -> tuple[str, str]:
        """Decode ``payload`` as UTF-8 text, replacing undecodable bytes."""
        return payload.decode("utf-8", errors="replace"), "Decoded the embedded text layer."


class PaddleOcrBackend(OcrBackend):
    """PaddleOCR adapter, active only when the package is importable."""

    name = "PADDLEOCR"

    def _module(self):
        """Import PaddleOCR lazily; return ``None`` when unavailable."""
        try:
            from paddleocr import PaddleOCR  # type: ignore

            return PaddleOCR
        except Exception:  # noqa: BLE001 - any import failure means unavailable
            return None

    def is_available(self) -> bool:
        """True only when PaddleOCR can actually be imported."""
        return self._module() is not None

    def recognise(self, payload: bytes, filename: str) -> tuple[str, str]:
        """Run PaddleOCR over the payload and join the recognised lines."""
        module = self._module()
        if module is None:
            raise RuntimeError("PaddleOCR is not installed")
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / (filename or "upload.bin")
            path.write_bytes(payload)
            engine = module(use_angle_cls=True, lang="en", show_log=False)
            result = engine.ocr(str(path), cls=True)
        lines: list[str] = []
        for page in result or []:
            for entry in page or []:
                text, _confidence = entry[1]
                if text:
                    lines.append(text)
        return "\n".join(lines), f"Recognised {len(lines)} line(s) with PaddleOCR."


def available_ocr_engines() -> list[str]:
    """Return the names of the OCR backends that can actually run."""
    engines = [TextLayerBackend().name]
    if PaddleOcrBackend().is_available():
        engines.append(PaddleOcrBackend().name)
    return engines


def extract_pdf_text(payload: bytes) -> tuple[str, str]:
    """Extract text from a PDF using ``pypdf`` when it is installed.

    Returns:
        ``(text, detail)``.  The detail string names the extractor so the pipeline
        can report exactly what ran.
    """
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:  # noqa: BLE001 - guarded so the app runs without pypdf
        return "", "pypdf is not installed; PDF text layer unavailable."
    import io

    try:
        reader = PdfReader(io.BytesIO(payload))
        chunks = []
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
        text = "\n".join(chunks).strip()
        return text, f"Extracted {len(reader.pages)} page(s) with pypdf."
    except Exception as exc:  # noqa: BLE001 - malformed PDFs must not crash
        return "", f"pypdf could not read the file: {exc}"


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class IngestionResult:
    """Everything one ingestion run produced."""

    document: Document | None = None
    pipeline: list[dict[str, Any]] = field(default_factory=list)
    events: list[DrillingEvent] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    text: str = ""

    def stage(self, name: str, status: str, detail: str, simulated: bool = False) -> None:
        """Append one pipeline stage record."""
        self.pipeline.append(
            {
                "stage": name,
                "status": status,
                "detail": detail,
                "simulated": simulated,
            }
        )


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def _tokenize_heading(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def split_sections(text: str) -> list[dict[str, Any]]:
    """Split report text into ``{heading, page, text}`` records.

    Headings are detected by :data:`HEADING_RE`.  ``page`` is always ``None``:
    the pipeline works on a flat text layer and must never invent a page number.
    """
    sections: list[dict[str, Any]] = []
    current_heading = "Document"
    buffer: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()
        # An inline response field belongs to the event above it: merge it into
        # that line so the response cannot be read as its own event, and so the
        # event keeps its own depth instead of the response's return depth.
        inline = INLINE_FIELD_RE.match(line)
        if inline and buffer:
            buffer[-1] = f"{buffer[-1]} {inline.group(1).upper()}: {inline.group(2).strip()}"
            continue
        if HEADING_RE.match(line) and line.strip():
            if buffer and "".join(buffer).strip():
                sections.append(
                    {
                        "heading": current_heading,
                        "page": None,
                        "text": "\n".join(buffer).strip(),
                    }
                )
            current_heading = _tokenize_heading(line)
            buffer = []
            continue
        buffer.append(line)
    if buffer and "".join(buffer).strip():
        sections.append(
            {
                "heading": current_heading,
                "page": None,
                "text": "\n".join(buffer).strip(),
            }
        )
    return sections


def _parse_depth(text: str) -> tuple[float, float | None]:
    """Return ``(md, tvd)`` parsed from the text; ``tvd`` is ``None`` when absent.

    A reading written as ``1500 m TVD`` matches the generic MD pattern first, so
    it is reclassified as a TVD reading rather than being recorded twice.
    """
    md_match = DEPTH_RE.search(text)
    tvd_match = TVD_RE.search(text)
    md = float(md_match.group("md").replace(",", "")) if md_match else None
    tvd = float(tvd_match.group("tvd").replace(",", "")) if tvd_match else None
    if md is not None and "tvd" in md_match.group(0).lower():
        md = None
    return md, tvd


def _effective_vocabulary(
    vocabulary: dict[str, tuple[str, ...]] | None,
) -> dict[str, tuple[str, ...]]:
    """Merge the report-template phrases with the real Volve operators' register.

    The published DDR corpus is written the way engineers actually talk — "stuck
    string", "bit balled", "tractor stalled out" — none of which appear in the
    report-template phrasing. Defaulting to the template list alone meant a
    genuine uploaded Volve report extracted almost nothing.
    """
    if vocabulary is not None:
        return vocabulary
    merged: dict[str, tuple[str, ...]] = {
        code: tuple(terms) for code, terms in EXTRACTION_VOCABULARY.items()
    }
    for code, terms in REAL_DDR_VOCABULARY.items():
        merged[code] = tuple(dict.fromkeys((*merged.get(code, ()), *terms)))
    return merged


def _match_event_type(text: str, vocabulary: dict[str, tuple[str, ...]] | None = None) -> str | None:
    """Return the event type whose hazard phrase appears in ``text``.

    Default vocabulary is :data:`SORTED_EXTRACTION_VOCABULARY` (hazard phrases
    only, not the wider search index — query keywords like "rop" or "wob" occur
    in ordinary progress lines and would fabricate events out of normal
    drilling). ``vocabulary`` overrides it, which is how the real Volve DDR
    corpus is read: operators write "stuck string", "overpull-torque" and
    "bit balled", none of which appear in the report-template phrasing.
    """
    lowered = text.lower()
    for event_type, terms in _effective_vocabulary(vocabulary).items():
        if any(term.lower() in lowered for term in terms):
            return event_type
    return None


def _parse_severity(text: str) -> str:
    """Return the severity named in the text, defaulting to ``MODERATE``."""
    match = SEVERITY_RE.search(text)
    if match:
        token = (match.group(1) or match.group(2) or "").upper()
        if token in ("LOW", "MODERATE", "HIGH", "CRITICAL"):
            return token
    return "MODERATE"


SEVERITY_SCORE = {"LOW": 0.25, "MODERATE": 0.5, "HIGH": 0.8, "CRITICAL": 1.0}


def _split_mitigation(line: str) -> tuple[str, str]:
    """Split ``"<event …> MITIGATION: <response>"`` into description and response."""
    match = re.search(r"\b(?:MITIGATION|REMEDIAL ACTION|ACTIONS? TAKEN)\s*[:\-]\s*", line, re.I)
    if not match:
        return line.strip(), ""
    return line[: match.start()].strip(), line[match.end() :].strip()


def parse_report_text(
    text: str, vocabulary: dict[str, tuple[str, ...]] | None = None
) -> list[dict[str, Any]]:
    """Extract candidate event records from DDR/WCR-style report text.

    Extraction is per *event line*, not per section: one DDR day routinely records
    several hazards at different depths, and the depth that belongs to an event is
    the one written on its own line. A section-level depth is only a fallback for
    prose that names the hazard without repeating a reading.

    A candidate needs at least a recognised event type and a depth; anything less
    is reported as a warning by the caller rather than being invented into an
    event.

    Args:
        text: The document's text layer.

    Returns:
        A list of dicts with ``event_type``, ``md``, ``tvd``, ``severity``,
        ``description``, ``mitigation`` and ``section``.
    """
    sections = split_sections(text)
    candidates: list[dict[str, Any]] = []
    for section_index, section in enumerate(sections):
        lines = [line for line in section["text"].splitlines() if line.strip()]
        section_md, section_tvd = _parse_depth(section["text"])
        matched_any = False

        for line in lines:
            if INLINE_FIELD_RE.match(line):
                # A response field with no event line above it (e.g. at the top of
                # a section) is a response, not an event in its own right.
                continue
            event_type = _match_event_type(line, vocabulary)
            if event_type is None:
                continue
            description, mitigation = _split_mitigation(line)
            if not mitigation:
                mitigation = _section_mitigation(sections, section_index)
            md, tvd = _parse_depth(line)
            if md is None and tvd is None:
                md, tvd = section_md, section_tvd
            if md is None and tvd is None:
                continue
            matched_any = True
            candidates.append(
                {
                    "event_type": event_type,
                    "md": md,
                    "tvd": tvd,
                    "severity": _parse_severity(line),
                    "description": description[:2000],
                    "mitigation": mitigation[:2000],
                    "section": section["heading"],
                }
            )

        if matched_any:
            continue

        # Prose form: the hazard and its reading span several lines with no single
        # event line. Fall back to one candidate for the section.
        event_type = _match_event_type(section["text"], vocabulary)
        if event_type is None or (section_md is None and section_tvd is None):
            continue
        description, mitigation = _split_mitigation(section["text"])
        candidates.append(
            {
                "event_type": event_type,
                "md": section_md,
                "tvd": section_tvd,
                "severity": _parse_severity(section["text"]),
                "description": description[:2000],
                "mitigation": mitigation[:2000],
                "section": section["heading"],
            }
        )
    return candidates


def _section_mitigation(sections: list[dict[str, Any]], index: int) -> str:
    """Return the response text from a dedicated mitigation section.

    DDRs write the response either inline on the event line or in a following
    "Mitigation of Record" block. Both forms are real; the second is not lost.
    """
    for offset, other in enumerate(sections[index + 1 : index + 3], start=1):
        if "mitigation" in other["heading"].lower() or "remedial" in other["heading"].lower():
            return other["text"].strip()
        del offset
    return ""


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def next_document_id(session: Session) -> str:
    """Return the next ``DOC-nnnn`` identifier."""
    count = session.scalar(select(func.count()).select_from(Document)) or 0
    return f"DOC-{count + 1:04d}"


def _ingest_well_id(
    session: Session, text: str, filename: str, requested_well_id: str | None
) -> tuple[str | None, list[str]]:
    """Resolve the target well from the request, the filename, then the text."""
    warnings: list[str] = []
    if requested_well_id:
        well = session.get(Well, requested_well_id)
        if well is None:
            warnings.append(f"Unknown well_id '{requested_well_id}'; document stored unattached.")
            return None, warnings
        return well.id, warnings
    match = WELL_RE.search(text or "") or WELL_RE.search(filename or "")
    if match:
        candidate = match.group(1)
        if session.get(Well, candidate) is not None:
            return candidate, warnings
    warnings.append(
        "No well could be resolved from the payload; the document was stored unattached "
        "(well_id: null) and its events were not linked to a well."
    )
    return None, warnings


def _default_doc_date() -> date:
    """Return today's date for a document that did not state one."""
    return datetime.now().date()


def ingest_document(
    session: Session,
    filename: str,
    text: str | None = None,
    payload: bytes | None = None,
    well_id: str | None = None,
    doc_type: str = "DDR",
    doc_date: date | None = None,
    ocr_engine: str = "NONE",
    title: str | None = None,
) -> IngestionResult:
    """Run the ingestion pipeline and persist everything it extracts.

    Args:
        session: Open SQLAlchemy session; the caller commits.
        filename: Original filename, used to pick the extraction path and to hint
            the well id.
        text: Pre-extracted text (JSON ingest).  When absent, a PDF payload is
            read with ``pypdf`` and, failing that, an OCR backend.
        payload: Raw uploaded bytes (multipart ingest).
        well_id: Optional target well; resolved from text/filename when omitted.
        doc_type: Document type code (``DDR``, ``WCR``, …).
        doc_date: Document date; defaults to today when not supplied.
        ocr_engine: Requested OCR engine name; ``NONE`` means "no OCR".
        title: Optional override for the stored title.

    Returns:
        An :class:`IngestionResult`.  Failures are reported through ``warnings``
        and empty event lists — the pipeline never raises.
    """
    result = IngestionResult()
    doc_type = (doc_type or "DDR").strip().upper()

    # ---- Stage 1: OCR / text acquisition -------------------------------- #
    extracted_text = text or ""
    if extracted_text.strip():
        result.stage(
            "ocr",
            "SKIPPED",
            "No OCR engine needed; the payload already carried an embedded text layer.",
            simulated=True,
        )
    elif payload is not None and filename.lower().endswith(".pdf"):
        pdf_text, detail = extract_pdf_text(payload)
        if pdf_text.strip():
            extracted_text = pdf_text
            result.stage("ocr", "OK", detail, simulated=False)
        else:
            result.warnings.append(f"PDF text extraction produced no text ({detail}).")
            backend = PaddleOcrBackend()
            if backend.is_available() and ocr_engine.upper() not in ("", "NONE"):
                try:
                    extracted_text, detail = backend.recognise(payload, filename)
                    result.stage("ocr", "OK", detail, simulated=False)
                except Exception as exc:  # noqa: BLE001 - OCR must not crash ingest
                    result.warnings.append(f"OCR failed: {exc}")
                    result.stage("ocr", "FAILED", f"OCR failed: {exc}", simulated=False)
            else:
                result.stage(
                    "ocr",
                    "SKIPPED",
                    "No OCR engine installed for this PDF; using the embedded text layer "
                    "(which was empty for this file). Extracted events will be empty.",
                    simulated=True,
                )
    elif payload is not None:
        decoded = TextLayerBackend().recognise(payload, filename)[0]
        extracted_text = decoded
        result.stage(
            "ocr",
            "OK",
            f"Decoded the {len(payload)} byte upload as text using the embedded text layer.",
            simulated=False,
        )
    else:
        result.stage(
            "ocr",
            "SKIPPED",
            "No text and no file payload supplied; nothing to extract.",
            simulated=True,
        )
        result.warnings.append("Ingest request contained neither a text field nor a file.")

    result.text = extracted_text
    result.sections = split_sections(extracted_text) if extracted_text else []
    result.stage(
        "layout",
        "OK" if extracted_text else "SKIPPED",
        f"Split the text layer into {len(result.sections)} block(s).",
        simulated=False,
    )
    result.stage(
        "sections",
        "OK" if result.sections else "SKIPPED",
        f"Identified {len(result.sections)} section heading(s). Page numbers are not "
        f"available from a flat text layer, so every section reports page: null.",
        simulated=False,
    )

    # ---- Stage 2: entities ---------------------------------------------- #
    candidates = parse_report_text(extracted_text) if extracted_text else []
    detected_types = sorted({c["event_type"] for c in candidates})
    result.stage(
        "entities",
        "OK" if candidates else "SKIPPED",
        (
            f"Recognised {len(candidates)} candidate event(s) "
            f"({', '.join(detected_types) if detected_types else 'no event vocabulary found'}) "
            f"with depths and severities."
        )
        if candidates
        else "No event type vocabulary or depth found in the text; no entities extracted.",
        simulated=False,
    )

    # ---- Stage 3: events ------------------------------------------------ #
    result.stage(
        "events",
        "OK" if candidates else "SKIPPED",
        f"Assembled {len(candidates)} candidate event record(s)."
        if candidates
        else "No candidate events to assemble; extracted_events is empty.",
        simulated=False,
    )

    # ---- Resolve the target well ---------------------------------------- #
    resolved_well_id, well_warnings = _ingest_well_id(
        session, extracted_text, filename, well_id
    )
    result.warnings.extend(well_warnings)
    well = session.get(Well, resolved_well_id) if resolved_well_id else None

    # ---- Persist the document ------------------------------------------- #
    document = Document(
        id=next_document_id(session),
        well_id=well.id if well else None,
        doc_type=doc_type,
        title=title or filename or f"Operator-supplied {doc_type}",
        filename=filename or "upload",
        doc_date=doc_date or _default_doc_date(),
        source_system="OPERATOR_UPLOAD",
        page_count=0,
        ocr_engine=(ocr_engine or "NONE").upper(),
        extraction_method="RULE_PARSER",
        is_simulated=False,
        excerpt=(candidates[0]["description"] if candidates else extracted_text[:2000]),
        sections=result.sections,
        full_text=extracted_text,
    )
    session.add(document)
    session.flush()

    # ---- Stage 4: depth normalisation + formation mapping --------------- #
    trajectory = trajectory_from_dicts(well.trajectory) if well else []
    normalised: list[dict[str, Any]] = []
    for candidate in candidates:
        md = candidate["md"]
        tvd = candidate["tvd"]
        if tvd is None and md is not None:
            if trajectory:
                tvd = tvd_at_md(trajectory, md)
            elif well:
                tvd = min(md, well.current_tvd or md)
            else:
                tvd = md
                result.warnings.append(
                    "No well trajectory available; the reported MD was used as TVD for this event."
                )
        if md is None and tvd is not None:
            md = tvd if not trajectory else _md_at_tvd(trajectory, tvd)
        if md is None or tvd is None:
            continue
        normalised.append({**candidate, "md": round(float(md), 1), "tvd": round(float(tvd), 1)})
    result.stage(
        "depth_normalization",
        "OK" if normalised else "SKIPPED",
        (
            f"Normalised {len(normalised)} depth reading(s) to MD/TVD using the "
            f"{'target well trajectory' if trajectory else 'reported values (no trajectory available)'}."
        )
        if normalised
        else "No depth readings to normalise.",
        simulated=False,
    )

    formation_hits: list[str] = []
    for candidate in normalised:
        formation: Formation = formation_at_tvd(session, candidate["tvd"])
        candidate["formation_id"] = formation.id if formation else None
        candidate["formation_name"] = formation.name if formation else None
        if formation is not None:
            formation_hits.append(formation.name)
    result.stage(
        "formation_mapping",
        "OK" if formation_hits else "SKIPPED",
        (
            f"Mapped depths to formations by TVD range lookup: "
            f"{', '.join(sorted(set(formation_hits)))}."
        )
        if formation_hits
        else "No formation could be mapped by range lookup for the extracted depths.",
        simulated=False,
    )

    if not normalised and extracted_text:
        result.warnings.append(
            "No event could be extracted with both a recognised event type and a depth. "
            "The document was stored so the text is still browsable."
        )

    # ---- Persist the extracted events + evidence ------------------------ #
    if normalised and well is None:
        result.warnings.append(
            f"Extracted {len(normalised)} event(s) from an unattached document; they were "
            f"not persisted as events because an event must belong to a well. Attach the "
            f"document to a well and re-ingest to create them."
        )
        normalised = []

    for index, candidate in enumerate(normalised, start=1):
        event = DrillingEvent(
            id=f"EVX-{document.id[4:]}-{index:02d}",
            well_id=well.id,
            document_id=document.id,
            formation_id=candidate["formation_id"],
            event_type=candidate["event_type"],
            event_subtype=candidate["section"][:128],
            md=candidate["md"],
            tvd=candidate["tvd"],
            severity=candidate["severity"],
            severity_score=SEVERITY_SCORE.get(candidate["severity"], 0.5),
            occurred_at=datetime.combine(doc_date or document.doc_date, datetime.min.time()),
            day_number=0,
            description=candidate["description"],
            mitigation=candidate["mitigation"],
            status="OPEN",
            days_open=0,
            is_simulated=False,
        )
        session.add(event)
        session.flush()
        # Evidence carries the exact stored excerpt; the page is unknown for a
        # flat text layer, so it stays null rather than being invented.
        evidence = Evidence(
                id=f"EVX-{document.id[4:]}-{index:02d}",
                event_id=event.id,
                document_id=document.id,
                page=None,
                section=candidate["section"][:128],
                text_span=candidate["description"],
                confidence=0.6,
                bbox=None,
                extraction_method="RULE_PARSER",
                is_simulated=False,
            )
        session.add(evidence)
        # Append explicitly: the freshly-created event has not been re-fetched, so
        # the lazy collection would otherwise still be empty on this instance.
        event.evidence.append(evidence)
        session.flush()
        result.events.append(event)

    result.document = document
    return result


def _md_at_tvd(trajectory: Sequence[Any], tvd: float) -> float | None:
    """Invert the trajectory: return the MD at which the well reaches ``tvd``."""
    for lower, upper in zip(trajectory, trajectory[1:]):
        if lower.tvd <= tvd <= upper.tvd:
            span = upper.tvd - lower.tvd
            if span <= 1e-9:
                return lower.md
            return round(lower.md + (tvd - lower.tvd) / span * (upper.md - lower.md), 1)
    if trajectory and tvd >= trajectory[-1].tvd:
        return trajectory[-1].md
    if trajectory:
        return trajectory[0].md
    return None

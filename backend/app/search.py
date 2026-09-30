"""Hybrid search: NL intent parsing + structured retrieval + template synthesis.

The API deliberately answers from *records*, not from prose.  The pipeline is:

1. **Intent parsing** (:func:`parse_intent`) — pull event types, formation names,
   a TVD/MD anchor, a radius and an intent class out of the question using the
   :mod:`app.event_types` vocabulary.  Every extracted element is reported in
   ``parsed_intent.explain`` so the UI can show why the answer looks like it does.
2. **Structured retrieval** — events, wells, documents, mitigations, evidence and
   alerts are queried with the parsed filters.
3. **Lexical fallback** — when the intent carries no usable filter, a small
   TF-IDF-ish scorer over event and document text ranks the same records, and
   ``parsed_intent.explain`` says so explicitly.
4. **Synthesis** — a template assembled *only* from the retrieved records, with
   citations.  An LLM may replace the text but never the structure or the
   citations (see :mod:`app.llm`).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .event_types import SORTED_VOCABULARY, VOCABULARY_INDEX
from .geo import haversine_km
from .llm import synthesize
from .models import (
    Alert,
    Document,
    DrillingEvent,
    Evidence,
    OffsetRelation,
    Well,
)

__all__ = [
    "LexicalIndex",
    "ParsedIntent",
    "build_answer",
    "parse_intent",
    "run_search",
]

#: Intent classes the parser can emit.
INTENTS: tuple[str, ...] = (
    "HAZARD_QUERY",
    "MITIGATION_QUERY",
    "DOCUMENT_QUERY",
    "WELL_QUERY",
    "EVIDENCE_QUERY",
    "GENERAL",
)

MITIGATION_MARKERS = (
    "mitigation",
    "mitigate",
    "what did they do",
    "what was done",
    "how did they",
    "how did we",
    "how to",
    "remedial",
    "remedy",
    "avoid",
    "prevent",
    "best practice",
    "lesson",
    "response",
)
DOCUMENT_MARKERS = ("document", "report", "ddr", "wcr", "pdf", "page", "excerpt", "log")
WELL_MARKERS = ("well", "wells", "offset", "nearby", "similar", "neighbour", "neighbor", "adjacent")
EVIDENCE_MARKERS = ("evidence", "proof", "cite", "citation", "source", "excerpt")
#: Deictic phrases that mean "relative to where the rig is now".  They only set
#: ``near_current_well`` when a current well was actually supplied, so the flag
#: can never claim proximity to a well the caller did not name.
DEICTIC_MARKERS = (
    "nearby", "near me", "around here", "here", "this well", "current well",
    "at this depth", "at our depth", "we are at", "where we are", "right now",
    "now", "ahead of us", "before we drill", "before drilling", "coming up",
)
DEPTH_MARKERS = ("tvd", "md", "depth", "metres", "meters", "depths")

# The thousands-grouped alternative must come first: ``\d{1,3}`` alone would
# match only "150" of "1500".
NUMBER_RE = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(m\b|metres?\b|meters?\b|md\b|tvd\b)?",
    re.I,
)
RADIUS_RE = re.compile(r"within\s+(\d+(?:\.\d+)?)\s*km", re.I)
RADIUS_ALT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*km\s*(?:radius|away)", re.I)


@dataclass(slots=True)
class ParsedIntent:
    """The structured reading of a natural-language query."""

    event_type: list[str] = field(default_factory=list)
    formations: list[str] = field(default_factory=list)
    tvd_anchor_m: float | None = None
    md_anchor_m: float | None = None
    depth_window_m: float = 150.0
    radius_km: float | None = None
    near_current_well: bool = False
    intent: str = "GENERAL"
    keywords: list[str] = field(default_factory=list)
    explain: str = ""

    def is_empty(self) -> bool:
        """True when nothing actionable was extracted, forcing lexical fallback."""
        return not (
            self.event_type
            or self.formations
            or self.tvd_anchor_m is not None
            or self.md_anchor_m is not None
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API."""
        return {
            "event_type": self.event_type,
            "formations": self.formations,
            "tvd_anchor_m": self.tvd_anchor_m,
            "md_anchor_m": self.md_anchor_m,
            "depth_window_m": self.depth_window_m,
            "radius_km": self.radius_km,
            "near_current_well": self.near_current_well,
            "intent": self.intent,
            "keywords": self.keywords,
            "explain": self.explain,
        }


def _formation_names(session: Session) -> list[str]:
    """Return every known formation name (from the DB, not hard-coded)."""
    from .models import Formation

    return [row[0] for row in session.execute(select(Formation.name).order_by(Formation.name))]


def parse_intent(
    session: Session,
    query: str,
    default_radius_km: float = 8.0,
    current_well_id: str | None = None,
) -> ParsedIntent:
    """Parse a natural-language query into a :class:`ParsedIntent`.

    The parser is deliberately rule-based and fully explainable: it reports which
    vocabulary matched and which number became the depth anchor.  It never calls a
    model, so its output is stable and auditable.

    Args:
        session: Used to read formation names (range lookup, not a literal list).
        query: The raw question.
        default_radius_km: Radius used when the query does not state one.

    Returns:
        The parsed intent, including a human-readable ``explain`` string.
    """
    text = (query or "").strip()
    lowered = text.lower()
    intent = ParsedIntent()

    # 1. Event-type vocabulary — longest terms first so "lost circulation" wins
    #    over "loss".
    matched_terms: list[str] = []
    for term in SORTED_VOCABULARY:
        if term in lowered:
            matched_terms.append(term)
            for code in VOCABULARY_INDEX[term]:
                if code not in intent.event_type:
                    intent.event_type.append(code)

    # 2. Formation names.
    for name in _formation_names(session):
        if re.search(rf"\b{re.escape(name.lower())}\b", lowered):
            intent.formations.append(name)

    # 3. Depth anchor: the number that is nearest a depth marker.
    anchor_value, anchor_is_tvd, anchor_text = _parse_depth_anchor(text, lowered)
    if anchor_value is not None:
        if anchor_is_tvd:
            intent.tvd_anchor_m = anchor_value
        else:
            intent.md_anchor_m = anchor_value

    # 4. "around X m" widens the depth window to cover the cluster.
    if re.search(r"\b(around|near|about|approximately|at)\s+\d", lowered):
        intent.depth_window_m = 150.0
    if anchor_text:
        intent.depth_window_m = max(intent.depth_window_m, 100.0)

    # 5. Radius.
    radius_match = RADIUS_RE.search(lowered) or RADIUS_ALT_RE.search(lowered)
    if radius_match:
        try:
            intent.radius_km = float(radius_match.group(1))
        except ValueError:
            intent.radius_km = default_radius_km
    else:
        intent.radius_km = default_radius_km

    # 6. Spatial / mitigation intent.  ``near_current_well`` is only claimed when a
    #    current well was supplied *and* the query is phrased relative to it.
    explicit_offset = any(
        marker in lowered
        for marker in ("nearby", "near me", "offset", "adjacent", "similar well",
                        "neighbour", "neighbor")
    )
    deictic = any(marker in lowered for marker in DEICTIC_MARKERS)
    # A bare depth anchor ("around 1500 m TVD") with a current well supplied is the
    # prototype's core question — "at the depth we are at now" — so it is deictic
    # too.  A stated radius ("within 12 km") is a different, explicit scope.
    depth_anchor = intent.tvd_anchor_m is not None or intent.md_anchor_m is not None
    explicit_radius = radius_match is not None
    if depth_anchor and not explicit_radius and current_well_id:
        deictic = True
    intent.near_current_well = bool(
        explicit_offset or (current_well_id and deictic)
    )

    if any(marker in lowered for marker in MITIGATION_MARKERS):
        intent.intent = "MITIGATION_QUERY"
    elif any(marker in lowered for marker in DOCUMENT_MARKERS):
        intent.intent = "DOCUMENT_QUERY"
    elif any(marker in lowered for marker in EVIDENCE_MARKERS):
        intent.intent = "EVIDENCE_QUERY"
    elif intent.event_type:
        intent.intent = "HAZARD_QUERY"
    elif any(marker in lowered for marker in WELL_MARKERS):
        intent.intent = "WELL_QUERY"
    else:
        intent.intent = "GENERAL"

    # 7. Leftover keywords for the lexical fallback.
    stop = {
        "what", "happened", "around", "near", "about", "did", "they", "there",
        "was", "were", "the", "and", "for", "with", "this", "that", "any", "have",
        "has", "how", "m", "tvd", "md", "metres", "meters", "depth", "in", "of",
        "to", "at", "me", "us", "a", "an", "is", "are", "be", "we", "i",
    }
    tokens = [t for t in re.findall(r"[a-z][a-z\-]{2,}", lowered) if t not in stop]
    intent.keywords = sorted(set(tokens))[:12]

    intent.explain = _explain(intent, matched_terms, anchor_text, anchor_is_tvd)
    return intent


def _parse_depth_anchor(text: str, lowered: str) -> tuple[float | None, bool, str]:
    """Return ``(value, is_tvd, matched_text)`` for the query's depth anchor.

    A number immediately followed by ``tvd`` is a TVD anchor; a number followed by
    ``md`` is an MD anchor; a bare number near a depth word defaults to TVD
    because TVD is what the risk engine reasons about.
    """
    best: tuple[float, bool, str] | None = None
    best_score = -1.0
    for match in NUMBER_RE.finditer(text):
        raw = match.group(1).replace(",", "")
        unit = (match.group(2) or "").lower()
        try:
            value = float(raw)
        except ValueError:
            continue
        if value <= 0 or value > 20000:
            continue
        window = lowered[max(0, match.start() - 30) : match.end() + 30]
        is_tvd = unit.startswith("tvd")
        is_md = unit.startswith("md")
        score = 0.0
        if is_tvd:
            score += 3.0
        elif is_md:
            score += 3.0
        if "tvd" in window:
            score += 1.0
            is_tvd = True
        if " md" in window or window.endswith(" md"):
            score += 0.5
            is_md = True
        if any(marker in window for marker in DEPTH_MARKERS):
            score += 1.0
        if score > best_score:
            best_score = score
            best = (value, is_tvd and not is_md, match.group(0).strip())
    if best is None or best_score < 1.0:
        return None, True, ""
    return best


def _explain(
    intent: ParsedIntent,
    matched_terms: Sequence[str],
    anchor_text: str,
    anchor_is_tvd: bool,
) -> str:
    """Build the human-readable explanation of what the parser extracted."""
    parts: list[str] = []
    if matched_terms:
        codes = ", ".join(intent.event_type)
        parts.append(f"Matched '{matched_terms[0]}' event vocabulary → {codes}.")
    if intent.formations:
        parts.append(f"Recognised formation name(s): {', '.join(intent.formations)}.")
    if anchor_text:
        kind = "TVD" if anchor_is_tvd else "MD"
        parts.append(f"Parsed '{anchor_text}' as a {kind} anchor (±{intent.depth_window_m:.0f} m).")
    if intent.radius_km is not None and parts:
        parts.append(f"Search radius {intent.radius_km:g} km.")
    if intent.near_current_well:
        parts.append(
            "Query is phrased relative to the current well, so nearby-well "
            "relevance is applied."
        )
    if intent.intent == "MITIGATION_QUERY":
        parts.append("Detected a mitigation intent: mitigations will be prioritised.")
    if not parts:
        parts.append(
            "No event type, formation or depth could be parsed; ranked by keyword "
            "overlap (TF-IDF-ish lexical fallback) over events and documents."
        )
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Lexical fallback
# --------------------------------------------------------------------------- #


class LexicalIndex:
    """A small TF-IDF scorer over event and document text.

    Deliberately dependency-free: it exists so a query with no parseable intent
    still returns ranked, explainable hits instead of an empty list.
    """

    def __init__(self, documents: Sequence[tuple[str, str]]) -> None:
        """Build the index from ``(id, text)`` pairs."""
        self.ids = [doc_id for doc_id, _ in documents]
        self.token_counts = [Counter(_tokenize(text)) for _, text in documents]
        lengths = [sum(counts.values()) or 1 for counts in self.token_counts]
        total = len(self.token_counts) or 1
        self.idf: dict[str, float] = {}
        for counts in self.token_counts:
            for term in counts:
                self.idf[term] = self.idf.get(term, 0.0) + 1
        self.idf = {
            term: math.log(total / (1 + df)) + 1.0 for term, df in self.idf.items()
        }
        self.lengths = lengths
        self.avg_length = sum(lengths) / total

    def score(self, terms: Sequence[str]) -> list[tuple[str, float]]:
        """Return ``[(id, score)]`` sorted by descending score."""
        if not terms:
            return []
        results: list[tuple[str, float]] = []
        for index, counts in enumerate(self.token_counts):
            score = 0.0
            for term in terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                normalised = tf / (self.lengths[index] / self.avg_length or 1.0)
                score += normalised * self.idf.get(term, 0.0)
            if score > 0:
                results.append((self.ids[index], score))
        results.sort(key=lambda pair: (-pair[1], pair[0]))
        return results

    def highlight_terms(self, terms: Sequence[str], text: str, limit: int = 3) -> list[str]:
        """Return the query terms actually present in ``text``."""
        tokens = set(_tokenize(text))
        return [term for term in terms if term in tokens][:limit]


def _tokenize(text: str) -> list[str]:
    """Lower-case word tokens of at least three characters."""
    return [t for t in re.findall(r"[a-z0-9][a-z0-9\-_]{2,}", (text or "").lower())]


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #


def _event_rows(
    session: Session,
    current_well: Well | None,
    intent: ParsedIntent,
    filters: dict[str, Any],
    limit: int,
) -> list[DrillingEvent]:
    """Retrieve events matching the parsed intent and explicit filters."""
    stmt = select(DrillingEvent)
    if filters.get("event_type"):
        stmt = stmt.where(DrillingEvent.event_type == filters["event_type"])
    elif intent.event_type:
        stmt = stmt.where(DrillingEvent.event_type.in_(intent.event_type))
    if filters.get("well_id"):
        stmt = stmt.where(DrillingEvent.well_id == filters["well_id"])
    if filters.get("formation"):
        from .models import Formation

        stmt = stmt.join(Formation, DrillingEvent.formation_id == Formation.id).where(
            Formation.name == filters["formation"]
        )
    elif intent.formations:
        from .models import Formation

        stmt = stmt.join(Formation, DrillingEvent.formation_id == Formation.id).where(
            Formation.name.in_(intent.formations)
        )
    tvd_min = filters.get("tvd_min")
    tvd_max = filters.get("tvd_max")
    if tvd_min is None and intent.tvd_anchor_m is not None:
        tvd_min = intent.tvd_anchor_m - intent.depth_window_m
    if tvd_max is None and intent.tvd_anchor_m is not None:
        tvd_max = intent.tvd_anchor_m + intent.depth_window_m
    if tvd_min is not None:
        stmt = stmt.where(DrillingEvent.tvd >= float(tvd_min))
    if tvd_max is not None:
        stmt = stmt.where(DrillingEvent.tvd <= float(tvd_max))
    if intent.md_anchor_m is not None and tvd_min is None and tvd_max is None:
        stmt = stmt.where(
            DrillingEvent.md >= intent.md_anchor_m - intent.depth_window_m,
            DrillingEvent.md <= intent.md_anchor_m + intent.depth_window_m,
        )
    if filters.get("severity_min"):
        stmt = stmt.where(DrillingEvent.severity_score >= float(filters["severity_min"]))
    stmt = stmt.order_by(
        DrillingEvent.tvd.asc() if tvd_min is not None else DrillingEvent.occurred_at.desc()
    ).limit(limit)
    return list(session.scalars(stmt))


def _nearby_well_ids(
    session: Session,
    current_well: Well | None,
    intent: ParsedIntent,
    radius_km: float,
) -> set[str]:
    """Return the offset well ids within the effective radius."""
    if current_well is None:
        return set()
    ids: set[str] = set()
    for well in session.scalars(select(Well)):
        if well.id == current_well.id:
            continue
        distance = haversine_km(
            current_well.latitude, current_well.longitude, well.latitude, well.longitude
        )
        if distance <= radius_km:
            ids.add(well.id)
    return ids


def _document_rows(
    session: Session,
    current_well: Well | None,
    intent: ParsedIntent,
    filters: dict[str, Any],
    event_ids: Sequence[str],
    limit: int,
) -> list[Document]:
    """Retrieve documents backing the matched events, plus well-level reports."""
    docs: dict[str, Document] = {}
    if event_ids:
        stmt = (
            select(Document)
            .where(Document.id.in_([e.document_id for e in event_ids if e.document_id]))
            .limit(limit)
        )
        for document in session.scalars(stmt):
            docs[document.id] = document
    if filters.get("doc_type"):
        extra = select(Document).where(Document.doc_type == filters["doc_type"]).limit(limit)
    elif filters.get("well_id"):
        extra = select(Document).where(Document.well_id == filters["well_id"]).limit(limit)
    elif current_well is not None and intent.near_current_well:
        extra = (
            select(Document)
            .where(Document.well_id.in_(_nearby_well_ids(session, current_well, intent, intent.radius_km or 8.0)))
            .limit(limit)
        )
    else:
        extra = None
    if extra is not None:
        for document in session.scalars(extra):
            docs.setdefault(document.id, document)
    return list(docs.values())[:limit]


def _lexical_ranking(
    session: Session, intent: ParsedIntent, limit: int
) -> tuple[list[DrillingEvent], list[Document], float | None]:
    """Rank events and documents by keyword overlap when the intent is empty."""
    events = list(session.scalars(select(DrillingEvent).limit(400)))
    documents = list(session.scalars(select(Document).limit(400)))
    if not intent.keywords:
        return events[:limit], documents[:limit], None
    event_index = LexicalIndex(
        [
            (event.id, f"{event.description} {event.mitigation} {event.event_type}")
            for event in events
        ]
    )
    document_index = LexicalIndex(
        [
            (document.id, f"{document.title} {document.excerpt} {document.full_text}")
            for document in documents
        ]
    )
    ranked_events = event_index.score(intent.keywords)[:limit]
    ranked_documents = document_index.score(intent.keywords)[:limit]
    event_by_id = {event.id: event for event in events}
    document_by_id = {document.id: document for document in documents}
    top_event = event_by_id[ranked_events[0][0]] if ranked_events else None
    top_document = document_by_id[ranked_documents[0][0]] if ranked_documents else None
    return (
        [event_by_id[i] for i, _ in ranked_events if i in event_by_id],
        [document_by_id[i] for i, _ in ranked_documents if i in document_by_id],
        top_event.tvd if top_event else None,
    )


def run_search(
    session: Session,
    query: str,
    current_well_id: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 20,
    default_radius_km: float = 8.0,
) -> dict[str, Any]:
    """Run the full hybrid search pipeline.

    Args:
        session: Open SQLAlchemy session.
        query: Natural-language question.
        current_well_id: Anchor well for "nearby"/depth-relative reasoning.
        filters: Explicit structured filters that override parsed intent.
        limit: Maximum records per structured result bucket.
        default_radius_km: Radius used when the query states none.
        current_well_id: The anchor well for the query, if any.  Required before
            ``near_current_well`` can be set from a deictic phrase.

    Returns:
        A dict with ``query``, ``parsed_intent``, ``structured_results``,
        ``synthesis``, ``result_count`` and ``data_provenance``.
    """
    filters = dict(filters or {})
    if filters.get("event_type") and not isinstance(filters["event_type"], list):
        filters["event_type"] = str(filters["event_type"]).upper()
    current_well = session.get(Well, current_well_id) if current_well_id else None
    intent = parse_intent(
        session, query, default_radius_km=default_radius_km, current_well_id=current_well_id
    )
    radius_km = float(filters.get("radius_km") or intent.radius_km or default_radius_km)
    lexical = False

    if intent.is_empty() and not filters:
        events, documents, _ = _lexical_ranking(session, intent, limit)
        lexical = True
    else:
        events = _event_rows(session, current_well, intent, filters, limit)
        event_ids = [event.id for event in events]
        documents = _document_rows(
            session, current_well, intent, filters, events, limit
        )
        if not events and not documents:
            events, documents, _ = _lexical_ranking(session, intent, limit)
            lexical = True

    nearby_ids = _nearby_well_ids(session, current_well, intent, radius_km)
    offset_wells: list[OffsetRelation] = []
    if current_well is not None:
        stmt = select(OffsetRelation).where(OffsetRelation.current_well_id == current_well.id)
        if intent.event_type:
            stmt = stmt.where(
                OffsetRelation.offset_well_id.in_(
                    select(DrillingEvent.well_id).where(
                        DrillingEvent.event_type.in_(intent.event_type)
                    )
                )
            )
        for relation in session.scalars(stmt.order_by(OffsetRelation.relevance_score.desc())):
            if relation.offset_well_id in nearby_ids or not nearby_ids:
                offset_wells.append(relation)
            if len(offset_wells) >= limit:
                break

    event_ids = [event.id for event in events]
    evidence_rows: list[Evidence] = []
    if event_ids:
        evidence_rows = list(
            session.scalars(
                select(Evidence).where(Evidence.event_id.in_(event_ids)).limit(limit)
            )
        )
    mitigations = [
        {
            "text": event.mitigation,
            "provenance": "SOURCE_DOCUMENT",
            "event_id": event.id,
            "well_name": event.well.name if event.well else event.well_id,
            "document_ref": _document_ref(event.document),
        }
        for event in events
        if event.mitigation
    ][:limit]
    alerts: list[Alert] = []
    if current_well is not None:
        alert_stmt = select(Alert).where(Alert.current_well_id == current_well.id)
        if intent.event_type:
            alert_stmt = alert_stmt.where(Alert.event_type.in_(intent.event_type))
        alerts = list(
            session.scalars(
                alert_stmt.order_by(Alert.risk_score.desc()).limit(limit)
            )
        )

    if lexical:
        intent.explain += " Ranked by keyword overlap (TF-IDF-ish lexical fallback)."

    result = {
        "query": query,
        "current_well_id": current_well.id if current_well else current_well_id,
        "parsed_intent": intent.to_dict(),
        "retrieval": {
            "method": "STRUCTURED" if not lexical else "LEXICAL_FALLBACK",
            "radius_km": radius_km,
            "limit": limit,
        },
        "structured_results": {
            "events": events,
            "wells": offset_wells,
            "documents": documents,
            "mitigations": mitigations,
            "evidence": evidence_rows,
            "alerts": alerts,
        },
        "data_provenance": "REAL_PUBLIC_DATA",
    }
    result["synthesis"] = build_answer(session, result)
    total = (
        len(events)
        + len(offset_wells)
        + len(documents)
        + len(mitigations)
        + len(evidence_rows)
        + len(alerts)
    )
    result["result_count"] = total
    return result


def _document_ref(document: Document | None) -> str:
    """Return a short human reference for a document, honest about a missing page."""
    if document is None:
        return "no document reference in the record"
    page = next((e.page for e in (document.evidence or []) if e.page is not None), None)
    if page is None:
        return f"{document.doc_type} {document.id} (page not available)"
    return f"{document.doc_type} {document.id} p.{page}"


def build_answer(session: Session, result: dict[str, Any]) -> dict[str, Any]:
    """Assemble the answer block from the retrieved records.

    The template text is built strictly from the retrieved rows — event types,
    depths, well ids and counts that came back from the database.  If an LLM is
    configured, :func:`app.llm.synthesize` may replace the *text* only; the
    citations, provenance labelling and record set are unchanged.
    """
    structured = result["structured_results"]
    events = structured["events"]
    intent = result["parsed_intent"]
    alerts = structured["alerts"]
    offset_wells = structured["wells"]

    citations: list[dict[str, Any]] = []
    for event in events[:5]:
        citations.append(
            {
                "event_id": event.id,
                "document_id": event.document_id,
                "page": next(
                    (e.page for e in (event.evidence or []) if e.page is not None), None
                ),
                "label": f"{event.well_id if event.well_id else 'well'} {event.event_type} @ "
                         f"{event.tvd:,.0f} m TVD",
            }
        )

    if events:
        by_type: Counter[str] = Counter(event.event_type for event in events)
        wells = sorted({event.well_id for event in events})
        top = by_type.most_common(1)[0]
        depth_phrase = (
            f"around {intent['tvd_anchor_m']:,.0f} m TVD"
            if intent.get("tvd_anchor_m") is not None
            else f"between {min(e.tvd for e in events):,.0f} and "
                 f"{max(e.tvd for e in events):,.0f} m TVD"
        )
        sentences = [
            f"Retrieved {len(events)} record(s) {depth_phrase} across "
            f"{len(wells)} well(s): {', '.join(wells)}."
        ]
        sentences.append(
            f"The dominant finding is {top[0].replace('_', ' ').lower()} with "
            f"{top[1]} event(s)."
        )
        if alerts:
            sentences.append(
                f"{len(alerts)} active alert(s) are raised for the current well, the "
                f"highest being {alerts[0].severity_band} "
                f"(risk {alerts[0].risk_score:.2f}) for {alerts[0].event_type.replace('_', ' ').lower()}."
            )
        elif offset_wells:
            sentences.append(
                f"{len(offset_wells)} offset well(s) are inside the "
                f"{result['retrieval']['radius_km']:g} km radius."
            )
        text = " ".join(sentences)
    elif structured["documents"]:
        text = (
            f"Retrieved {len(structured['documents'])} document(s) matching the "
            f"query. No drilling event in the current dataset matches the parsed depth and "
            f"type filters, so the documents are the closest available evidence."
        )
    else:
        text = (
            "No record matched this query. Try naming an event type "
            "(for example stuck pipe or mud loss) or a depth such as 1500 m TVD."
        )

    records = [_record_digest(event) for event in events[:8]]
    synthesis = synthesize(
        query=result["query"],
        records=records,
        template_text=text,
        citations=citations,
    )
    payload = synthesis.to_dict()
    payload["detail"] = synthesis.detail
    return payload


def _record_digest(event: DrillingEvent) -> dict[str, Any]:
    """Flatten one event into the record shape handed to the LLM."""
    return {
        "event_id": event.id,
        "well_id": event.well_id,
        "event_type": event.event_type,
        "md": event.md,
        "tvd": event.tvd,
        "severity": event.severity,
        "description": event.description,
        "mitigation": event.mitigation,
        "document_id": event.document_id,
    }

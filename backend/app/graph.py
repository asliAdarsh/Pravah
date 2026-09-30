"""An in-memory property graph over the real drilling corpus.

The graph is derived — never authored. Every node and edge is rebuilt from rows
that are already in the database (or, for the two derived types, from verbatim
text that is already stored in an event description). Nothing is summarised,
inferred or generated: an ``Intervention`` node carries the exact characters
that appear in the DDR line it came from, and the event that line belongs to.

Node types
----------
``Well``            an NPD well header
``Formation``       an NPD lithostratigraphic unit
``Event``           an extracted DDR event
``Hazard``          an event-type class, with the real counts behind it
``Intervention``    a verbatim response clause taken from an event description
``Outcome``         a verbatim recorded-result clause from the same description
``ReportSnippet``   an :class:`~app.models.Evidence` row — the literal span

Edge types
----------
``DRILLED_THROUGH``     Well → Formation
``HAD_EVENT``           Well → Event
``FOLLOWED_BY``         Event → Event (same well, ascending MD)
``MITIGATED_BY``        Event → Intervention
``LED_TO``              Event → Outcome
``ANALOG_FOR_HAZARD``   Well → Well (from the ``offset_relations`` cache)
``EXTRACTED_FROM``      Event → ReportSnippet
``CLASSIFIED_AS``       Event → Hazard

Derivation honesty
------------------
The Volve DDRs do not use a labelled mitigation field: ``mitigation`` is empty
on all 1,658 real events because ``ingestion._split_mitigation`` looks for
``MITIGATION:`` / ``REMEDIAL ACTION:`` markers that this corpus never writes.
Rather than invent a response, :func:`response_clauses` first delegates to the
ingestion splitter and, when that finds nothing, falls back to splitting the
stored line into literal clauses. Every derived node therefore carries
``derived=True`` and the id of the event it was read from, and the UI can show
the source line. Clauses the vocabulary does not recognise are dropped rather
than guessed at.
"""

from __future__ import annotations

import hashlib
import heapq
import re
from collections import deque
from typing import Any, Iterable, Sequence

import networkx as nx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ingestion import _parse_severity, _split_mitigation
from .models import (
    Document,
    DrillingEvent,
    Evidence,
    Formation,
    OffsetRelation,
    Well,
)

__all__ = [
    "normalise_node_id",
    "ANALOG_MIN_RELEVANCE",
    "EDGE_TYPES",
    "GRAPH_SCHEMA_VERSION",
    "NODE_TYPES",
    "build_graph",
    "expand",
    "invalidate",
    "labels_for",
    "node_exists",
    "node_id",
    "path_between",
    "response_clauses",
    "split_node_id",
    "stats",
    "subgraph",
]

#: Bumped when the node/edge vocabulary changes so caches can be dropped.
GRAPH_SCHEMA_VERSION = "graph-v1"

#: Minimum cached relevance score for two wells to be linked by
#: ``ANALOG_FOR_HAZARD``. This is the relevance engine's own ``HIGH`` band on
#: the real corpus; below it the cache is close to uniform across all 119
#: wells and the link carries no information. :func:`stats` reports how many
#: cache rows that discarded.
ANALOG_MIN_RELEVANCE = 0.7

#: The seven node types the graph is allowed to contain.
NODE_TYPES: tuple[str, ...] = (
    "Well",
    "Formation",
    "Event",
    "Hazard",
    "Intervention",
    "Outcome",
    "ReportSnippet",
)

#: The edge types the graph is allowed to contain. ``CLASSIFIED_AS`` is the one
#: addition to the brief: without it the ``Hazard`` node type is unreachable
#: from any other node, so BFS, subgraph and path finding could never touch it.
EDGE_TYPES: tuple[str, ...] = (
    "DRILLED_THROUGH",
    "HAD_EVENT",
    "FOLLOWED_BY",
    "MITIGATED_BY",
    "LED_TO",
    "ANALOG_FOR_HAZARD",
    "EXTRACTED_FROM",
    "CLASSIFIED_AS",
)

# --------------------------------------------------------------------------- #
# Derived-node vocabulary
# --------------------------------------------------------------------------- #

#: The DDR activity line opens with the time range it covers; it is not part of
#: the reported action, so it is stripped before the line is split.
_ROLE_PREFIX_RE = re.compile(r"^\s*\d{1,2}[:.]\d{2}\s*[-\u2013]\s*\d{1,2}[:.]\d{2}\s*:\s*")

#: Clause boundary used by the real logs: full stops and semicolons.
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.;])\s+")

#: Verbs an operator writes when they acted on the hazard. A clause matching
#: this is read as the recorded response, not as the recorded result.
_ACTION_RE = re.compile(
    r"\b("
    r"worked|working|jarred|jarring|jarred\s+on|freed|freeing|released|releasing|"
    r"circulat\w*|pooh|poob|rop|rih|rod|pulled|pulling|backed\s+off|"
    r"cut|cutting|reamed|reaming|milled|milling|drilled|drilling|"
    r"re-?align\w*|installed|replaced|replac\w*|removed|cleaned|cleaning|"
    r"displaced|bullhead\w*|killed|waited|reversed|slipped|set\s+back|"
    r"topped\s+up|changed\s+out|checked|flow-?checked|monitored|observed|"
    r"applied|used|added|treat\w*|secured|stopped|started|resumed|"
    r"logged\s+off|rigged|adjusted|calibrat\w*|tension\w*|"
    r"confirmed|verified|checked\s+and\s+re-?circulat\w*|"
    r"w/\s*w|re-?entered|continued|held|performed|repaired|troubleshoot\w*"
    r")\b",
    re.I,
)

#: Markers of a recorded *result* rather than an action. A clause that matches
#: neither an action nor a result is not turned into a node.
_RESULT_RE = re.compile(
    r"\b("
    r"no\s+progress|no\s+further|no\s+additional|nothing|none|"
    r"still|remained|continued\s+to|"
    r"free|released|normal|stable|static|balanced|decreas\w*|increas\w*|"
    r"observed|stopped|stalled|succeeded|success\w*|recovered|resumed|"
    r"unchanged|held|passed|broke\s+out|gained|cleared|closed|"
    r"fired|done|finished|ok\b|good|settled|logged"
    r")\b",
    re.I,
)

#: A recorded quantity is itself a result: "loss rate less than 0,1 m3/hour" is
#: what the operator measured, not what they did.
_MEASUREMENT_RE = re.compile(
    r"(\d+[.,]?\d*\s*(m3|m3/hr|m3/hour|%|bar|min|mm|kg|kkgf|mmd|m/hr|m/h|m\b)"
    r"|less\s+than|approx\w*|rate|at\s+same\s+depth)",
    re.I,
)

#: Shortest clause worth turning into a node. Shorter spans are fragments of a
#: number, not a reportable action.
_MIN_CLAUSE_CHARS = 8


def _short_hash(text: str) -> str:
    """Stable short digest used to de-duplicate identical real clauses."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def response_clauses(description: str) -> tuple[list[str], list[str], str]:
    """Split one stored event description into recorded actions and results.

    The DDR mitigation-field splitter from :mod:`app.ingestion` is tried first,
    so a report that *does* carry a ``MITIGATION:`` clause is read exactly the
    way ingestion would read it. Only when it finds nothing — which is the case
    for the whole Volve corpus — are the literal clauses of the line used.

    Args:
        description: The verbatim ``drilling_events.description`` text.

    Returns:
        ``(actions, results, basis)`` where ``basis`` names which of the two
        readings produced them. Every returned string is a verbatim substring
        of ``description`` (the leading clock range is stripped).
    """
    text = (description or "").strip()
    if not text:
        return [], [], "NONE"

    _, mitigation = _split_mitigation(text)
    if mitigation:
        body = _ROLE_PREFIX_RE.sub("", mitigation).strip()
        parts = [p.strip(" .;-") for p in _CLAUSE_SPLIT_RE.split(body) if p.strip(" .;-")]
        actions = [p for p in parts if len(p) >= _MIN_CLAUSE_CHARS]
        return actions, [], "MITIGATION_FIELD"

    body = _ROLE_PREFIX_RE.sub("", text).strip()
    parts = [p.strip(" .;-") for p in _CLAUSE_SPLIT_RE.split(body) if p.strip(" .;-")]
    actions: list[str] = []
    results: list[str] = []
    for part in parts:
        if len(part) < _MIN_CLAUSE_CHARS:
            continue
        if _ACTION_RE.search(part):
            actions.append(part)
        elif _RESULT_RE.search(part) or _MEASUREMENT_RE.search(part):
            results.append(part)
    return actions, results, "INLINE_CLAUSE"


#: Sorts after any real ``(hops, seed rank)`` pair, so an unseen node is always
#: improved on the first time the search reaches it.
_UNREACHED = 1 << 30

# --------------------------------------------------------------------------- #
# Node ids
# --------------------------------------------------------------------------- #


#: Canonical wire prefix for each declared node type. Ids are uppercase on the
#: wire (``WELL:15/9-F-9A``) so they read as constants in a URL or a log line;
#: the node payload still carries the declared camel-case ``type``.
_ID_PREFIX: dict[str, str] = {
    "Well": "WELL",
    "Formation": "FORMATION",
    "Event": "EVENT",
    "Hazard": "HAZARD",
    "Intervention": "INTERVENTION",
    "Outcome": "OUTCOME",
    "ReportSnippet": "SNIPPET",
}
_TYPE_BY_PREFIX: dict[str, str] = {v: k for k, v in _ID_PREFIX.items()}


def node_id(kind: str, key: str) -> str:
    """Return the canonical ``PREFIX:key`` identifier for a graph node."""
    return f"{_ID_PREFIX[kind]}:{key}"


def normalise_node_id(value: str) -> str:
    """Return the canonical form of a node id, accepting any letter case.

    Raises:
        ValueError: when the id carries no kind prefix or an unknown kind.
    """
    prefix, _, key = (value or "").partition(":")
    kind = _TYPE_BY_PREFIX.get(prefix.upper())
    if not key or kind is None:
        raise ValueError(f"Unknown graph node id: {value!r}")
    return node_id(kind, key)


def split_node_id(value: str) -> tuple[str, str]:
    """Split a node id into its declared type name and key.

    Raises:
        ValueError: when the id carries no kind prefix or an unknown kind.
    """
    canonical = normalise_node_id(value)
    prefix, _, key = canonical.partition(":")
    return _TYPE_BY_PREFIX[prefix], key


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

#: ``(engine_url, schema_version) -> (fingerprint, graph)``.
_CACHE: dict[tuple[str, str, Any], nx.DiGraph] = {}


def invalidate() -> None:
    """Drop every cached graph — call this after writing to the corpus."""
    _CACHE.clear()


def _fingerprint(session: Session) -> tuple[int, int, int, int]:
    """Cheap corpus signature: four counts that change on any real write."""
    return (
        session.scalar(select(func.count()).select_from(Well)) or 0,
        session.scalar(select(func.count()).select_from(DrillingEvent)) or 0,
        session.scalar(select(func.count()).select_from(Evidence)) or 0,
        session.scalar(select(func.count()).select_from(OffsetRelation)) or 0,
    )


def _cache_key(session: Session) -> tuple[str, str, Any]:
    bind = session.get_bind()
    url = str(getattr(bind, "url", bind))
    return (url, GRAPH_SCHEMA_VERSION, _fingerprint(session))


def _well_node(row: Well) -> dict[str, Any]:
    return {
        "type": "Well",
        "label": row.name,
        "well_id": row.id,
        "field": row.field,
        "block": row.block,
        "operator": row.operator,
        "well_type": row.well_type,
        "status": row.status,
        "water_depth_m": row.water_depth_m,
        "total_depth_md": row.total_depth_md,
        "current_depth_md": row.current_depth_md,
        "is_active": bool(row.is_active),
        "is_simulated": bool(row.is_simulated),
        "derived": False,
    }


def _formation_node(row: Formation) -> dict[str, Any]:
    return {
        "type": "Formation",
        "label": row.name,
        "formation_code": row.code,
        "top_depth": row.top_depth,
        "bottom_depth": row.bottom_depth,
        "lithology": row.lithology,
        "age": row.age,
        "is_simulated": bool(row.is_simulated),
        "derived": False,
    }


def _event_node(row: DrillingEvent, document: Document | None) -> dict[str, Any]:
    return {
        "type": "Event",
        "label": f"{row.event_type} @ {row.md:g} m MD",
        "event_id": row.id,
        "well_id": row.well_id,
        "document_id": row.document_id,
        "document_title": document.title if document is not None else None,
        "event_type": row.event_type,
        "md": row.md,
        "tvd": row.tvd,
        "severity": row.severity,
        "severity_score": row.severity_score,
        "status": row.status,
        "days_open": row.days_open,
        "day_number": row.day_number,
        "description": row.description,
        "mitigation": row.mitigation,
        "formation_code": row.formation.code if row.formation is not None else None,
        "is_simulated": bool(row.is_simulated),
        "derived": False,
    }


def _snippet_node(row: Evidence) -> dict[str, Any]:
    return {
        "type": "ReportSnippet",
        "label": f"snippet {row.id}",
        "evidence_id": row.id,
        "event_id": row.event_id,
        "document_id": row.document_id,
        "page": row.page,
        "section": row.section,
        "text": row.text_span,
        "confidence": row.confidence,
        "extraction_method": row.extraction_method,
        "is_simulated": bool(row.is_simulated),
        "derived": False,
    }


def _add_derived_clauses(
    graph: nx.DiGraph,
    pending: list[tuple[str, str, str, dict[str, Any]]],
    event_node: str,
    event: DrillingEvent,
    kind: str,
    edge_type: str,
    clauses: Sequence[str],
    seen: set[str],
    basis: str,
) -> None:
    """Add one node per distinct real clause, plus the link to its event.

    Identical clauses collapse into a single node, so the graph records an
    action once with the number of events that recorded it rather than
    repeating the same sentence hundreds of times. The text is never rewritten,
    and the first event that wrote it is kept as the cited source.
    """
    for clause in clauses:
        key = _short_hash(clause.lower())
        target = node_id(kind, key)
        if key not in seen:
            seen.add(key)
            attributes = {
                "type": kind,
                "label": clause,
                "text": clause,
                "source_event_id": event.id,
                "source_well_id": event.well_id,
                "basis": basis,
                "cited_by": 0,
                "derived": True,
            }
            if kind == "Outcome":
                attributes["severity_from_text"] = _parse_severity(event.description)
            graph.add_node(target, **attributes)
        graph.nodes[target]["cited_by"] += 1
        pending.append((event_node, target, edge_type, {"basis": basis}))


def _assemble(session: Session) -> nx.DiGraph:
    """Read the corpus once and build the property graph."""
    graph = nx.DiGraph()

    for row in session.scalars(select(Formation).order_by(Formation.code)):
        graph.add_node(node_id("Formation", row.code), **_formation_node(row))

    for row in session.scalars(select(Well).order_by(Well.id)):
        graph.add_node(node_id("Well", row.id), **_well_node(row))

    # DRILLED_THROUGH: the NPD unit the well finished in, plus every unit one
    # of its real events was attributed to by the seeder's depth lookup.
    code_by_id: dict[str, str] = {}
    drilled: dict[str, set[str]] = {}
    for row in session.scalars(select(Formation)):
        code_by_id[str(row.id)] = row.code
    for row in session.scalars(select(Well).order_by(Well.id)):
        if row.formation is not None:
            drilled.setdefault(row.id, set()).add(row.formation.code)
    attributed: dict[str, set[str]] = {}
    for well_id, formation_id in session.execute(
        select(DrillingEvent.well_id, DrillingEvent.formation_id).distinct()
    ):
        code = code_by_id.get(str(formation_id)) if formation_id is not None else None
        if code:
            attributed.setdefault(well_id, set()).add(code)
    for well_id, codes in attributed.items():
        drilled.setdefault(well_id, set()).update(codes)
    for well_id, codes in drilled.items():
        for code in codes:
            graph.add_edge(
                node_id("Well", well_id),
                node_id("Formation", code),
                type="DRILLED_THROUGH",
                basis=(
                    "EVENT_ATTRIBUTION"
                    if code in attributed.get(well_id, set())
                    else "WELL_TD_FORMATION"
                ),
            )

    documents = {
        row.id: row for row in session.scalars(select(Document))
    }

    # Events, hazards, snippets and the two derived node types.
    hazard_counts: dict[str, int] = {}
    hazard_wells: dict[str, set[str]] = {}
    hazard_examples: dict[str, str] = {}
    #: Clause hashes already turned into a node, so identical real sentences
    #: recorded in many events collapse into one node.
    interventions: set[str] = set()
    outcomes: set[str] = set()
    pending: list[tuple[str, str, str, dict[str, Any]]] = []
    events_by_well: dict[str, list[DrillingEvent]] = {}

    for row in session.scalars(select(DrillingEvent).order_by(DrillingEvent.id)):
        event_node = node_id("Event", row.id)
        graph.add_node(event_node, **_event_node(row, documents.get(row.document_id or "")))
        events_by_well.setdefault(row.well_id, []).append(row)

        well_node = node_id("Well", row.well_id)
        if graph.has_node(well_node):
            graph.add_edge(
                well_node,
                event_node,
                type="HAD_EVENT",
                basis="DRILLING_EVENT.WELL_ID",
            )

        hazard = node_id("Hazard", row.event_type)
        hazard_counts[row.event_type] = hazard_counts.get(row.event_type, 0) + 1
        hazard_wells.setdefault(row.event_type, set()).add(row.well_id)
        hazard_examples.setdefault(row.event_type, row.id)
        graph.add_edge(event_node, hazard, type="CLASSIFIED_AS", basis="EVENT_TYPE")

        actions, results, basis = response_clauses(row.description)
        _add_derived_clauses(
            graph, pending, event_node, row, "Intervention", "MITIGATED_BY", actions,
            interventions, basis,
        )
        _add_derived_clauses(
            graph, pending, event_node, row, "Outcome", "LED_TO", results, outcomes, basis
        )

    for hazard_type, count in hazard_counts.items():
        nxt = node_id("Hazard", hazard_type)
        graph.nodes[nxt].update(
            {
                "type": "Hazard",
                "label": hazard_type,
                "event_type": hazard_type,
                "event_count": count,
                "well_count": len(hazard_wells[hazard_type]),
                "example_event_id": hazard_examples[hazard_type],
                "derived": False,
            }
        )

    # FOLLOWED_BY: the same well's events chained in ascending MD order.
    for rows in events_by_well.values():
        ordered = sorted(rows, key=lambda r: (r.md, r.tvd, r.id))
        for earlier, later in zip(ordered, ordered[1:]):
            graph.add_edge(
                node_id("Event", earlier.id),
                node_id("Event", later.id),
                type="FOLLOWED_BY",
                basis="ASCENDING_MD",
                delta_md=round(later.md - earlier.md, 3),
            )

    for row in session.scalars(select(Evidence).order_by(Evidence.id)):
        snippet = node_id("ReportSnippet", row.id)
        graph.add_node(snippet, **_snippet_node(row))
        event_node = node_id("Event", row.event_id)
        if graph.has_node(event_node):
            graph.add_edge(
                event_node,
                snippet,
                type="EXTRACTED_FROM",
                basis="EVIDENCE.EVENT_ID",
            )

    for source, target, edge_type, attributes in pending:
        graph.add_edge(source, target, type=edge_type, **attributes)

    # ANALOG_FOR_HAZARD: read from the relevance engine's cache, never
    # recomputed here. On the real corpus the cache scores essentially every
    # well pair between 0.62 and 0.73, so taking all 13k of them would turn
    # the well layer into a near-clique that carries no information and
    # swallows every traversal. Only the cache's own top band becomes an edge;
    # stats() reports how many cache rows were considered so the cut is
    # visible rather than hidden.
    cached = session.scalar(
        select(func.count()).select_from(OffsetRelation).where(
            OffsetRelation.relevance_score >= ANALOG_MIN_RELEVANCE
        )
    ) or 0
    graph.graph["offset_relations_considered"] = int(
        session.scalar(select(func.count()).select_from(OffsetRelation)) or 0
    )
    graph.graph["offset_relations_used"] = int(cached)
    for row in session.scalars(
        select(OffsetRelation).where(
            OffsetRelation.relevance_score >= ANALOG_MIN_RELEVANCE
        )
    ):
        source = node_id("Well", row.current_well_id)
        target = node_id("Well", row.offset_well_id)
        if graph.has_node(source) and graph.has_node(target):
            graph.add_edge(
                source,
                target,
                type="ANALOG_FOR_HAZARD",
                basis="OFFSET_RELATIONS_CACHE",
                relevance_score=round(float(row.relevance_score), 4),
                relevance_band=row.relevance_band,
                distance_km=round(float(row.distance_km), 3),
            )

    graph.graph["schema_version"] = GRAPH_SCHEMA_VERSION
    return graph


def build_graph(session: Session) -> nx.DiGraph:
    """Return the property graph for the corpus, building it once per process.

    The graph is cached on ``(engine, schema version, row counts)``, so a
    request never re-reads 1,658 events, and a corpus write (which moves the
    counts) transparently rebuilds it. :func:`invalidate` forces a rebuild for
    callers that mutate rows without changing those counts.

    Args:
        session: Open SQLAlchemy session.

    Returns:
        The cached :class:`networkx.DiGraph`.
    """
    key = _cache_key(session)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    _CACHE.clear()  # a corpus change invalidates every other engine's entry
    graph = _assemble(session)
    _CACHE[key] = graph
    return graph


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #


def _serialise_node(node_id_value: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"id": node_id_value, **data}


def _serialise_edge(source: str, target: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"source": source, "target": target, **data}


def node_exists(session: Session, value: str) -> bool:
    """True when ``value`` names a node in the current graph.

    A malformed id is a lookup miss, not an error: the caller decides whether
    that is a 404.
    """
    try:
        return normalise_node_id(value) in build_graph(session)
    except ValueError:
        return False


#: Neighbour visit order within one hop. A well in the real corpus has 1,658
#: events and dozens of offset links, so without an explicit order the node cap
#: would be spent on whichever neighbour happens to sort first. The node's own
#: content is shown before the broad neighbourhood links.
_NEIGHBOUR_ORDER: tuple[str, ...] = (
    "HAD_EVENT",
    "EXTRACTED_FROM",
    "CLASSIFIED_AS",
    "MITIGATED_BY",
    "LED_TO",
    "DRILLED_THROUGH",
    "FOLLOWED_BY",
    "ANALOG_FOR_HAZARD",
)
_NEIGHBOUR_RANK: dict[str, int] = {t: i for i, t in enumerate(_NEIGHBOUR_ORDER)}


def _edge_type(graph: nx.DiGraph, source: str, target: str) -> str:
    """Type of the edge joining two nodes, whichever way it points."""
    if graph.has_edge(source, target):
        return str(graph.edges[source, target]["type"])
    return str(graph.edges[target, source]["type"])


def _ordered_neighbours(graph: nx.DiGraph, current: str) -> list[str]:
    """Neighbours of ``current``, closest edge type first then by node id."""
    neighbours = set(graph.successors(current)) | set(graph.predecessors(current))
    return sorted(
        neighbours,
        key=lambda n: (
            _NEIGHBOUR_RANK.get(_edge_type(graph, current, n), 99),
            n,
        ),
    )


def _canonical_or_missing(value: str) -> str:
    """Canonicalise a node id, reporting a malformed one as a missing node.

    Keeps every graph lookup raising the single :class:`KeyError` the routers
    turn into a 404, whatever the caller sent.
    """
    try:
        return normalise_node_id(value)
    except ValueError:
        raise KeyError(value) from None




def stats(session: Session) -> dict[str, Any]:
    """Node and edge counts by type, plus the totals behind them.

    Args:
        session: Open SQLAlchemy session.

    Returns:
        A dict with ``node_types``/``edge_types`` (every declared type present
        even when its count is zero), the totals and the schema version.
    """
    graph = build_graph(session)
    node_counts = {kind: 0 for kind in NODE_TYPES}
    for _, data in graph.nodes(data=True):
        node_counts[data["type"]] = node_counts.get(data["type"], 0) + 1
    edge_counts = {kind: 0 for kind in EDGE_TYPES}
    for _, _, data in graph.edges(data=True):
        edge_counts[data["type"]] = edge_counts.get(data["type"], 0) + 1
    return {
        "schema_version": graph.graph.get("schema_version"),
        "node_types": node_counts,
        "edge_types": edge_counts,
        "node_total": graph.number_of_nodes(),
        "edge_total": graph.number_of_edges(),
        "offset_relations": {
            "considered": graph.graph.get("offset_relations_considered", 0),
            "used": graph.graph.get("offset_relations_used", 0),
            "min_relevance": ANALOG_MIN_RELEVANCE,
        },
    }


def subgraph(
    session: Session, root_id: str, depth: int = 2, limit: int = 200
) -> dict[str, Any]:
    """Breadth-first neighbourhood of ``root_id``, serialisable for the UI.

    Args:
        session: Open SQLAlchemy session.
        root_id: A node id such as ``WELL:15/9-F-9A``.
        depth: Maximum hop count to expand.
        limit: Hard cap on returned nodes.

    Returns:
        ``{"root", "depth", "limit", "nodes", "edges", "truncated",
        "truncated_by"}``. ``truncated_by`` is ``"LIMIT"`` when the node cap
        cut the walk short, ``"DEPTH"`` when the requested depth did, and
        ``None`` when the walk exhausted the neighbourhood.

    Raises:
        KeyError: when ``root_id`` is not in the graph.
    """
    if depth < 0:
        raise ValueError("depth must be >= 0")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    graph = build_graph(session)
    root_id = _canonical_or_missing(root_id)
    if root_id not in graph:
        raise KeyError(root_id)

    order: list[str] = [root_id]
    hops: dict[str, int] = {root_id: 0}
    truncated_by: str | None = None
    queue: deque[tuple[str, int]] = deque([(root_id, 0)])
    while queue and truncated_by is None:
        current, level = queue.popleft()
        if level >= depth:
            # Unexplored neighbours sit one hop past the requested depth.
            if graph.degree(current) > 0:
                truncated_by = "DEPTH"
            continue
        for neighbour in _ordered_neighbours(graph, current):
            if neighbour in hops:
                continue
            if len(order) >= limit:
                truncated_by = "LIMIT"
                break
            hops[neighbour] = level + 1
            order.append(neighbour)
            queue.append((neighbour, level + 1))

    keep = set(order)
    nodes = [_serialise_node(n, dict(graph.nodes[n])) for n in order]
    edges = [
        _serialise_edge(u, v, dict(data))
        for u, v, data in graph.edges(data=True)
        if u in keep and v in keep
    ]
    return {
        "root": root_id,
        "root_type": graph.nodes[root_id]["type"],
        "depth": depth,
        "limit": limit,
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated_by is not None,
        "truncated_by": truncated_by,
    }


def path_between(
    session: Session, a_id: str, b_id: str, max_hops: int = 4
) -> dict[str, Any]:
    """Shortest chain between two nodes, or an honest "not found".

    Args:
        session: Open SQLAlchemy session.
        a_id: Start node id.
        b_id: Target node id.
        max_hops: Refuse chains longer than this many hops.

    Returns:
        ``{"found", "hops", "path", "steps", "reason"}``. ``path`` holds the
        node ids; ``steps`` holds the edge type between each consecutive pair.

    Raises:
        KeyError: when either id is not in the graph.
    """
    if max_hops < 1:
        raise ValueError("max_hops must be >= 1")
    graph = build_graph(session)
    a_id = _canonical_or_missing(a_id)
    b_id = _canonical_or_missing(b_id)
    if a_id not in graph:
        raise KeyError(a_id)
    if b_id not in graph:
        raise KeyError(b_id)
    if a_id == b_id:
        return {
            "found": True,
            "hops": 0,
            "path": [a_id],
            "steps": [],
            "reason": "same node",
        }

    try:
        chain = nx.bidirectional_shortest_path(graph, a_id, b_id)
    except nx.NetworkXNoPath:
        undirected = graph.to_undirected(as_view=True)
        try:
            reach = nx.shortest_path_length(undirected, a_id, b_id)
        except nx.NetworkXNoPath:
            reach = None
        return {
            "found": False,
            "hops": None,
            "path": [],
            "steps": [],
            "reason": (
                "no chain in the corpus"
                if reach is None
                else f"no directed chain; closest undirected distance is {reach} hops"
            ),
        }

    hops = len(chain) - 1
    steps = [
        {
            "from": chain[i],
            "to": chain[i + 1],
            "type": graph.edges[chain[i], chain[i + 1]]["type"],
        }
        for i in range(hops)
    ]
    if hops > max_hops:
        return {
            "found": False,
            "hops": hops,
            "path": chain,
            "steps": steps,
            "reason": f"shortest chain is {hops} hops, above max_hops={max_hops}",
        }
    return {
        "found": True,
        "hops": hops,
        "path": chain,
        "steps": steps,
        "reason": "shortest directed chain",
    }


def expand(
    session: Session,
    seed_ids: Iterable[str],
    max_hops: int = 2,
    limit: int = 400,
    node_type: str | None = None,
) -> list[dict[str, Any]]:
    """Expand from several seeds at once, keeping the chain that reached each node.

    The seeds are the caller's relevance ranking, and the search is
    lexicographic on ``(hops, seed rank)``: a node one hop from the top-ranked
    seed beats a node one hop from a weaker seed, which in turn beats a node
    two hops from the best one. Running the seeds as one search rather than
    several is what stops a prolific low-rank seed from spending the whole
    result budget before the others are looked at.

    Args:
        session: Open SQLAlchemy session.
        seed_ids: Node ids to expand from, in relevance order; unknown and
            malformed ids are skipped.
        max_hops: Maximum hop count from any seed.
        limit: Hard cap on returned records, applied after ranking.
        node_type: Keep only nodes of this type.

    Returns:
        ``[{"node", "hops", "seed", "seed_rank", "path"}, ...]`` in rank
        order. ``path`` is the node id chain that reached ``node``.
    """
    if max_hops < 1:
        raise ValueError("max_hops must be >= 1")
    graph = build_graph(session)

    seeds: list[str] = []
    for raw_seed in seed_ids:
        try:
            seed = normalise_node_id(raw_seed)
        except ValueError:
            continue
        if seed in graph and seed not in seeds:
            seeds.append(seed)

    # Dijkstra over (hops, seed rank): popping the smallest key settles the
    # best route to that node, so ``parent`` always reconstructs that route.
    best: dict[str, tuple[int, int]] = {}
    parent: dict[str, str] = {}
    seed_of: dict[str, str] = {}
    queue: list[tuple[int, int, str]] = []
    for rank, seed in enumerate(seeds):
        best[seed] = (0, rank)
        seed_of[seed] = seed
        queue.append((0, rank, seed))
    heapq.heapify(queue)

    records: list[dict[str, Any]] = []
    while queue:
        hops, rank, current = heapq.heappop(queue)
        if best.get(current) != (hops, rank):
            continue  # superseded by a better route
        if (
            current not in seeds
            and node_type is not None
            and graph.nodes[current]["type"] == node_type
        ):
            # Settled in (hops, seed rank) order, so this is already the order
            # the caller will receive, and the walk can stop at the limit.
            chain: list[str] = []
            cursor = current
            while cursor:
                chain.append(cursor)
                cursor = parent.get(cursor, "")
            chain.reverse()
            # The walk is undirected — a chain may step back along a
            # FOLLOWED_BY link — so each step records which way the stored
            # edge actually points, rather than leaving the UI to guess.
            steps = [
                {
                    "from": source,
                    "to": target,
                    "type": _edge_type(graph, source, target),
                    "forward": graph.has_edge(source, target),
                }
                for source, target in zip(chain, chain[1:])
            ]
            records.append(
                {
                    "node": current,
                    "hops": hops,
                    "seed": seed_of[current],
                    "seed_rank": rank,
                    "path": chain,
                    "steps": steps,
                }
            )
            if len(records) >= limit:
                break
        if hops >= max_hops:
            continue
        for neighbour in _ordered_neighbours(graph, current):
            candidate = (hops + 1, rank)
            if candidate >= best.get(neighbour, (_UNREACHED, _UNREACHED)):
                continue
            best[neighbour] = candidate
            parent[neighbour] = current
            seed_of[neighbour] = seed_of.get(current, current)
            heapq.heappush(queue, (candidate[0], candidate[1], neighbour))
    return records


def labels_for(session: Session, path: Sequence[str]) -> list[str]:
    """Return human labels for a node id chain, for display next to a path."""
    graph = build_graph(session)
    return [str(graph.nodes[p]["label"]) if p in graph else p for p in path]

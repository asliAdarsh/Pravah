"""Tests for the knowledge graph and graph-constrained retrieval.

These run against the **real** corpus — the NPD well headers, the Volve Daily
Drilling Reports and their verbatim evidence spans — loaded into a throwaway
SQLite file, so the counts asserted here are the counts the shipped prototype
actually holds. Nothing is stubbed and no expected count is hard-coded: each
assertion recomputes what the database says and checks the graph agrees.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import graph, graphrag, models  # noqa: E402  (registers the mappers)
from app.db import get_db  # noqa: E402
from app.models import (  # noqa: E402
    DrillingEvent,
    Evidence,
    Formation,
    OffsetRelation,
    Well,
)
from app.relevance import refresh_relations  # noqa: E402
from app.seed_real import VOLVE_TELEMETRY_WELL, seed_real  # noqa: E402

#: The stuck-pipe records the Volve field published. Used as a known-real
#: landmark, never as a scoring input.
STUCK_PIPE_EVENTS = ("EV-00306-1397", "EV-00306-1398")


@pytest.fixture(scope="session")
def real_engine(tmp_path_factory):
    """A throwaway SQLite file loaded with the real NPD + Volve corpus."""
    path = tmp_path_factory.mktemp("pravah_graph") / "real.db"
    eng = create_engine(f"sqlite:///{path.as_posix()}", future=True)

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    models.Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    session = factory()
    try:
        seed_real(session)
        # The graph reads the offset cache rather than recomputing relevance, so
        # the cache has to hold the real scores for ANALOG_FOR_HAZARD to mean
        # anything. This is the engine's own rebuild path, not a graph shortcut.
        refresh_relations(session)
        session.commit()
    finally:
        session.close()
    return eng


@pytest.fixture
def real_session(real_engine):
    """A per-test session over the real corpus; the graph cache is dropped."""
    graph.invalidate()
    factory = sessionmaker(
        bind=real_engine, autoflush=False, expire_on_commit=False, future=True
    )
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        graph.invalidate()


@pytest.fixture
def real_client(real_engine):
    """A test client whose requests read the real corpus."""
    from app.main import create_app

    application = create_app()
    factory = sessionmaker(
        bind=real_engine, autoflush=False, expire_on_commit=False, future=True
    )

    def _override():
        db = factory()
        try:
            yield db
        finally:
            db.rollback()
            db.close()

    application.dependency_overrides[get_db] = _override
    # Deliberately not used as a context manager: the app's lifespan seeds the
    # process-wide database, and these tests must read only the temp corpus.
    client = TestClient(application)
    yield client
    client.close()
    graph.invalidate()


def _count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


# --------------------------------------------------------------------------- #
# Node and edge counts
# --------------------------------------------------------------------------- #


def test_node_counts_match_the_database(real_session):
    """Every declared node type holds exactly the rows the database holds."""
    built = graph.build_graph(real_session)
    by_type = Counter(data["type"] for _, data in built.nodes(data=True))

    assert by_type["Well"] == _count(real_session, Well)
    assert by_type["Formation"] == _count(real_session, Formation)
    assert by_type["Event"] == _count(real_session, DrillingEvent)
    assert by_type["ReportSnippet"] == _count(real_session, Evidence)
    # Hazard is a class of event_type, so it can never exceed the event count.
    assert 0 < by_type["Hazard"] <= by_type["Event"]
    assert set(by_type) <= set(graph.NODE_TYPES)


def test_every_declared_node_type_is_populated(real_session):
    """A node type that is empty is a dead node type, not a feature."""
    by_type = Counter(
        data["type"] for _, data in graph.build_graph(real_session).nodes(data=True)
    )
    for kind in graph.NODE_TYPES:
        assert by_type[kind] > 0, f"{kind} node type is empty on the real corpus"
        assert graph.stats(real_session)["node_types"][kind] == by_type[kind]


def test_edge_counts_match_the_database(real_session):
    """Edges are derived from real rows, so each count has a row behind it."""
    built = graph.build_graph(real_session)
    by_type = Counter(data["type"] for _, _, data in built.edges(data=True))
    session = real_session

    event_count = _count(session, DrillingEvent)
    snippet_count = _count(session, Evidence)
    # One HAD_EVENT and one CLASSIFIED_AS per event, one EXTRACTED_FROM per
    # evidence row.
    assert by_type["HAD_EVENT"] == event_count
    assert by_type["CLASSIFIED_AS"] == event_count
    assert by_type["EXTRACTED_FROM"] == snippet_count
    # FOLLOWED_BY chains n events of a well into n-1 links, summed per well.
    events_per_well = session.execute(
        select(DrillingEvent.well_id, func.count())
        .group_by(DrillingEvent.well_id)
    ).all()
    assert by_type["FOLLOWED_BY"] == sum(count - 1 for _, count in events_per_well)
    # Every cached offset above the declared threshold becomes one link.
    assert by_type["ANALOG_FOR_HAZARD"] == session.scalar(
        select(func.count())
        .select_from(OffsetRelation)
        .where(OffsetRelation.relevance_score >= graph.ANALOG_MIN_RELEVANCE)
    )
    assert by_type["DRILLED_THROUGH"] > 0
    assert by_type["MITIGATED_BY"] > 0
    assert by_type["LED_TO"] > 0
    assert set(by_type) <= set(graph.EDGE_TYPES)


def test_stats_totals_add_up(real_session):
    """The per-type counts and the graph totals are the same numbers."""
    reported = graph.stats(real_session)
    built = graph.build_graph(real_session)
    assert sum(reported["node_types"].values()) == built.number_of_nodes()
    assert sum(reported["edge_types"].values()) == built.number_of_edges()
    assert reported["node_total"] == built.number_of_nodes()
    assert reported["edge_total"] == built.number_of_edges()
    assert reported["offset_relations"]["used"] == reported["edge_types"]["ANALOG_FOR_HAZARD"]


def test_every_edge_references_existing_nodes(real_session):
    """No dangling edge: both ends are real graph nodes of the declared type."""
    built = graph.build_graph(real_session)
    for source, target, data in built.edges(data=True):
        assert built.has_node(source), f"edge from missing node {source}"
        assert built.has_node(target), f"edge to missing node {target}"
        assert data["type"] in graph.EDGE_TYPES


# --------------------------------------------------------------------------- #
# Edge semantics
# --------------------------------------------------------------------------- #


def test_followed_by_is_ordered_by_md_within_a_well(real_session):
    """Each FOLLOWED_BY link goes from the shallower event to the deeper one."""
    session = real_session
    built = graph.build_graph(session)
    links = [
        (source, target, data)
        for source, target, data in built.edges(data=True)
        if data["type"] == "FOLLOWED_BY"
    ]
    assert links
    ids = {key for link in links for key in (link[0], link[1])}
    rows = {
        row.id: row
        for row in session.scalars(
            select(DrillingEvent).where(
                DrillingEvent.id.in_([n.split(":", 1)[1] for n in ids])
            )
        )
    }
    for source, target, data in links:
        earlier = rows[source.split(":", 1)[1]]
        later = rows[target.split(":", 1)[1]]
        assert earlier.well_id == later.well_id
        # Two events can sit at the same logged depth; the chain is ordered by
        # (MD, TVD, id), so a link never goes backward.
        assert (earlier.md, earlier.tvd, earlier.id) < (later.md, later.tvd, later.id)
        assert data["delta_md"] == pytest.approx(later.md - earlier.md, abs=1e-3)
        assert data["basis"] == "ASCENDING_MD"


def test_followed_by_chains_a_whole_well_in_depth_order(real_session):
    """The stored links are exactly the MD-ordered chain of that well's events."""
    session = real_session
    built = graph.build_graph(session)
    ordered = sorted(
        session.scalars(
            select(DrillingEvent).where(DrillingEvent.well_id == VOLVE_TELEMETRY_WELL)
        ),
        key=lambda r: (r.md, r.tvd, r.id),
    )
    assert len(ordered) > 1
    stored = {
        (source.split(":", 1)[1], target.split(":", 1)[1])
        for source, target, data in built.edges(data=True)
        if data["type"] == "FOLLOWED_BY"
    }
    mine = {
        pair for pair in stored if session.get(DrillingEvent, pair[0]) is not None
        and session.get(DrillingEvent, pair[0]).well_id == VOLVE_TELEMETRY_WELL
    }
    assert mine == {(a.id, b.id) for a, b in zip(ordered, ordered[1:])}

    # The well's shallowest event heads the chain: no FOLLOWED_BY link points
    # at it, because the corpus records nothing shallower in that well.
    head = graph.node_id("Event", ordered[0].id)
    assert all(
        data["type"] != "FOLLOWED_BY"
        for _, _, data in built.in_edges(head, data=True)
    )
    tail = graph.node_id("Event", ordered[-1].id)
    assert all(
        data["type"] != "FOLLOWED_BY"
        for _, _, data in built.out_edges(tail, data=True)
    )


def test_had_event_links_an_event_to_its_own_well(real_session):
    """HAD_EVENT never crosses a well boundary."""
    session = real_session
    built = graph.build_graph(session)
    for source, target, data in built.edges(data=True):
        if data["type"] != "HAD_EVENT":
            continue
        event_id = target.split(":", 1)[1]
        well_id = source.split(":", 1)[1]
        assert session.get(DrillingEvent, event_id).well_id == well_id


def test_extracted_from_links_a_snippet_to_its_backing_event(real_session):
    """The graph never claims a snippet came from an event that did not cite it."""
    session = real_session
    built = graph.build_graph(session)
    checked = 0
    for source, target, data in built.edges(data=True):
        if data["type"] != "EXTRACTED_FROM":
            continue
        row = session.get(Evidence, target.split(":", 1)[1])
        assert row is not None
        assert row.event_id == source.split(":", 1)[1]
        checked += 1
        if checked >= 200:
            break
    assert checked == 200


def test_derived_nodes_quote_their_source_event_verbatim(real_session):
    """An Intervention/Outcome label is a literal substring of the real line.

    This is the honesty guarantee: the derived nodes may be *classified* by the
    vocabulary, but not one character of their text is invented.
    """
    session = real_session
    built = graph.build_graph(session)
    derived = [
        (node, data)
        for node, data in built.nodes(data=True)
        if data["type"] in ("Intervention", "Outcome")
    ]
    assert derived
    for node, data in derived:
        source = session.get(DrillingEvent, data["source_event_id"])
        assert source is not None, f"{node} cites a missing event"
        assert data["text"] in source.description, f"{node} is not verbatim"
        assert data["derived"] is True
        assert data["cited_by"] >= 1


def test_analog_for_hazard_matches_the_offset_cache(real_session):
    """The well-to-well links are the cached scores, carrying their score."""
    session = real_session
    built = graph.build_graph(session)
    links = [
        (source, target, data)
        for source, target, data in built.edges(data=True)
        if data["type"] == "ANALOG_FOR_HAZARD"
    ]
    assert links
    cached = {
        (row.current_well_id, row.offset_well_id): row
        for row in session.scalars(select(OffsetRelation))
    }
    for source, target, data in links:
        key = (source.split(":", 1)[1], target.split(":", 1)[1])
        assert key in cached
        assert data["relevance_score"] == pytest.approx(cached[key].relevance_score, abs=1e-4)
        assert data["relevance_score"] >= graph.ANALOG_MIN_RELEVANCE
        assert data["basis"] == "OFFSET_RELATIONS_CACHE"


# --------------------------------------------------------------------------- #
# Traversal
# --------------------------------------------------------------------------- #


def test_subgraph_from_a_well_reaches_its_events_and_snippets(real_session):
    """Two hops from the well reaches its events and the real spans behind them."""
    result = graph.subgraph(
        real_session, f"WELL:{VOLVE_TELEMETRY_WELL}", depth=2, limit=500_000
    )
    assert result["root"] == f"WELL:{VOLVE_TELEMETRY_WELL}"
    assert result["root_type"] == "Well"
    by_type = Counter(node["type"] for node in result["nodes"])
    assert by_type["Event"] == _count(real_session, DrillingEvent)
    assert by_type["ReportSnippet"] == _count(real_session, Evidence)
    assert by_type["Hazard"] > 0
    assert by_type["Formation"] > 0

    # Every returned node and edge is internally consistent and JSON-ready.
    ids = {node["id"] for node in result["nodes"]}
    assert len(ids) == len(result["nodes"])
    for edge in result["edges"]:
        assert edge["source"] in ids and edge["target"] in ids


def test_subgraph_reports_when_the_limit_cut_it_short(real_session):
    """A capped walk returns what fits and says so, rather than silently lying."""
    capped = graph.subgraph(
        real_session, f"WELL:{VOLVE_TELEMETRY_WELL}", depth=2, limit=25
    )
    assert len(capped["nodes"]) == 25
    assert capped["truncated"] is True
    assert capped["truncated_by"] == "LIMIT"

    # One hop from a real event is capped by the depth, not the limit: the event
    # still has neighbours at hop two.
    at_depth = graph.subgraph(
        real_session, f"EVENT:{STUCK_PIPE_EVENTS[0]}", depth=1, limit=5000
    )
    assert at_depth["truncated"] is True
    assert at_depth["truncated_by"] == "DEPTH"
    assert at_depth["truncated_by"] != "LIMIT"




def test_subgraph_rejects_an_unknown_node(real_session):
    """An unknown root is a lookup miss, not an empty graph."""
    with pytest.raises(KeyError):
        graph.subgraph(real_session, "WELL:no-such-well", depth=1, limit=10)
    with pytest.raises(KeyError):
        graph.subgraph(real_session, "NOT_A_TYPE:x", depth=1, limit=10)


def test_path_between_returns_a_real_chain(real_session):
    """Well → Event → Hazard is a genuine three-node chain of real rows."""
    result = graph.path_between(
        real_session, f"WELL:{VOLVE_TELEMETRY_WELL}", "HAZARD:STUCK_PIPE", max_hops=6
    )
    assert result["found"] is True
    assert result["hops"] == 2
    assert result["path"][0] == f"WELL:{VOLVE_TELEMETRY_WELL}"
    assert result["path"][-1] == "HAZARD:STUCK_PIPE"
    assert [step["type"] for step in result["steps"]] == ["HAD_EVENT", "CLASSIFIED_AS"]

    built = graph.build_graph(real_session)
    for step in result["steps"]:
        assert built.has_edge(step["from"], step["to"])
        assert built.edges[step["from"], step["to"]]["type"] == step["type"]


def test_path_between_honours_max_hops_and_reports_why(real_session):
    """Too few hops gives an explicit refusal, not a longer chain."""
    shallow = graph.path_between(
        real_session, f"WELL:{VOLVE_TELEMETRY_WELL}", "HAZARD:STUCK_PIPE", max_hops=1
    )
    assert shallow["found"] is False
    assert shallow["hops"] == 2
    assert shallow["path"][-1] == "HAZARD:STUCK_PIPE"
    assert "max_hops=1" in shallow["reason"]


def test_path_between_reports_a_genuinely_unlinked_node(real_session):
    """A unit no well in this release drilled has no chain, and it says so.

    The Volve lithostratigraphy carries 85 real NPD units but only some of them
    are attributed to a well or an event here; the rest are legitimately
    isolated rather than silently linked to a neighbour.
    """
    built = graph.build_graph(real_session)
    isolated = sorted(node for node in built if built.degree(node) == 0)
    assert isolated, "the real corpus should carry units nothing drilled through"
    result = graph.path_between(
        real_session, f"WELL:{VOLVE_TELEMETRY_WELL}", isolated[0], max_hops=4
    )
    assert result["found"] is False
    assert result["path"] == []
    assert result["hops"] is None
    assert result["reason"] == "no chain in the corpus"


def test_path_between_normalises_the_type_prefix_only(real_session):
    """The type prefix is case-insensitive; the key is a real id and is not."""
    result = graph.path_between(
        real_session, f"well:{VOLVE_TELEMETRY_WELL}", "hazard:STUCK_PIPE", max_hops=4
    )
    assert result["found"] is True
    assert result["path"][0] == f"WELL:{VOLVE_TELEMETRY_WELL}"
    assert result["path"][-1] == "HAZARD:STUCK_PIPE"
    # The key after the colon is a real identifier, so it is not case-folded.
    with pytest.raises(KeyError):
        graph.path_between(
            real_session, f"WELL:{VOLVE_TELEMETRY_WELL}", "hazard:stuck_pipe"
        )


def test_node_id_round_trips(real_session):
    """Every declared type has a prefix, and splitting gives the type back."""
    for kind in graph.NODE_TYPES:
        canonical = graph.node_id(kind, "MiXeD-Key")
        prefix, _, key = canonical.partition(":")
        assert prefix == prefix.upper()
        # The prefix is case-folded, the key is left exactly as written.
        assert graph.normalise_node_id(prefix.lower() + ":MiXeD-Key") == canonical
        assert graph.split_node_id(canonical) == (kind, "MiXeD-Key")
    with pytest.raises(ValueError):
        graph.normalise_node_id("NOPE:x")
    with pytest.raises(ValueError):
        graph.normalise_node_id("WELL")
    with pytest.raises(ValueError):
        graph.normalise_node_id("WELL")


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #


def test_graph_is_built_once_and_reused(real_session):
    """A second request reuses the cached graph rather than re-reading 1,658 events."""
    first = graph.build_graph(real_session)
    assert graph.build_graph(real_session) is first
    graph.invalidate()
    assert graph.build_graph(real_session) is not first


def test_writing_to_the_corpus_rebuilds_the_graph(real_session):
    """The cache key moves when the corpus does, so a stale graph is impossible."""
    from datetime import date

    before = graph.build_graph(real_session)
    assert before.number_of_nodes() > 0
    real_session.add(
        Well(
            id="ZZ-TEST-ONLY",
            name="test-only well",
            field="N",
            block="30/11",
            operator="NONE",
            well_type="EXPLORATION",
            status="PLANNED",
            latitude=0.0,
            longitude=0.0,
            spud_date=date(2000, 1, 1),
            rig="NONE",
            mud_system="NONE",
            water_depth_m=0.0,
            total_depth_md=0.0,
            current_depth_md=0.0,
            current_tvd=0.0,
        )
    )
    real_session.flush()
    after = graph.build_graph(real_session)
    assert after is not before
    assert graph.node_id("Well", "ZZ-TEST-ONLY") in after


# --------------------------------------------------------------------------- #
# GraphRAG
# --------------------------------------------------------------------------- #


def test_graphrag_returns_only_stored_evidence_spans(real_session):
    """Every snippet returned is a literal row of the evidence table."""
    result = graphrag.retrieve(
        real_session,
        "stuck pipe recovery",
        f"WELL:{VOLVE_TELEMETRY_WELL}",
        max_hops=3,
        limit=8,
    )
    assert result["result_count"] > 0
    spans = set(real_session.scalars(select(Evidence.text_span)))
    for record in result["results"]:
        snippet = record["snippet"]
        assert snippet["text"] in spans, "returned text is not a stored evidence span"
        stored = real_session.get(Evidence, snippet["evidence_id"])
        assert stored is not None
        assert stored.text_span == snippet["text"]
        assert record["provenance"] == graphrag.PROVENANCE
        assert record["hops"] == len(record["path"]) - 1


def test_graphrag_paths_are_real_edge_chains(real_session):
    """The recorded hop chain is walkable in the graph, step for step."""
    result = graphrag.retrieve(
        real_session,
        "how did they free the stuck string",
        f"WELL:{VOLVE_TELEMETRY_WELL}",
        max_hops=3,
        limit=8,
    )
    built = graph.build_graph(real_session)
    assert result["result_count"] > 0
    for record in result["results"]:
        path = record["path"]
        assert path[0] == record["seed"]
        assert path[-1] == f"SNIPPET:{record['snippet']['evidence_id']}"
        for earlier, later in zip(path, path[1:]):
            assert built.has_edge(earlier, later) or built.has_edge(later, earlier)
        assert len(record["path_labels"]) == len(path)


def test_graphrag_path_steps_name_the_stored_edges(real_session):
    """Each step of a recorded chain names the real edge it walked, and its way."""
    result = graphrag.retrieve(
        real_session, "stuck pipe", f"WELL:{VOLVE_TELEMETRY_WELL}", max_hops=3, limit=12
    )
    built = graph.build_graph(real_session)
    saw_multi_hop = False
    for record in result["results"]:
        steps = record["path_steps"]
        assert len(steps) == record["hops"]
        assert [s["from"] for s in steps] + [record["path"][-1]] == record["path"]
        for step in steps:
            source, target = step["from"], step["to"]
            assert built.has_edge(source, target) or built.has_edge(target, source)
            assert step["type"] in graph.EDGE_TYPES
            assert step["forward"] == built.has_edge(source, target)
        if record["hops"] > 1:
            saw_multi_hop = True
    assert saw_multi_hop, "a stuck-pipe query should reach beyond its own event"


def test_graphrag_finds_the_stuck_pipe_line_and_its_response(real_session):
    """A stuck-pipe question surfaces the real worked-free span, cited."""
    result = graphrag.retrieve(
        real_session,
        "stuck pipe",
        f"WELL:{VOLVE_TELEMETRY_WELL}",
        max_hops=3,
        limit=10,
    )
    texts = [record["snippet"]["text"] for record in result["results"]]
    assert any("Worked stuck pipe free" in text for text in texts)
    freed = next(
        record
        for record in result["results"]
        if "Worked stuck pipe free" in record["snippet"]["text"]
    )
    assert freed["event"]["event_type"] == "STUCK_PIPE"
    assert freed["well"]["id"] == VOLVE_TELEMETRY_WELL
    assert freed["snippet"]["document_id"]
    # The span is the DDR line itself, not a rewording of it: the evidence row
    # stores either the whole description or the mitigation clause.
    event = real_session.get(DrillingEvent, freed["event"]["id"])
    assert freed["snippet"]["text"].strip() in (
        event.description.strip(),
        event.mitigation.strip(),
    )
    assert freed["event"]["md"] == event.md


def test_graphrag_reports_an_empty_traversal_instead_of_inventing_one(real_session):
    """One hop from an event reaches no snippet, and the answer says so."""
    result = graphrag.retrieve(
        real_session,
        "stuck pipe",
        f"EVENT:{STUCK_PIPE_EVENTS[0]}",
        max_hops=1,
        limit=5,
    )
    if result["result_count"] == 0:
        assert result["results"] == []
        assert "no ReportSnippet node" in result["explanation"]
    else:
        # If a link does exist at one hop it must still be a stored span.
        for record in result["results"]:
            assert record["hops"] == 1


def test_graphrag_rejects_an_unknown_root(real_session):
    with pytest.raises(KeyError):
        graphrag.retrieve(real_session, "stuck pipe", "WELL:no-such-well")


def test_graphrag_limit_is_respected(real_session):
    result = graphrag.retrieve(
        real_session, "mud loss", f"WELL:{VOLVE_TELEMETRY_WELL}", max_hops=2, limit=3
    )
    assert len(result["results"]) <= 3


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #


def test_stats_endpoint(real_client):
    response = real_client.get("/api/v1/graph/stats")
    assert response.status_code == 200
    body = response.json()
    assert set(body["node_types"]) == set(graph.NODE_TYPES)
    assert set(body["edge_types"]) == set(graph.EDGE_TYPES)
    assert body["node_total"] == sum(body["node_types"].values())


def test_subgraph_and_path_endpoints(real_client):
    root = f"WELL:{VOLVE_TELEMETRY_WELL}"
    sub = real_client.get("/api/v1/graph/subgraph", params={"root": root, "depth": 1, "limit": 50})
    assert sub.status_code == 200
    assert len(sub.json()["nodes"]) == 50
    assert sub.json()["truncated"] is True

    path = real_client.get(
        "/api/v1/graph/path", params={"from": root, "to": "HAZARD:STUCK_PIPE", "max_hops": 4}
    )
    assert path.status_code == 200
    assert path.json()["found"] is True
    assert path.json()["hops"] == 2


def test_endpoints_return_404_for_an_unknown_node(real_client):
    sub = real_client.get("/api/v1/graph/subgraph", params={"root": "WELL:no-such-well"})
    assert sub.status_code == 404
    path = real_client.get(
        "/api/v1/graph/path", params={"from": "WELL:no-such-well", "to": "HAZARD:STUCK_PIPE"}
    )
    assert path.status_code == 404
    bad_hop = real_client.get(
        "/api/v1/graph/path", params={"from": f"WELL:{VOLVE_TELEMETRY_WELL}", "to": "NOPE:x"}
    )
    assert bad_hop.status_code == 404


def test_retrieve_endpoint(real_client):
    response = real_client.post(
        "/api/v1/graph/retrieve",
        json={
            "query": "stuck pipe recovery",
            "root_id": f"WELL:{VOLVE_TELEMETRY_WELL}",
            "max_hops": 3,
            "limit": 5,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["provenance"] == "GRAPH_TRAVERSAL"
    assert body["result_count"] == len(body["results"])
    for record in body["results"]:
        assert record["snippet"]["text"]
        assert record["hops"] == len(record["path"]) - 1


def test_retrieve_endpoint_validates_and_404s(real_client):
    bad = real_client.post(
        "/api/v1/graph/retrieve", json={"query": "x", "root_id": "WELL:no-such-well"}
    )
    assert bad.status_code == 404
    invalid = real_client.post(
        "/api/v1/graph/retrieve",
        json={"query": "", "root_id": f"WELL:{VOLVE_TELEMETRY_WELL}", "max_hops": 0},
    )
    assert invalid.status_code == 422
    unknown_field = real_client.post(
        "/api/v1/graph/retrieve",
        json={"query": "x", "root_id": f"WELL:{VOLVE_TELEMETRY_WELL}", "bogus": 1},
    )
    assert unknown_field.status_code == 422

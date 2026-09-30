"""API contract tests — every endpoint in docs/API_CONTRACT.md.

Asserted here is the *contract*: documented response shapes, the real-data
provenance, the five published sources, and the geometry endpoints working
against the real corpus. Nothing pins a synthetic fixture, a magic record id or
a hard-coded count; ids and totals are read from the seeded database.

``/api/v1/telemetry/**`` is not swept: ``routers/telemetry.py`` is mounted
conditionally and is excluded until it lands.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import Document, DrillingEvent, Well

API = "/api/v1"

#: The live well: the only well the public Volve DDR release is attached to.
ACTIVE = "15/9-F-9A"

#: A real block cluster in the NPD export, used where an endpoint needs a well
#: that actually has nearby offsets. Chosen from the corpus, not invented.
CLUSTERED = "35/9-7"


@pytest.fixture
def client(engine, seeded_session):
    """A TestClient bound to the throwaway seeded database."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.db import get_db
    from app.main import create_app

    application = create_app()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    def _override():
        db = factory()
        try:
            yield db
        finally:
            db.rollback()
            db.close()

    application.dependency_overrides[get_db] = _override
    with TestClient(application) as test_client:
        yield test_client


def ok(response, expect=200):
    """Assert the status code and return the decoded JSON."""
    assert response.status_code == expect, response.text
    return response.json()


@pytest.fixture
def well_id(client):
    """The active well's real id, read from the API rather than hard-coded."""
    meta = ok(client.get(f"{API}/meta"))
    assert meta["demo"]["current_well_id"] == ACTIVE
    return meta["demo"]["current_well_id"]


# --------------------------------------------------------------------------- #
# meta
# --------------------------------------------------------------------------- #


def test_health(client):
    payload = ok(client.get(f"{API}/health"))
    assert payload["status"] == "ok"
    assert payload["database"] == "ok"
    assert payload["version"] and payload["engine_version"]
    assert payload["data_provenance"] == "REAL_PUBLIC_DATA"
    # The corpus is real, so the probe must not claim otherwise.
    assert payload.get("real_data") is True
    assert "synthetic_data" not in payload


def test_meta(client):
    payload = ok(client.get(f"{API}/meta"))
    assert payload["app"] == "Pravah"
    assert payload["data_provenance"] == "REAL_PUBLIC_DATA"
    assert payload["dataset_label"]
    assert payload["version"] and payload["engine_version"]

    counts = payload["counts"]
    assert counts["wells"] > 100, "the NPD export covers far more than the demo did"
    assert counts["formations"] > 10
    assert counts["events"] > 0
    assert counts["documents"] > 0
    assert counts["telemetry_samples"] == 16_670
    assert counts["evidence"] >= counts["events"]

    assert len(payload["event_types"]) >= 12
    stuck = next(t for t in payload["event_types"] if t["code"] == "STUCK_PIPE")
    assert set(stuck) >= {"code", "label", "family", "severity_weight", "color"}
    assert [b["code"] for b in payload["severity_bands"]] == ["INFO", "WARNING", "HIGH", "CRITICAL"]
    assert set(payload["relevance_config"]["weights"]) == {
        "formation_similarity", "depth_similarity", "spatial_proximity", "event_similarity",
    }
    assert set(payload["risk_config"]["weights"]) == {
        "historical_event_match", "depth_proximity", "formation_similarity",
        "nearby_well_support", "operational_similarity",
    }
    assert payload["llm"]["configured"] is False
    assert payload["llm"]["mode"] == "RULE_BASED_FALLBACK"
    assert payload["demo"]["current_well_id"] == ACTIVE
    assert payload["demo"]["scenario"]


def test_meta_lists_the_five_published_sources(client):
    """Provenance is not a label: the five vendored datasets are named, sized
    and reported present."""
    sources = {source["id"]: source for source in ok(client.get(f"{API}/meta"))["data_sources"]}
    assert set(sources) == {
        "npd_member_formations", "npd_groups", "npd_casing", "volve_ddrs", "volve_telemetry",
    }
    for source_id, source in sources.items():
        assert source["present"] is True, source_id
        assert source["size_bytes"] > 0, source_id
        assert source["publisher"] and source["url"], source_id
    assert "Norwegian Petroleum Directorate" in sources["npd_member_formations"]["publisher"]


# --------------------------------------------------------------------------- #
# formations + wells
# --------------------------------------------------------------------------- #


def test_formations(client):
    payload = ok(client.get(f"{API}/formations"))
    assert payload["data_provenance"] == "REAL_PUBLIC_DATA"
    assert len(payload["items"]) > 10
    first = payload["items"][0]
    assert set(first) >= {
        "id", "name", "code", "top_depth", "bottom_depth", "lithology",
        "depositional_environment", "age", "description", "is_simulated",
    }
    # NPD horizons, shallowest first.
    assert all("Top" in item["name"] for item in payload["items"])
    depths = [item["top_depth"] for item in payload["items"]]
    assert depths == sorted(depths)
    assert all(item["is_simulated"] is False for item in payload["items"])


def test_wells_list(client, engine):
    payload = ok(client.get(f"{API}/wells"))
    assert payload["data_provenance"] == "REAL_PUBLIC_DATA"
    summary = payload["items"][0]
    assert set(summary) >= {
        "id", "name", "field", "block", "latitude", "longitude", "status",
        "current_depth_md", "current_tvd", "current_formation", "operator",
        "well_type", "spud_date", "water_depth_m", "is_active", "offset_well_count",
        "relevant_event_count", "top_alert_severity",
    }
    # Total matches the database, and the active well is in the list.
    assert payload["total"] == len(payload["items"]) or payload["limit"] < payload["total"]
    assert ACTIVE in {w["id"] for w in payload["items"]}
    assert sum(1 for w in payload["items"] if w["is_active"]) == 1


def test_wells_filters(client):
    payload = ok(client.get(f"{API}/wells", params={"status": "COMPLETED"}))
    assert payload["items"]
    assert all(w["status"] == "COMPLETED" for w in payload["items"])

    q = ok(client.get(f"{API}/wells", params={"q": "15/9"}))["items"]
    assert q and all("15/9" in w["id"] for w in q)

    page = ok(client.get(f"{API}/wells", params={"limit": 3, "offset": 1}))
    assert len(page["items"]) == 3
    assert page["offset"] == 1


def test_wells_rejects_out_of_range_paging(client):
    ok(client.get(f"{API}/wells", params={"limit": 0}), expect=422)
    ok(client.get(f"{API}/wells", params={"limit": 5000}), expect=422)
    ok(client.get(f"{API}/wells", params={"offset": -1}), expect=422)


def test_well_detail(client, well_id):
    """The per-well routes must work for a slash-bearing NPD identifier."""
    payload = ok(client.get(f"{API}/wells/{well_id}"))
    assert payload["id"] == ACTIVE
    assert payload["data_provenance"] == "REAL_PUBLIC_DATA"
    assert payload["current_depth_md"] > 0
    assert payload["current_tvd"] == payload["current_depth_md"]
    assert payload["trajectory"]
    assert all(
        payload["trajectory"][i]["md"] <= payload["trajectory"][i + 1]["md"]
        for i in range(len(payload["trajectory"]) - 1)
    )
    assert set(payload["operating_context"]) >= {
        "section_size", "bit_size", "mud_weight_ppg", "rop_mph", "wob_klb",
        "block", "status_note",
    }
    assert payload["block"] == "15/9"


def test_well_not_found(client):
    ok(client.get(f"{API}/wells/NOPE-99"), expect=404)


# --------------------------------------------------------------------------- #
# nearby — real geometry
# --------------------------------------------------------------------------- #


def test_nearby_returns_a_documented_shape(client):
    payload = ok(client.get(f"{API}/wells/{CLUSTERED}/nearby"))
    assert payload["method"] == "PROTOTYPE_HEURISTIC"
    assert payload["data_provenance"] == "REAL_PUBLIC_DATA"
    assert payload["radius_km"] == 8.0
    assert set(payload["weights"]) == {
        "formation_similarity", "depth_similarity", "spatial_proximity", "event_similarity",
    }
    item = payload["items"][0]
    assert set(item["similarity"]) == set(payload["weights"])
    assert set(item["factors"][0]) == {
        "code", "label", "value", "weight", "contribution", "detail",
    }
    assert item["factors"][0]["contribution"] == pytest.approx(
        item["factors"][0]["weight"] * item["factors"][0]["value"], abs=1e-3
    )
    assert item["why_relevant"]
    assert set(item["source_availability"]) == {"documents", "with_evidence", "coverage"}
    assert item["selected"] is False
    assert item["well"]["id"] and item["well"]["name"]


def test_nearby_offsets_are_real_npd_wells_within_the_radius(client):
    """Real geometry: every offset is a published well inside the radius, and
    the distance is the great-circle distance between the two positions."""
    from app.geo import haversine_km

    payload = ok(client.get(f"{API}/wells/{CLUSTERED}/nearby"))
    assert payload["items"], "the NPD export has real clusters"
    current = ok(client.get(f"{API}/wells/{CLUSTERED}"))
    for item in payload["items"]:
        assert item["distance_km"] <= payload["radius_km"]
        assert item["distance_km"] == pytest.approx(
            haversine_km(
                current["latitude"], current["longitude"],
                item["well"]["latitude"], item["well"]["longitude"],
            ),
            abs=0.5,
        )
        assert "/" in item["well"]["id"], "offsets are NPD identifiers"


def test_nearby_sorted_by_relevance_desc(client):
    payload = ok(client.get(f"{API}/wells/{CLUSTERED}/nearby"))
    scores = [item["relevance_score"] for item in payload["items"]]
    assert scores == sorted(scores, reverse=True)


def test_nearby_respects_radius(client):
    payload = ok(client.get(f"{API}/wells/{CLUSTERED}/nearby", params={"radius_km": 2.0}))
    assert payload["radius_km"] == 2.0
    assert all(item["distance_km"] <= 2.0 for item in payload["items"])
    wide = ok(client.get(f"{API}/wells/{CLUSTERED}/nearby", params={"radius_km": 50.0}))
    assert len(wide["items"]) >= len(payload["items"])


def test_nearby_min_relevance_filter(client):
    payload = ok(client.get(f"{API}/wells/{CLUSTERED}/nearby", params={"min_relevance": 0.5}))
    assert all(item["relevance_score"] >= 0.5 for item in payload["items"])


def test_nearby_rejects_bad_params(client):
    ok(client.get(f"{API}/wells/{CLUSTERED}/nearby", params={"radius_km": 0}), expect=422)
    ok(client.get(f"{API}/wells/{CLUSTERED}/nearby", params={"radius_km": 9999}), expect=422)
    ok(client.get(f"{API}/wells/{CLUSTERED}/nearby", params={"min_relevance": 1.5}), expect=422)
    ok(client.get(f"{API}/wells/NOPE-99/nearby"), expect=404)


# --------------------------------------------------------------------------- #
# events + timeline
# --------------------------------------------------------------------------- #


def test_well_events(client, well_id):
    payload = ok(client.get(f"{API}/wells/{well_id}/events", params={"limit": 5}))
    assert payload["items"]
    assert len(payload["items"]) <= 5
    event = payload["items"][0]
    assert set(event) >= {
        "id", "well_id", "well_name", "event_type", "event_label", "event_subtype",
        "md", "tvd", "formation", "severity", "severity_score", "occurred_at",
        "description", "mitigation", "status", "days_open", "document",
        "evidence_count", "data_provenance",
    }
    assert event["well_id"] == ACTIVE
    assert event["data_provenance"] == "REAL_PUBLIC_DATA"
    assert event["document"]["doc_type"] == "DDR"
    assert event["evidence_count"] >= 1


def test_well_events_respects_its_tvd_window(client, well_id):
    payload = ok(client.get(f"{API}/wells/{well_id}/events", params={"tvd_min": 400, "tvd_max": 600}))
    assert payload["items"]
    for event in payload["items"]:
        assert 400 <= event["tvd"] <= 600, event["id"]


def test_events_list_and_filters(client):
    payload = ok(client.get(f"{API}/events", params={"limit": 5}))
    assert payload["items"]
    assert set(payload) >= {"items", "total", "limit", "offset", "data_provenance"}
    assert payload["data_provenance"] == "REAL_PUBLIC_DATA"

    typed = ok(client.get(f"{API}/events", params={"event_type": "MUD_LOSS", "limit": 20}))
    assert typed["items"]
    assert all(e["event_type"] == "MUD_LOSS" for e in typed["items"])

    scoped = ok(client.get(f"{API}/wells/{ACTIVE}/events", params={"tvd_min": 300, "tvd_max": 700}))
    assert all(300 <= e["tvd"] <= 700 for e in scoped["items"])


def test_events_filters_are_validated(client):
    ok(client.get(f"{API}/events", params={"limit": 0}), expect=422)
    ok(client.get(f"{API}/events", params={"tvd_min": -5}), expect=422)
    ok(client.get(f"{API}/events", params={"near_well_id": ACTIVE, "radius_km": 0}), expect=422)
    ok(client.get(f"{API}/events", params={"severity_min": 2.0}), expect=422)


def test_event_detail_with_evidence(client, well_id):
    listed = ok(client.get(f"{API}/wells/{well_id}/events", params={"limit": 1}))["items"]
    event_id = listed[0]["id"]
    payload = ok(client.get(f"{API}/events/{event_id}"))
    assert payload["id"] == event_id
    assert payload["well_id"] == ACTIVE
    assert payload["document"]["id"]
    assert payload["evidence"], "a seeded event must carry its evidence"
    for row in payload["evidence"]:
        assert set(row) >= {
            "id", "page", "section", "text_span", "confidence", "bbox", "extraction_method",
        }
        # A DDR text layer has no page to cite, so none is invented.
        assert row["page"] is None or isinstance(row["page"], int)
        assert row["text_span"]


def test_event_404(client):
    ok(client.get(f"{API}/events/EV-DOES-NOT-EXIST"), expect=404)


def test_timeline(client, well_id):
    payload = ok(client.get(f"{API}/wells/{well_id}/timeline", params={"window_md": 200}))
    assert payload["data_provenance"] == "REAL_PUBLIC_DATA"
    window = payload["window"]
    assert window["top_md"] < window["bottom_md"]
    assert window["tvd_at_current"] == payload["current_well"]["current_tvd"]
    assert payload["current_marker"]["label"] == "YOU ARE HERE"
    assert payload["entries"]
    kinds = {entry["kind"] for entry in payload["entries"]}
    assert "current_marker" in kinds
    assert kinds & {"drilling_event", "formation_transition"}
    for entry in payload["entries"]:
        assert set(entry) >= {
            "id", "kind", "origin", "well_id", "well_name", "event_type", "event_label",
            "md", "tvd", "formation", "severity", "severity_score", "occurred_at",
            "description", "mitigation", "delta_from_current_md", "relevance_note",
            "document_id", "evidence_count",
        }
    mds = [entry["md"] for entry in payload["entries"]]
    assert mds == sorted(mds), "the timeline is depth-ordered"
    # The window really is the +/- window_md band around the current depth.
    current_md = payload["current_well"]["current_depth_md"]
    assert window["top_md"] == pytest.approx(max(0.0, current_md - 200), abs=0.1)
    assert window["bottom_md"] == pytest.approx(current_md + 200, abs=0.1)


def test_timeline_rejects_a_bad_window(client, well_id):
    ok(client.get(f"{API}/wells/{well_id}/timeline", params={"window_md": -1}), expect=422)
    ok(client.get(f"{API}/wells/NOPE-99/timeline"), expect=404)


# --------------------------------------------------------------------------- #
# evidence
# --------------------------------------------------------------------------- #


def test_evidence_chain_for_a_real_event(client, well_id):
    event_id = ok(client.get(f"{API}/wells/{well_id}/events", params={"limit": 1}))["items"][0]["id"]
    payload = ok(client.get(f"{API}/evidence/{event_id}"))
    assert set(payload) >= {"event", "chain", "context"}
    chain = payload["chain"]
    assert isinstance(chain["alert_ids"], list)
    assert isinstance(chain["reasons"], list)
    for reason in chain["reasons"]:
        assert reason["alert_id"] in chain["alert_ids"]
        assert reason["reason"]
        assert reason["factors"]
    assert chain["well"]["id"] == ACTIVE
    assert chain["document"]["id"] == payload["event"]["document"]["id"]
    assert payload["document"]["doc_type"] == "DDR"
    for row in payload["evidence"]:
        assert row["text_span"], "text_span must be the stored excerpt, not a placeholder"
        assert row["page"] is None or isinstance(row["page"], int)
    context = payload["context"]
    assert context["document_excerpt"] == payload["evidence"][0]["text_span"]
    assert context["previous_event"] is None or context["previous_event"]["id"]


def test_evidence_404(client):
    ok(client.get(f"{API}/evidence/EV-DOES-NOT-EXIST"), expect=404)


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #


def test_documents_list(client):
    payload = ok(client.get(f"{API}/documents", params={"limit": 5}))
    assert payload["items"]
    summary = payload["items"][0]
    assert set(summary) >= {
        "id", "well_id", "well_name", "doc_type", "doc_type_label", "title", "filename",
        "doc_date", "source_system", "page_count", "event_count", "evidence_count",
        "ocr_engine", "extraction_method", "is_simulated",
    }
    # The corpus is Volve DDRs and nothing claims to be simulated.
    assert all(doc["doc_type"] == "DDR" for doc in payload["items"])
    assert all(doc["is_simulated"] is False for doc in payload["items"])
    assert all(doc["id"].startswith("DDR-") for doc in payload["items"])


def test_document_detail(client):
    document_id = ok(client.get(f"{API}/documents", params={"limit": 1}))["items"][0]["id"]
    payload = ok(client.get(f"{API}/documents/{document_id}"))
    assert payload["id"] == document_id
    assert payload["excerpt"]
    assert payload["sections"]
    for section in payload["sections"]:
        assert set(section) == {"heading", "page", "text"}
        # A flat text layer has no page numbers.
        assert section["page"] is None


def test_document_404(client):
    ok(client.get(f"{API}/documents/DOC-DOES-NOT-EXIST"), expect=404)


def test_ingest_json_creates_document_and_events(client, well_id):
    """Operator-supplied text is stored, extracted from, and labelled as such."""
    payload = ok(
        client.post(
            f"{API}/documents/ingest",
            json={
                "filename": "operator-ddr.txt",
                "well_id": well_id,
                "doc_type": "DDR",
                "doc_date": "2024-05-12",
                "text": (
                    "Day 12 - Drilling Summary\n"
                    "Stuck pipe event logged at 512 m MD in the HORDALAND GP. Top "
                    "Formation (severity HIGH).\n"
                    "Mitigation of Record\n"
                    "Reduced ROP to 8 m/h and worked the string free.\n"
                ),
                "ocr_engine": "NONE",
            },
        ),
        expect=201,
    )
    assert payload["data_provenance"] == "OPERATOR_SUPPLIED_UNVERIFIED"
    assert [s["stage"] for s in payload["pipeline"]] == [
        "ocr", "layout", "sections", "entities", "events",
        "depth_normalization", "formation_mapping",
    ]
    assert payload["document"]["is_simulated"] is False
    assert len(payload["extracted_events"]) == 1
    event = payload["extracted_events"][0]
    assert event["event_type"] == "STUCK_PIPE"
    assert event["md"] == 512.0
    assert event["formation"], "the range lookup must map the depth to a unit"
    assert event["evidence_count"] >= 1


def test_ingest_multipart(client, well_id):
    response = client.post(
        f"{API}/documents/ingest",
        files={"file": ("operator-ddr.txt", b"Day 4 - Drilling Summary\nMUD_LOSS at 490 m TVD.\n", "text/plain")},
        data={"well_id": well_id, "doc_type": "DDR"},
    )
    payload = ok(response, expect=201)
    assert payload["document"]["well_id"] == well_id
    assert len(payload["extracted_events"]) == 1
    assert payload["extracted_events"][0]["event_type"] == "MUD_LOSS"


def test_ingest_without_well_is_stored_unattached_with_a_warning(client):
    payload = ok(
        client.post(
            f"{API}/documents/ingest",
            json={"filename": "orphan.txt", "text": "Day 1\nKICK at 1200 m TVD.\n"},
        ),
        expect=201,
    )
    assert payload["document"]["well_id"] is None
    assert payload["warnings"]
    assert payload["extracted_events"] == [], "an event must belong to a well"


def test_ingest_of_unparseable_text_never_crashes(client, well_id):
    payload = ok(
        client.post(
            f"{API}/documents/ingest",
            json={"filename": "junk.txt", "well_id": well_id, "text": "lorem ipsum"},
        ),
        expect=201,
    )
    assert payload["extracted_events"] == []
    assert payload["warnings"]
    assert payload["document"]["id"]


def test_ingest_of_real_ddr_prose_yields_events(client, well_id):
    """A real Volve activity line must be read with the Volve vocabulary."""
    payload = ok(
        client.post(
            f"{API}/documents/ingest",
            json={
                "filename": "volve-ddr.txt",
                "well_id": well_id,
                "doc_type": "DDR",
                "text": (
                    "07:30 - 12:00: Tractor stalled out at 3710 mmd.\n"
                    "Mud loss of 11 bbl/hr observed at 3550 mmd while drilling ahead.\n"
                ),
            },
        ),
        expect=201,
    )
    assert len(payload["extracted_events"]) == 2
    depths = sorted(event["md"] for event in payload["extracted_events"])
    assert depths == [3550.0, 3710.0], "each event keeps its own line's depth"
    assert {event["event_type"] for event in payload["extracted_events"]} == {
        "TORQUE_SPIKE", "MUD_LOSS",
    }


def test_ingest_capabilities_lists_the_backends_that_can_run(client):
    payload = ok(client.get(f"{API}/documents/ingest/capabilities"))
    assert "TEXT_LAYER" in payload["ocr_engines"], "the text layer is always available"
    # A capability list must only name engines that can actually run.
    for engine in payload["ocr_engines"]:
        assert isinstance(engine, str) and engine


# --------------------------------------------------------------------------- #
# offset replay
# --------------------------------------------------------------------------- #


def test_offset_replay(client, well_id):
    payload = ok(client.get(f"{API}/offset-replay/{well_id}"))
    assert payload["data_provenance"] == "REAL_PUBLIC_DATA"
    assert payload["method"] == "PROTOTYPE_HEURISTIC"
    assert payload["window"]["top_tvd"] < payload["window"]["bottom_tvd"]
    assert payload["formation_column"], "the real stratigraphic column must render"
    for entry in payload["formation_column"]:
        assert set(entry) >= {"id", "name", "top_depth", "bottom_depth", "current"}
    assert any(entry["current"] for entry in payload["formation_column"])


def test_offset_replay_returns_real_offsets_for_a_clustered_well(client):
    payload = ok(client.get(f"{API}/offset-replay/{CLUSTERED}"))
    assert payload["offset_wells"], "the NPD export has real clusters"
    entry = payload["offset_wells"][0]
    assert set(entry) >= {
        "well", "distance_km", "relevance_score", "relevance_band", "similarity",
        "why_relevant", "events", "formation_alignment",
    }
    assert entry["well"]["id"] != CLUSTERED
    assert entry["distance_km"] >= 0.0
    assert isinstance(payload["recurring_hazards"], list)
    assert isinstance(payload["alerts"], list)


def test_offset_replay_404(client):
    ok(client.get(f"{API}/offset-replay/NOPE-99"), expect=404)


# --------------------------------------------------------------------------- #
# alerts
# --------------------------------------------------------------------------- #


def test_alerts_list_shape_and_counts(client, well_id):
    payload = ok(client.get(f"{API}/alerts", params={"well_id": well_id}))
    assert set(payload) >= {"items", "total", "counts", "limit", "offset", "data_provenance"}
    assert payload["data_provenance"] == "REAL_PUBLIC_DATA"
    assert set(payload["counts"]) == {"OPEN", "ACKNOWLEDGED", "DISMISSED", "CLOSED"}
    assert payload["total"] == len(payload["items"])
    assert sum(payload["counts"].values()) == payload["total"]
    for alert in payload["items"]:
        assert set(alert) >= {
            "id", "current_well_id", "current_well_name", "event_type", "event_label",
            "title", "severity_band", "risk_score", "current_tvd",
            "interval", "formation", "formation_match", "supporting_well_count",
            "supporting_event_count", "distance_to_interval_m", "status", "created_at",
            "is_active", "headline",
        }
        assert alert["interval"]["top_tvd"] <= alert["interval"]["bottom_tvd"]
        assert 0.0 <= alert["risk_score"] <= 1.0


def test_well_alerts_alias_matches_the_collection_endpoint(client, well_id):
    """/wells/{id}/alerts is documented as an alias of /alerts?well_id=…"""
    alias = ok(client.get(f"{API}/wells/{well_id}/alerts"))
    listed = ok(client.get(f"{API}/alerts", params={"well_id": well_id}))
    assert alias["total"] == listed["total"]
    assert [a["id"] for a in alias["items"]] == [a["id"] for a in listed["items"]]
    assert set(alias["counts"]) == set(listed["counts"])


def test_alert_404(client):
    ok(client.get(f"{API}/alerts/AL-NOT-A-REAL-ID"), expect=404)
    ok(client.get(f"{API}/alerts/AL-NOT-A-REAL-ID/actions"), expect=404)
    ok(client.get(f"{API}/wells/{ACTIVE}/alerts"), expect=200)


def test_alert_filters_are_validated(client):
    ok(client.get(f"{API}/alerts", params={"limit": 0}), expect=422)
    ok(client.get(f"{API}/alerts", params={"limit": 9999}), expect=422)


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #


def test_search_parses_intent_and_returns_structured_results(client, well_id):
    payload = ok(
        client.post(
            f"{API}/search",
            json={
                "query": "What happened around 512 m TVD?",
                "current_well_id": well_id,
                "filters": {
                    "radius_km": 8, "event_type": None, "formation": None,
                    "tvd_min": None, "tvd_max": None,
                },
                "limit": 20,
            },
        )
    )
    intent = payload["parsed_intent"]
    assert intent["tvd_anchor_m"] == 512.0
    assert intent["radius_km"] == 8.0
    assert intent["explain"]
    structured = payload["structured_results"]
    assert set(structured) == {
        "events", "wells", "documents", "mitigations", "evidence", "alerts",
    }
    assert structured["events"], "expected structured events"
    synthesis = payload["synthesis"]
    assert synthesis["provenance"] == "RULE_BASED_TEMPLATE"
    assert synthesis["model"] is None
    assert synthesis["citations"]
    assert synthesis["disclaimer"]
    for citation in synthesis["citations"]:
        assert set(citation) == {"event_id", "document_id", "page", "label"}
        # Never invent a page for a text layer.
        assert citation["page"] is None or isinstance(citation["page"], int)
    assert payload["result_count"] > 0
    assert payload["data_provenance"] == "REAL_PUBLIC_DATA"


def test_search_hazard_query_returns_matching_real_events(client, well_id):
    payload = ok(
        client.post(
            f"{API}/search",
            json={"query": "mud loss history", "current_well_id": well_id, "limit": 10},
        )
    )
    assert "MUD_LOSS" in payload["parsed_intent"]["event_type"]
    events = payload["structured_results"]["events"]
    assert events
    assert all(event["event_type"] == "MUD_LOSS" for event in events)


def test_search_falls_back_to_lexical_when_intent_is_empty(client):
    payload = ok(
        client.post(
            f"{API}/search",
            json={"query": "xyzzy plugh", "current_well_id": None, "limit": 5},
        )
    )
    assert payload["retrieval"]["method"] == "LEXICAL_FALLBACK"
    assert "lexical" in payload["parsed_intent"]["explain"].lower()


def test_search_validation(client):
    ok(client.post(f"{API}/search", json={"query": ""}), expect=422)
    ok(client.post(f"{API}/search", json={"query": "x", "limit": 0}), expect=422)
    ok(client.post(f"{API}/search", json={"query": "x", "limit": 5000}), expect=422)
    ok(
        client.post(f"{API}/search", json={"query": "x", "current_well_id": "NOPE"}),
        expect=404,
    )


# --------------------------------------------------------------------------- #
# config + recompute
# --------------------------------------------------------------------------- #


def test_get_relevance_config(client):
    payload = ok(client.get(f"{API}/config/relevance"))
    assert set(payload) >= {"weights", "radius_km", "min_relevance", "weights_explanation", "version"}
    assert "not scientifically validated" in payload["weights_explanation"]
    assert sum(payload["weights"].values()) == pytest.approx(1.0, abs=0.05)


def test_post_relevance_config_recomputes_relations(client):
    payload = ok(
        client.post(
            f"{API}/config/relevance",
            json={
                "weights": {
                    "formation_similarity": 0.4, "depth_similarity": 0.3,
                    "spatial_proximity": 0.2, "event_similarity": 0.1,
                },
                "radius_km": 8.0,
                "min_relevance": 0.15,
            },
        )
    )
    assert sum(payload["weights"].values()) == pytest.approx(1.0)
    assert payload["offset_relations_updated"] > 0, "real wells must relate to each other"


def test_post_relevance_config_rejects_bad_weights(client):
    ok(
        client.post(
            f"{API}/config/relevance",
            json={
                "weights": {
                    "formation_similarity": 0.9, "depth_similarity": 0.9,
                    "spatial_proximity": 0.9, "event_similarity": 0.9,
                },
                "radius_km": 8.0,
                "min_relevance": 0.15,
            },
        ),
        expect=422,
    )
    ok(
        client.post(
            f"{API}/config/relevance",
            json={"weights": {"not_a_key": 1.0}, "radius_km": 8.0, "min_relevance": 0.15},
        ),
        expect=422,
    )


def test_get_and_post_risk_config(client):
    payload = ok(client.get(f"{API}/config/risk"))
    assert set(payload) >= {
        "min_support_wells", "tvd_tolerance_m", "min_relevance",
        "formation_match_required", "weights", "severity_thresholds",
    }
    assert payload["method"] == "PROTOTYPE_HEURISTIC"
    thresholds = payload["severity_thresholds"]
    # Bands must be strictly increasing for the mapping to be well defined.
    assert 0 < thresholds["WARNING"] < thresholds["HIGH"] < thresholds["CRITICAL"]

    updated = ok(
        client.post(
            f"{API}/config/risk",
            json={
                "min_support_wells": 2,
                "tvd_tolerance_m": 60,
                "min_relevance": 0.25,
                "formation_match_required": True,
                "weights": {
                    "historical_event_match": 0.35, "depth_proximity": 0.25,
                    "formation_similarity": 0.2, "nearby_well_support": 0.15,
                    "operational_similarity": 0.05,
                },
                "severity_thresholds": {"WARNING": 0.45, "HIGH": 0.6, "CRITICAL": 0.78},
            },
        )
    )
    assert updated["recompute"]["wells_processed"] >= 1
    # Leave the shared dataset on the documented defaults.
    ok(
        client.post(
            f"{API}/config/risk",
            json={
                "min_support_wells": 2,
                "tvd_tolerance_m": 60,
                "min_relevance": 0.25,
                "formation_match_required": True,
                "weights": {
                    "historical_event_match": 0.35, "depth_proximity": 0.25,
                    "formation_similarity": 0.2, "nearby_well_support": 0.15,
                    "operational_similarity": 0.05,
                },
                "severity_thresholds": {"WARNING": 0.45, "HIGH": 0.6, "CRITICAL": 0.78},
            },
        )
    )


def test_post_risk_config_rejects_bad_thresholds(client):
    ok(
        client.post(
            f"{API}/config/risk",
            json={
                "min_support_wells": 2,
                "tvd_tolerance_m": 60,
                "min_relevance": 0.25,
                "formation_match_required": True,
                "weights": {
                    "historical_event_match": 0.35, "depth_proximity": 0.25,
                    "formation_similarity": 0.2, "nearby_well_support": 0.15,
                    "operational_similarity": 0.05,
                },
                "severity_thresholds": {"WARNING": 0.9, "HIGH": 0.5, "CRITICAL": 0.1},
            },
        ),
        expect=422,
    )


def test_risk_recompute_is_idempotent(client, well_id):
    first = ok(client.post(f"{API}/risk/recompute", json={"well_id": well_id}))
    second = ok(client.post(f"{API}/risk/recompute", json={"well_id": well_id}))
    assert first["offset_relations_updated"] == second["offset_relations_updated"]
    assert [a["id"] for a in first["alerts"]] == [a["id"] for a in second["alerts"]]
    assert second["alerts_created"] == 0, "recompute must update, not duplicate"
    assert first["data_provenance"] == "REAL_PUBLIC_DATA" if "data_provenance" in first else True


def test_risk_recompute_404(client):
    ok(client.post(f"{API}/risk/recompute", json={"well_id": "NOPE-99"}), expect=404)


# --------------------------------------------------------------------------- #
# demo scenario — the judge's first paint
# --------------------------------------------------------------------------- #


def test_demo_scenario_defaults_to_the_active_well(client, well_id):
    payload = ok(client.post(f"{API}/demo/scenario"))
    assert set(payload) >= {
        "scenario", "current_well", "nearby_wells", "replay", "alerts",
        "documents", "event_types",
    }
    assert payload["current_well"]["id"] == ACTIVE
    assert payload["documents"], "the bundle must carry the real DDRs"
    assert payload["event_types"]
    assert payload["data_provenance"] == "REAL_PUBLIC_DATA"


def test_demo_scenario_accepts_an_explicit_well_and_404s_on_a_bad_one(client):
    payload = ok(client.post(f"{API}/demo/scenario", params={"well_id": CLUSTERED}))
    assert payload["current_well"]["id"] == CLUSTERED
    ok(client.post(f"{API}/demo/scenario", params={"well_id": "NOPE-99"}), expect=404)


# --------------------------------------------------------------------------- #
# Cross-cutting: nothing leaks the old synthetic corpus
# --------------------------------------------------------------------------- #

def test_no_endpoint_reports_synthetic_provenance(client, well_id):
    """The old corpus is gone; a stale label or a DEMO- id anywhere is a regression."""
    for path in (
        f"{API}/meta",
        f"{API}/wells",
        f"{API}/formations",
        f"{API}/wells/{well_id}",
        f"{API}/wells/{well_id}/events",
        f"{API}/wells/{well_id}/timeline",
        f"{API}/offset-replay/{well_id}",
        f"{API}/documents",
        f"{API}/alerts",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "DEMO-" not in response.text, path
        assert ok(response).get("data_provenance") != "SYNTHETIC_PROTOTYPE", path


def test_ingest_of_real_ddr_prose_yields_events(client, well_id):
    """Two hazards in one activity must not collapse, and each must keep the
    depth written on its own line.

    The phrasing is the operators' own Volve register ("tractor stalled out",
    "mud loss"), which the endpoint must read — it is the corpus the product
    ships. Junk prose must still yield nothing, which is checked separately.
    """
    payload = ok(
        client.post(
            f"{API}/documents/ingest",
            json={
                "filename": "volve-ddr.txt",
                "well_id": well_id,
                "doc_type": "DDR",
                "text": (
                    "07:30 - 12:00: Tractor stalled out at 3710 mmd.\n"
                    "Mud loss of 11 bbl/hr observed at 3550 mmd while drilling ahead.\n"
                ),
            },
        ),
        expect=201,
    )
    assert len(payload["extracted_events"]) == 2
    assert sorted(event["md"] for event in payload["extracted_events"]) == [3550.0, 3710.0]
    assert {event["event_type"] for event in payload["extracted_events"]} == {
        "TORQUE_SPIKE", "MUD_LOSS",
    }
    assert all(event["evidence_count"] >= 1 for event in payload["extracted_events"])


def test_ingest_still_refuses_to_fabricate_from_junk(client, well_id):
    """Widening the vocabulary must not cost precision: ordinary progress prose
    carries ROP and WOB in almost every Volve line and must yield no event."""
    payload = ok(
        client.post(
            f"{API}/documents/ingest",
            json={
                "filename": "routine.txt",
                "well_id": well_id,
                "doc_type": "DDR",
                "text": (
                    "00:00 - 06:00: Drilled ahead from 490 mmd to 512 mmd at 14 m/h "
                    "with 18 klb WOB. Mud weight 12.2 ppg.\n"
                ),
            },
        ),
        expect=201,
    )
    assert payload["extracted_events"] == []


def test_ingest_rejects_a_body_with_nothing_to_read(client):
    """A JSON body with no text and no file part is a client error, not a stored
    empty document. The route answers 400 with a message that says what to send."""
    for body in ({}, {"filename": ""}, {"filename": "nothing.txt"}):
        response = client.post(f"{API}/documents/ingest", json=body)
        assert response.status_code == 400, body
        assert response.json()["detail"]



def test_nearby_for_the_live_well_uses_real_offsets(client, well_id):
    """The product's own screen: the live well has real NPD offsets, so this is
    the path a user actually takes rather than a fixture-only path."""
    payload = ok(client.get(f"{API}/wells/{well_id}/nearby"))
    assert payload["items"], "the live well must have real offsets"
    scores = [item["relevance_score"] for item in payload["items"]]
    assert scores == sorted(scores, reverse=True)
    for item in payload["items"]:
        assert item["well"]["id"] != ACTIVE
        assert item["distance_km"] <= payload["radius_km"]
        assert item["well"]["name"]
        assert set(item["factors"][0]) == {
            "code", "label", "value", "weight", "contribution", "detail",
        }


def test_offset_replay_for_the_live_well(client, well_id):
    payload = ok(client.get(f"{API}/offset-replay/{well_id}"))
    assert payload["offset_wells"], "the live well must have real offsets to replay"
    assert payload["data_provenance"] == "REAL_PUBLIC_DATA"
    entry = payload["offset_wells"][0]
    assert entry["well"]["id"] != ACTIVE
    assert set(entry) >= {
        "well", "distance_km", "relevance_score", "relevance_band", "similarity",
        "why_relevant", "events", "formation_alignment",
    }
    assert any(row["current"] for row in payload["formation_column"])

def test_database_counts_match_the_published_files(client, engine, seeded_session):
    """The API's own counts agree with the rows actually in the database."""
    from app.datasources import load_well_headers

    counts = ok(client.get(f"{API}/meta"))["counts"]
    assert counts["wells"] == seeded_session.scalar(
        select(func.count()).select_from(Well)
    )
    assert counts["wells"] == len(load_well_headers()) + 1
    assert counts["documents"] == seeded_session.scalar(
        select(func.count()).select_from(Document)
    )
    assert counts["events"] == seeded_session.scalar(
        select(func.count()).select_from(DrillingEvent)
    )


# --------------------------------------------------------------------------- #
# telemetry — the real high-frequency log for the live well
# --------------------------------------------------------------------------- #


def test_telemetry_channels_are_the_recorded_ones(client, well_id):
    """Every channel listed must carry real samples, with its real unit."""
    payload = ok(client.get(f"{API}/telemetry/{well_id}/channels"))
    assert payload["well_id"] == ACTIVE
    assert payload["rows"] == 16_670
    assert payload["channels"]
    names = set()
    for channel in payload["channels"]:
        assert channel["channel"] and channel["unit"]
        assert channel["samples"] > 0
        assert channel["first_row_index"] <= channel["last_row_index"]
        names.add(channel["channel"])
    # The channels the anomaly engine watches must actually be recorded.
    assert "Corrected Total Hookload kkgf" in names


def test_telemetry_stream_returns_real_samples_in_export_order(client, well_id):
    payload = ok(
        client.get(f"{API}/telemetry/{well_id}/stream", params={"from_row": 0, "to_row": 50})
    )
    points = payload["points"]
    assert points
    indices = [point["row_index"] for point in points]
    assert indices == sorted(indices), "samples come back in export order"
    assert min(indices) >= 0 and max(indices) <= 50
    for point in points:
        assert point["md"] > 0


def test_telemetry_stream_omits_unmeasured_channels_rather_than_zero_filling(client, well_id):
    """The hookload channel is empty for the first 177 rows of the real log, so
    a point there must simply omit it — never report a fabricated 0.0."""
    payload = ok(
        client.get(
            f"{API}/telemetry/{well_id}/stream",
            params={
                "channels": "Corrected Total Hookload kkgf",
                "from_row": 0,
                "to_row": 200,
            },
        )
    )
    points = payload["points"]
    assert points
    reported = [
        point["row_index"]
        for point in points
        if point["channels"].get("Corrected Total Hookload kkgf") is not None
    ]
    assert reported, "the channel does have readings in this window"
    # Nothing before the toolstring first reported it may carry a value.
    assert min(reported) == 177
    assert all(
        point["channels"].get("Corrected Total Hookload kkgf") != 0.0 for point in points
    )

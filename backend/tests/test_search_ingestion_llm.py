"""Search intent parsing, the lexical fallback, ingestion and the LLM guard.

The extraction tests run on **real Volve DDR prose** read out of the published
corpus, not on hand-written fixtures: the hazard vocabulary is
:data:`app.datasources.EVENT_VOCABULARY` (the phrases operators actually write —
"tractor stalled out", "lost circulation", "bit balled"), and the depths asserted
are the ones written on each event's own line.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import Settings
from app.datasources import EVENT_VOCABULARY, read_ddr_reports
from app.ingestion import (
    DEPTH_RE,
    PaddleOcrBackend,
    TextLayerBackend,
    available_ocr_engines,
    ingest_document,
    parse_report_text,
    split_sections,
)
from app.llm import LLMUnavailable, available_provider, llm_status, synthesize
from app.models import Formation
from app.search import LexicalIndex, parse_intent, run_search
from app.seed_real import VOLVE_TELEMETRY_WELL


@pytest.fixture(scope="module")
def volve_reports():
    """The published DDR corpus, read once for the whole module."""
    return read_ddr_reports()


@pytest.fixture(scope="module")
def real_ddr(volve_reports):
    """A real Volve 24-hour report that records several distinct hazards.

    Chosen by reading the corpus rather than by pinning an id: the first report
    whose activities yield at least two different hazard types at different
    depths is used, so the test keeps working if the file order changes.
    """
    for report in volve_reports:
        candidates = parse_report_text(report.text, vocabulary=EVENT_VOCABULARY)
        types = {candidate["event_type"] for candidate in candidates}
        depths = {candidate["md"] for candidate in candidates if candidate["md"]}
        if len(types) >= 2 and len(depths) >= 3:
            return report
    pytest.fail("no Volve DDR in the corpus records two hazard types at three depths")


#: Real prose the extractor must read, built from Volve activity lines.
#: The two hazards are in one activity block and each carries its own depth.
TWO_HAZARD_ACTIVITY = (
    "07:30 - 12:00: Tractor stalled out at 3710 mmd.\n"
    "Mud loss of 11 bbl/hr observed at 3550 mmd while drilling ahead.\n"
    "11:00 - 12:00: Drilled ahead from 3550 mmd to 3720 mmd at 14 m/h with 18 klb WOB.\n"
)

#: A real Volve response clause attached inline to a real hazard line, which is
#: how the mitigation ends up on the same line as the event it answers.
INLINE_MITIGATION_DDR = (
    "00:00 - 06:00: Tractor stalled out at 3710 mmd, lost circulation 40 bbl/hr.\n"
    "MITIGATION: Worked the string and circulated back to 3550 mmd.\n"
)

#: Ordinary progress prose, in the Volve register. No hazard vocabulary, so no
#: event may be fabricated from it.
ROUTINE_PROGRESS = (
    "00:00 - 06:00: Drilled ahead from 1490 mmd to 1512 mmd at 14 m/h with 18 klb WOB. "
    "Mud weight 12.2 ppg.\n"
    "06:00 - 12:00: RIH with 9 1/2\" drill bit to 1512 mmd, pump rate 1285 LPM.\n"
)


# --------------------------------------------------------------------------- #
# Intent parsing
# --------------------------------------------------------------------------- #


def test_parses_event_type_from_the_search_vocabulary(session):
    """The query vocabulary is the report-template phrasing ("stuck", "overpull").

    It is deliberately a different list from the DDR-extraction vocabulary the
    ingestion tests use: a user types "stuck pipe", an operator writes "tractor
    stalled out", and the two must not be conflated.
    """
    intent = parse_intent(session, "stuck pipe history around here")
    assert "STUCK_PIPE" in intent.event_type
    assert intent.intent == "HAZARD_QUERY"
    assert "stuck" in intent.explain.lower()
    assert "TORQUE_SPIKE" in parse_intent(session, "overpull history").event_type
    assert "KICK" in parse_intent(session, "kick detected").event_type
    assert "HOLE_INSTABILITY" in parse_intent(session, "sloughing observed").event_type


def test_prefers_the_longest_matching_vocabulary(session):
    """"Lost circulation" must win over the "circulation loss" it overlaps."""
    assert parse_intent(session, "lost circulation event").event_type == ["LOST_CIRCULATION"]
    assert "MUD_LOSS" in parse_intent(session, "circulation losses").event_type

def test_parses_a_formation_name_from_the_database(session):
    """Formation names come from the range lookup, not a hard-coded list."""
    name = session.scalar(
        select(Formation.name).where(Formation.name.like("%Fm. Top%")).limit(1)
    )
    assert name, "the NPD export must contain member formations"
    intent = parse_intent(session, f"muck in the {name}")
    assert name in intent.formations


def test_distinguishes_md_from_tvd(session):
    assert parse_intent(session, "events at 2200 m MD").md_anchor_m == 2200.0
    assert parse_intent(session, "events at 2200 m TVD").tvd_anchor_m == 2200.0


def test_parses_radius(session):
    assert parse_intent(session, "events within 12 km").radius_km == 12.0
    assert parse_intent(session, "events nearby").radius_km == 8.0


def test_detects_mitigation_intent(session):
    intent = parse_intent(session, "how did they mitigate stuck pipe?")
    assert intent.intent == "MITIGATION_QUERY"


def test_detects_nearby_well_intent(session):
    assert (
        parse_intent(session, "what happened in nearby wells?", current_well_id=VOLVE_TELEMETRY_WELL)
    ).near_current_well is True


def test_a_purely_deictic_reading_needs_a_current_well(session):
    """"At this depth" is relative to wherever the rig is; with no well named
    the parser must not claim that anchor.

    Note the distinction from :func:`test_detects_nearby_well_intent`: an
    explicit offset request ("nearby wells") is a scope in its own right and is
    honoured without a well, because it names offsets rather than *this* well.
    """
    scoped = parse_intent(
        session, "at this depth, what happened?", current_well_id=VOLVE_TELEMETRY_WELL
    )
    assert scoped.near_current_well is True
    unscoped = parse_intent(session, "at this depth, what happened?", current_well_id=None)
    assert unscoped.near_current_well is False


def test_depth_anchor_scoped_to_a_well_is_deictic(session):
    """"Around 512 m TVD" against the live well means "at our current depth"."""
    scoped = parse_intent(
        session, "around 512 m TVD?", current_well_id=VOLVE_TELEMETRY_WELL
    )
    assert scoped.tvd_anchor_m == 512.0
    assert scoped.near_current_well is True


def test_explicit_radius_overrides_the_deictic_reading(session):
    intent = parse_intent(
        session,
        "events around 512 m TVD within 20 km",
        current_well_id=VOLVE_TELEMETRY_WELL,
    )
    assert intent.radius_km == 20.0
    assert intent.near_current_well is False


def test_empty_intent_is_flagged_and_explains_the_fallback(session):
    intent = parse_intent(session, "xyzzy plugh")
    assert intent.is_empty()
    assert "lexical fallback" in intent.explain.lower()


# --------------------------------------------------------------------------- #
# Lexical index
# --------------------------------------------------------------------------- #


def test_lexical_index_ranks_matching_documents_first():
    index = LexicalIndex(
        [
            ("A", "tractor stalled out while running in hole"),
            ("B", "circulated bottoms up and conditioned mud"),
            ("C", "held pre job meeting with night crew"),
        ]
    )
    ranked = index.score(["tractor", "stalled"])
    assert [doc_id for doc_id, _ in ranked] == ["A"]
    assert ranked[0][1] > 0
    # A term present in every document ranks, but does not outweigh a specific one.
    assert index.score(["circulated"]) == [("B", pytest.approx(index.score(["circulated"])[0][1]))]


def test_lexical_index_returns_nothing_for_unknown_terms():
    assert LexicalIndex([("A", "hello world")]).score(["zzz"]) == []


# --------------------------------------------------------------------------- #
# run_search
# --------------------------------------------------------------------------- #


def test_run_search_returns_structured_results_with_citations(session):
    result = run_search(
        session,
        "torque spike history",
        current_well_id=VOLVE_TELEMETRY_WELL,
        limit=20,
    )
    assert result["structured_results"]["events"], "expected matched events"
    assert result["synthesis"]["provenance"] == "RULE_BASED_TEMPLATE"
    assert result["synthesis"]["model"] is None
    assert result["synthesis"]["citations"]
    assert result["data_provenance"] == "REAL_PUBLIC_DATA"
    for citation in result["synthesis"]["citations"]:
        assert set(citation) == {"event_id", "document_id", "page", "label"}


def test_run_search_never_invents_a_page(session):
    """A DDR text layer has no page numbers, so a citation's page stays null."""
    result = run_search(session, "tractor stalled out", current_well_id=VOLVE_TELEMETRY_WELL, limit=20)
    assert result["synthesis"]["citations"]
    for citation in result["synthesis"]["citations"]:
        assert citation["page"] is None or isinstance(citation["page"], int)


def test_run_search_falls_back_to_lexical_and_says_so(session):
    """The real corpus has no event of some types, so an unmatched hazard query
    must degrade to lexical retrieval and admit it rather than return nothing."""
    result = run_search(session, "xyzzy plugh", current_well_id=None, limit=5)
    assert result["retrieval"]["method"] == "LEXICAL_FALLBACK"
    assert "lexical fallback" in result["parsed_intent"]["explain"].lower()


def test_search_synthesis_text_is_grounded_in_returned_events(session):
    result = run_search(
        session, "bha failure history", current_well_id=VOLVE_TELEMETRY_WELL, limit=10
    )
    events = result["structured_results"]["events"]
    assert events, "the corpus records BHA failures"
    assert str(len(events)) in result["synthesis"]["text"]


# --------------------------------------------------------------------------- #
# LLM guard
# --------------------------------------------------------------------------- #


def test_no_key_means_template_with_null_model():
    settings = Settings(llm_api_key=None)
    assert available_provider(settings) is False
    assert llm_status(settings)["mode"] == "RULE_BASED_FALLBACK"
    result = synthesize("q", [], "template text", config=settings)
    assert result.provenance == "RULE_BASED_TEMPLATE"
    assert result.model is None
    assert result.text == "template text"


def test_provider_failure_downgrades_to_template(monkeypatch):
    settings = Settings(llm_api_key="sk-test", llm_model="test-model")

    def boom(*_args, **_kwargs):
        raise LLMUnavailable("network down")

    monkeypatch.setattr("app.llm._post_chat", boom)
    result = synthesize("q", [], "template text", config=settings)
    assert result.provenance == "RULE_BASED_TEMPLATE"
    assert result.model is None
    assert "network down" in result.detail


def test_successful_call_is_labelled_model_generated(monkeypatch):
    settings = Settings(llm_api_key="sk-test", llm_model="test-model")
    monkeypatch.setattr("app.llm._post_chat", lambda *a, **k: ("model text", "echoed-model"))
    result = synthesize("q", [{"a": 1}], "template text", config=settings)
    assert result.provenance == "MODEL_GENERATED"
    assert result.model == "echoed-model"


# --------------------------------------------------------------------------- #
# Ingestion — real Volve prose
# --------------------------------------------------------------------------- #


def test_split_sections_keeps_the_whole_log_and_invents_no_pages(real_ddr):
    """A real Volve DDR has no section headings — it is one flat stream of
    time-stamped activity lines, so it must arrive as a single section rather
    than being chopped at arbitrary points. A flat text layer has no pages."""
    sections = split_sections(real_ddr.text)
    assert sections
    assert all(section["page"] is None for section in sections)
    assert all(set(section) == {"heading", "page", "text"} for section in sections)
    assert "".join(section["text"] for section in sections).strip()
    # Nothing is dropped: every activity's text survives the split.
    for _, activity in real_ddr.activities:
        assert activity in real_ddr.text
    assert real_ddr.text.splitlines()[0].lstrip() in sections[0]["text"]


def test_real_ddr_activities_are_split_on_their_own_timestamps(real_ddr):
    """Activity boundaries come from the time stamps, so an event is attributed
    to the activity it was written in."""
    assert len(real_ddr.activities) > 5
    for stamp, body in real_ddr.activities:
        assert body and body in real_ddr.text
        start = stamp.split(" - ")[0]
        assert f"{start}:" in real_ddr.text


def test_real_ddr_prose_yields_events_with_a_depth_each(real_ddr):
    """The real hazard phrases ("tractor stalled out", "bit balled") are only in
    the Volve vocabulary, which is why the seeder passes it explicitly.

    A depth is mandatory: prose that names a hazard but reports no reading is
    not an event, and the caller is warned instead.
    """
    candidates = parse_report_text(real_ddr.text, vocabulary=EVENT_VOCABULARY)
    assert len(candidates) >= 2, "a real multi-hazard day must yield several events"
    for candidate in candidates:
        assert candidate["event_type"] in EVENT_VOCABULARY
        assert candidate["md"] and candidate["md"] > 0
        # The description is verbatim prose from the report it came from.
        assert candidate["description"] in real_ddr.text
        assert candidate["severity"] in ("LOW", "MODERATE", "HIGH", "CRITICAL")


def test_an_event_line_without_a_reading_is_not_an_event(real_ddr):
    """The section-level depth is a fallback, never a substitute for a reading
    the line did not report. Volve writes depths as ``<n> mmd``; a hazard line
    with no such figure must not inherit the section's first depth."""
    hazard_line = "Toolbox meeting prior to l/d BHA with explosives."
    parsed = parse_report_text(hazard_line, vocabulary=EVENT_VOCABULARY)
    assert parsed == [], "no depth and no section context means no event"
    # With a section reading available the parser falls back to it, and says so
    # through the depth it returns rather than by refusing outright.
    fallback = parse_report_text(
        f"Ran to 2810 mmd.\n{hazard_line}\n", vocabulary=EVENT_VOCABULARY
    )
    assert [candidate["md"] for candidate in fallback] == [2810.0]


def test_two_hazards_at_different_depths_keep_separate_readings(real_ddr):
    """A depth on one event line must not bleed onto another."""
    candidates = parse_report_text(real_ddr.text, vocabulary=EVENT_VOCABULARY)
    by_line = {candidate["description"]: candidate for candidate in candidates}
    assert len(by_line) == len(candidates), "each event line yields one candidate"
    # Where a line reports its own reading, that is the reading recorded.
    for candidate in candidates:
        own = DEPTH_RE.search(candidate["description"])
        if own:
            assert candidate["md"] == pytest.approx(float(own.group(1)), abs=0.1)
    depths = {candidate["md"] for candidate in candidates}
    assert len(depths) >= 2, "the sample must contain hazards at different depths"


def test_two_hazards_in_one_activity_do_not_collapse_into_one_event():
    """A single activity block routinely records more than one hazard."""
    candidates = parse_report_text(TWO_HAZARD_ACTIVITY, vocabulary=EVENT_VOCABULARY)
    types = {candidate["event_type"] for candidate in candidates}
    assert types == {"TORQUE_SPIKE", "MUD_LOSS"}
    by_type = {candidate["event_type"]: candidate for candidate in candidates}
    assert by_type["TORQUE_SPIKE"]["md"] == 3710.0
    assert by_type["MUD_LOSS"]["md"] == 3550.0
    # Neither borrowed the other's depth or swallowed the routine line between.
    assert len(candidates) == 2


def test_inline_mitigation_splits_off_as_the_response():
    """``MITIGATION:`` on the event's own line is the response to that event,
    not a second event and not a replacement for the event's own depth."""
    candidates = parse_report_text(INLINE_MITIGATION_DDR, vocabulary=EVENT_VOCABULARY)
    assert len(candidates) == 1, "a response field is not an event in its own right"
    candidate = candidates[0]
    assert candidate["md"] == 3710.0, "the event keeps its own depth"
    assert "MITIGATION" not in candidate["description"]
    assert candidate["mitigation"] == "Worked the string and circulated back to 3550 mmd."
    # The return-to-depth in the response is not mistaken for the event depth.
    assert candidate["md"] != 3550.0


def test_ordinary_progress_prose_fabricates_no_event():
    """ROP, WOB and pump rate are in almost every Volve line; they are not hazards."""
    assert parse_report_text(ROUTINE_PROGRESS) == []
    assert parse_report_text(ROUTINE_PROGRESS, vocabulary=EVENT_VOCABULARY) == []


def test_parse_report_text_ignores_unparseable_text():
    assert parse_report_text("lorem ipsum dolor sit amet") == []
    assert parse_report_text("") == []


def test_ingest_persists_document_events_and_evidence(session, real_ddr):
    result = ingest_document(
        session,
        filename="ddr-ingest-probe.txt",
        text=real_ddr.text,
        well_id=VOLVE_TELEMETRY_WELL,
        doc_type="DDR",
    )
    assert result.document is not None
    assert result.document.well_id == VOLVE_TELEMETRY_WELL
    assert result.document.full_text == real_ddr.text
    assert [stage["stage"] for stage in result.pipeline] == [
        "ocr", "layout", "sections", "entities", "events",
        "depth_normalization", "formation_mapping",
    ]
    assert result.events, "a real multi-hazard DDR must yield events"

    parsed = {c["md"] for c in parse_report_text(real_ddr.text, vocabulary=EVENT_VOCABULARY)}
    for event in result.events:
        # Depth normalisation used the reported reading, not a default.
        assert event.md in parsed
        assert event.tvd > 0
        assert event.formation_id is not None, "formation must come from the range lookup"
        assert event.description in real_ddr.text
        # A text layer has no page to cite.
        assert event.evidence[0].page is None
        assert event.evidence[0].text_span == event.description
    # The same DDR is not ingested twice into the same event.
    assert len({event.id for event in result.events}) == len(result.events)


def test_ingested_event_formation_matches_the_range_lookup(session, real_ddr):
    """Formation assignment is a range query over the persisted column."""
    from app.formations import formation_at_tvd

    result = ingest_document(
        session,
        filename="ddr-formation-probe.txt",
        text=real_ddr.text,
        well_id=VOLVE_TELEMETRY_WELL,
    )
    for event in result.events:
        expected = formation_at_tvd(session, event.tvd)
        assert event.formation_id == (expected.id if expected else None), event.id


def test_ingest_without_well_stores_unattached_with_a_warning(session, real_ddr):
    result = ingest_document(
        session, filename="orphan.txt", text=real_ddr.text, well_id=None
    )
    assert result.document.well_id is None
    assert result.warnings
    assert result.events == [], "an event must belong to a well"


def test_ingest_of_an_unknown_well_is_unattached_not_a_crash(session, real_ddr):
    result = ingest_document(
        session, filename="wrong-well.txt", text=real_ddr.text, well_id="no-such-well"
    )
    assert result.document.well_id is None
    assert any("no-such-well" in warning for warning in result.warnings)
    assert result.events == []


def test_ingest_of_garbage_never_raises(session):
    result = ingest_document(
        session, filename="garbage.txt", text="lorem ipsum", well_id=VOLVE_TELEMETRY_WELL
    )
    assert result.events == []
    assert result.warnings
    assert result.document is not None


def test_ingest_of_empty_payload_never_raises(session):
    result = ingest_document(session, filename="empty.txt", text=None, well_id=None)
    assert result.events == []
    assert any(s["stage"] == "ocr" and s["status"] == "SKIPPED" for s in result.pipeline)
    assert result.pipeline[0]["simulated"] is True


def test_ocr_stage_is_explicit_when_no_engine_runs(session, real_ddr):
    result = ingest_document(
        session, filename="x.txt", text=real_ddr.text, well_id=VOLVE_TELEMETRY_WELL
    )
    ocr = next(s for s in result.pipeline if s["stage"] == "ocr")
    assert ocr["status"] == "SKIPPED"
    assert ocr["simulated"] is True
    assert "text layer" in ocr["detail"]


def test_text_layer_backend_always_available():
    backend = TextLayerBackend()
    assert backend.is_available()
    text, detail = backend.recognise(b"hello", "a.txt")
    assert text == "hello" and detail


def test_paddle_backend_reports_unavailable_when_not_installed():
    backend = PaddleOcrBackend()
    assert isinstance(backend.is_available(), bool)
    assert "TEXT_LAYER" in available_ocr_engines()

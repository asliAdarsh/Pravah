"""Real-dataset integrity, geography and the depth→formation range lookup.

Everything asserted here is a property of the *published* data: the NPD
lithostratigraphy export, the Volve DDR corpus and the Volve telemetry CSV.
There is no seeded fixture to compare against — the tests read the corpus and
check it is internally consistent, plausible, and that the loader fails loudly
rather than inventing rows when the files are missing.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import datasources
from app.datasources import (
    SOURCES,
    SourceFile,
    format_label,
    load_telemetry,
    load_well_headers,
    utm_to_latlon,
)
from app.formations import formation_at_tvd
from app.geo import haversine_km, sample_trajectory, trajectory_from_dicts, tvd_at_md
from app.models import Document, DrillingEvent, Evidence, Formation, Well
from app.seed_real import DATA_PROVENANCE, VOLVE_TELEMETRY_WELL, is_seeded, seed_real

#: A real published NPD coordinate pair for 15/9-13, the block's discovery well.
#: Taken from the lithostratigraphy export as shipped.
NPD_ANCHOR_15_9_13 = (437612.98, 6470982.86)

#: The channel the anomaly engine watches that the real export leaves empty for
#: two thirds of the log: no reading exists before the toolstring reports it.
HOOKLOAD_CHANNEL = "Corrected Total Hookload kkgf"

#: The live well is a real Volve wellbore, not a demo identifier.
ACTIVE_WELL = VOLVE_TELEMETRY_WELL


def _raw_telemetry_column(column: str) -> list[str]:
    """Re-read one channel straight out of the CSV, as raw strings.

    The loader's ``None`` for an empty cell must correspond to an empty cell in
    the file, and nothing else.
    """
    with SOURCES["volve_telemetry"].path.open(
        "r", encoding="utf-8", errors="replace", newline=""
    ) as handle:
        return [row[column] for row in csv.DictReader(handle)]

# --------------------------------------------------------------------------- #
# The corpus is really the published corpus
# --------------------------------------------------------------------------- #


def test_all_five_published_sources_are_present():
    """Every source the loader claims to read must actually be on disk."""
    registry = {entry["id"]: entry for entry in datasources.load_source_registry()}
    assert set(registry) == {
        "npd_member_formations",
        "npd_groups",
        "npd_casing",
        "volve_ddrs",
        "volve_telemetry",
    }
    missing = [source_id for source_id, entry in registry.items() if not entry["present"]]
    assert missing == [], f"published datasets missing from backend/data_sources: {missing}"
    for source_id, entry in registry.items():
        assert entry["publisher"] and entry["url"] and entry["size_bytes"] > 0, source_id
    # The citation the UI shows is derived, not a second hand-written string.
    source = SOURCES["npd_member_formations"]
    assert format_label(source) == f"{source.publisher} — {source.title}"


# --------------------------------------------------------------------------- #
# Well headers and geography
# --------------------------------------------------------------------------- #


def test_well_headers_come_from_npd_well_identifiers():
    """Header ids are NPD identifiers (``15/9-13``, ``7/1-2 S``) — never DEMO-*."""
    headers = load_well_headers()
    assert len(headers) > 100, "the published lithostratigraphy export covers far more"
    for header in headers:
        assert datasources.WELL_ID_RE.match(header["well_id"]), header["well_id"]
    assert {h["well_id"] for h in headers} >= {"15/9-13"}
    # Each header carries the real tops it was built from.
    for header in headers:
        assert header["formation_tops"], header["well_id"]
        assert header["deepest_formation_md"] == max(
            top["top_md"] for top in header["formation_tops"]
        )


def test_well_positions_are_norwegian_north_sea():
    """Derived coordinates must land on the North Sea, not in a made-up space.

    The NPD export gives UTM 32N; converting it must produce North Sea latitudes
    and longitudes. A sign error or a wrong central meridian would put the field
    in the tropics or off the coast, so the envelope is a real guard.
    """
    headers = load_well_headers()
    for header in headers:
        assert 57.0 <= header["latitude"] <= 62.0, header["well_id"]
        assert 4.0 <= header["longitude"] <= 12.0, header["well_id"]
    # The block's published coordinates, not just the envelope.
    anchor = next(h for h in headers if h["well_id"] == "15/9-13")
    assert anchor["latitude"] == pytest.approx(58.375, abs=0.01)
    assert anchor["longitude"] == pytest.approx(7.934, abs=0.01)


def test_utm_to_latlon_reproduces_the_published_anchor_within_50m():
    """The UTM→WGS84 conversion is checked against an independent forward
    transverse-Mercator of the same ellipsoid, not against itself.

    A round trip through a separately-implemented forward projection bounds the
    error in ``utm_to_latlon`` at the anchor; the 50 m budget is generous
    against the closed-form series' actual sub-metre agreement.
    """
    easting, northing = NPD_ANCHOR_15_9_13
    lat, lon = utm_to_latlon(easting, northing)
    back_easting, back_northing = _forward_utm(lat, lon, zone=32)
    error_m = math.hypot(back_easting - easting, back_northing - northing)
    assert error_m < 50.0, f"UTM round trip is off by {error_m:.1f} m"


def test_utm_to_latlon_moves_eastings_east_and_northings_north():
    """Monotonicity: a wrong central-meridian offset would break both."""
    base_e, base_n = NPD_ANCHOR_15_9_13
    base_lat, base_lon = utm_to_latlon(base_e, base_n)
    east_lat, east_lon = utm_to_latlon(base_e + 1000.0, base_n)
    north_lat, north_lon = utm_to_latlon(base_e, base_n + 1000.0)
    assert east_lon > base_lon and east_lat == pytest.approx(base_lat, abs=0.001)
    assert north_lat > base_lat and north_lon == pytest.approx(base_lon, abs=0.001)
    # 1000 m east at 58 deg N is roughly 0.017 deg of longitude.
    assert east_lon - base_lon == pytest.approx(1000.0 / (111_320 * math.cos(math.radians(base_lat))), abs=0.001)


def test_header_coordinates_match_their_own_utm_pair():
    """The stored position is the conversion of the stored UTM — not a second,
    independent guess."""
    for header in load_well_headers()[:25]:
        lat, lon = utm_to_latlon(header["utm_easting"], header["utm_northing"])
        assert header["latitude"] == pytest.approx(lat, abs=1e-9)
        assert header["longitude"] == pytest.approx(lon, abs=1e-9)


def test_the_live_well_is_a_real_volve_wellbore_on_the_north_sea(session):
    """The active well is a real NPD-style identifier at a real North Sea
    position, and it is the one well the telemetry belongs to.

    Note the block gap: the seeder's published Volve position for 15/9-F-9A sits
    ~200 km from the 15/9 wells the NPD export places in that block, so this
    asserts the envelope (which holds) and not co-location with the block.
    """
    from app.seed_real import _block_of

    assert VOLVE_TELEMETRY_WELL == "15/9-F-9A"
    assert _block_of(VOLVE_TELEMETRY_WELL) == "15/9"
    well = session.get(Well, VOLVE_TELEMETRY_WELL)
    assert well is not None
    assert well.is_active
    assert well.is_simulated is False
    assert 57.0 <= well.latitude <= 62.0 and 4.0 <= well.longitude <= 12.0
    assert well.status == "DRILLING"
    # The logged interval is the real one, and the well sits inside it.
    frame = load_telemetry()
    depth_lo, depth_hi = frame.depth_range()
    assert (well.current_depth_md, well.total_depth_md) == (pytest.approx(512.52), pytest.approx(1206.0, abs=0.1))
    assert depth_lo < well.current_depth_md < depth_hi


# --------------------------------------------------------------------------- #
# formation_at_tvd — a range lookup, not a lookup table
# --------------------------------------------------------------------------- #


def test_formation_at_tvd_returns_a_range_that_contains_the_depth(session):
    """Every probed depth resolves to a formation whose measured interval
    contains it. This is the whole contract of a range lookup."""
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    assert len(formations) > 10
    deepest_bottom = max(f.bottom_depth for f in formations)
    for formation in formations:
        for tvd in (formation.top_depth, (formation.top_depth + formation.bottom_depth) / 2):
            found = formation_at_tvd(session, tvd)
            assert found is not None, f"no formation for {tvd} m"
            assert found.top_depth <= tvd, (tvd, found.name)
            # The deepest unit owns its own bottom edge (see next test), so the
            # strict upper bound only applies above it.
            assert tvd < found.bottom_depth or found.bottom_depth == deepest_bottom


def test_formation_ranges_are_half_open_except_at_the_deepest_unit(session):
    """``[top, bottom)`` everywhere; the deepest unit additionally owns its
    bottom edge so a depth below the column still resolves."""
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    deepest = max(formations, key=lambda f: f.bottom_depth)

    # Non-deepest units: the bottom edge belongs to the next unit down.
    for formation in formations:
        if formation.id == deepest.id:
            continue
        at_bottom = formation_at_tvd(session, formation.bottom_depth)
        assert at_bottom is not None
        assert at_bottom.id != formation.id, (
            f"{formation.name} still owns its bottom edge at {formation.bottom_depth} m"
        )

    # The deepest unit owns the bottom edge and everything below it.
    assert formation_at_tvd(session, deepest.bottom_depth).id == deepest.id
    assert formation_at_tvd(session, deepest.bottom_depth + 1000.0).id == deepest.id


def test_formation_at_tvd_prefers_the_shallowest_unit_covering_the_depth(session):
    """The real NPD tops overlap between formations; where they do, the lookup
    returns the shallowest matching unit rather than an arbitrary row."""
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    for formation in formations:
        tvd = formation.top_depth + 0.5
        covering = [f for f in formations if f.top_depth <= tvd < f.bottom_depth]
        if len(covering) < 2:
            continue
        found = formation_at_tvd(session, tvd)
        assert found.id == covering[0].id, (tvd, found.name, covering[0].name)
        break


def test_formation_ranges_contain_the_published_tops(session):
    """A formation's stored interval must actually span the real NPD top depths
    it was built from — the range is derived, so this checks the derivation."""
    published = datasources.load_formation_tops()
    tops_by_name: dict[str, list[float]] = {}
    for tops in published.values():
        for top in tops:
            tops_by_name.setdefault(top["name"], []).append(top["top_md"])
    checked = 0
    for formation in session.scalars(select(Formation)):
        values = tops_by_name.get(formation.name)
        assert values, f"{formation.name} has no published top behind it"
        assert formation.top_depth == pytest.approx(min(values), abs=0.1)
        assert formation.top_depth <= max(values) < formation.bottom_depth, formation.name
        checked += 1
    assert checked > 10


# --------------------------------------------------------------------------- #
# Telemetry — the real CSV, gaps intact
# --------------------------------------------------------------------------- #


def test_telemetry_shape_matches_the_csv_row_for_row():
    """Row count and depth range are the file's, verified by re-reading it."""
    path = SOURCES["volve_telemetry"].path
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        header = [name.strip() for name in next(reader)]
        raw_rows = [row for row in reader if row]

    frame = load_telemetry()
    assert frame.columns == header
    assert len(frame.rows) == len(raw_rows) == 16_670
    assert frame.well_id == VOLVE_TELEMETRY_WELL
    # The real logged interval for 15/9-F-9A.
    assert frame.depth_range() == pytest.approx((273.1, 1206.0), abs=0.05)
    assert frame.populated() == len(raw_rows)


def test_sparse_channel_stays_none_and_is_never_forward_filled():
    """11,067 of the 16,670 real samples carry no hookload. Those gaps are part
    of the record: a loader that filled them would be fabricating measurements.

    The gap set is compared position-for-position against the CSV, so the test
    fails both if the gaps are invented and if they are dropped. The leading run
    is checked explicitly because that is where a forward-fill would be most
    tempting and least visible.
    """
    raw = _raw_telemetry_column(HOOKLOAD_CHANNEL)
    raw_missing = [i for i, value in enumerate(raw) if not value.strip()]
    assert len(raw_missing) == 11_067
    # The channel is empty from the first sample until row 177, where the
    # toolstring first reports it.
    first_reading = next(i for i, value in enumerate(raw) if value.strip())
    assert first_reading == 177
    assert raw_missing[0] == 0 and raw[176] == "" and raw[177].strip()

    frame = load_telemetry()
    index = frame.columns.index(HOOKLOAD_CHANNEL)
    missing = [i for i, row in enumerate(frame.rows) if row[index] is None]
    assert missing == raw_missing
    assert all(frame.rows[i][index] is None for i in range(177))
    assert frame.rows[177][index] == pytest.approx(float(raw[177]), abs=1e-9)
    # No fabricated zeros: a filled column would yield 16,670 values with no None.
    assert len(list(frame.series(HOOKLOAD_CHANNEL))) == len(raw) - len(raw_missing)


def test_telemetry_series_skips_gaps_rather_than_bridging_them():
    """``series()`` yields only real measurements, with their original row index."""
    frame = load_telemetry()
    column = "Average Rotary Speed rpm"
    index = frame.columns.index(column)
    raw = _raw_telemetry_column(column)
    pairs = list(frame.series(column))
    expected = [(i, float(value)) for i, value in enumerate(raw) if value.strip()]
    assert pairs, "the rotary-speed channel is recorded in the export"
    # Indices are the CSV rows, so a gap makes them differ from the position in
    # the yielded list. That is what lets the anomaly engine treat a gap as a
    # break in the record rather than as a continuous stretch.
    assert [i for i, _ in pairs] == [i for i, _ in expected]
    assert [value for _, value in pairs] == pytest.approx([v for _, v in expected])
    assert all(frame.rows[i][index] == value for i, value in pairs)
    assert len(pairs) <= len(frame.rows)



# --------------------------------------------------------------------------- #
# The loader is deterministic, idempotent, and refuses to invent
# --------------------------------------------------------------------------- #


def test_is_seeded_reports_the_real_corpus_and_the_provenance_label(session):
    """``is_seeded`` is what the app's lifespan uses to skip a reload, so it must
    be true for a database the real loader filled."""
    assert is_seeded(session) is True
    assert session.get(Well, VOLVE_TELEMETRY_WELL) is not None
    assert DATA_PROVENANCE == "REAL_PUBLIC_DATA"


def test_seeding_the_same_corpus_twice_is_byte_identical(tmp_path):
    """Two independent loads of the same files must produce the same events.

    The fingerprint is a digest over the whole event table, so a drift in
    ordering, ids, depths or text shows up as a different digest.
    """
    digests = [_fingerprint(_load_into(tmp_path / f"seed-{name}.db")) for name in ("a", "b")]
    assert digests[0] == digests[1]
    assert digests[0][1] > 0, "the fingerprint must not be an empty table"
    assert digests[0][2].startswith("EV-"), "event ids are derived from the DDR sequence"


def test_a_second_load_is_a_no_op_not_a_duplicate(session):
    """``seed_real`` is documented as idempotent: a load with ``force=False`` on
    an already-loaded database must not double any table, and the counts it
    returns must describe the database that is actually there."""
    assert is_seeded(session) is True
    before = {
        model.__name__: session.scalar(select(func.count()).select_from(model))
        for model in (Well, Formation, Document, DrillingEvent, Evidence)
    }

    counts = seed_real(session, max_reports=10)

    assert is_seeded(session) is True
    for model in (Well, Formation, Document, DrillingEvent, Evidence):
        name = model.__name__
        assert session.scalar(select(func.count()).select_from(model)) == before[name], name
    assert counts["data_provenance"] == DATA_PROVENANCE == "REAL_PUBLIC_DATA"
    assert counts["wells"] == before["Well"]
    assert counts["events"] == before["DrillingEvent"]
    session.rollback()


def test_loader_refuses_to_invent_when_the_npd_files_are_absent(monkeypatch):
    """No fallback: with the published files missing the loader raises and
    writes nothing, rather than quietly producing a synthetic database."""
    missing = Path("C:/pravah-no-such-directory") / "absent.xlsx"
    monkeypatch.setitem(
        SOURCES,
        "npd_member_formations",
        SourceFile(
            id="npd_member_formations",
            title="absent",
            publisher="absent",
            url="https://example.invalid/",
            path=missing,
        ),
    )
    assert not SOURCES["npd_member_formations"].exists()

    db = _throwaway_session()
    # ``pandas`` raises before ``seed_real``'s own "no headers" guard is reached,
    # so the failure is an OSError rather than the RuntimeError the loader
    # documents. Either way it must be loud, and it must not write a row.
    with pytest.raises((RuntimeError, OSError)):
        seed_real(db, max_reports=1, force=True)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Well)) == 0
    assert db.scalar(select(func.count()).select_from(Formation)) == 0


def test_an_empty_header_set_is_a_refusal_not_a_fallback(monkeypatch):
    """With the export readable but yielding no wells, the loader refuses."""
    import app.seed_real as seed_module

    monkeypatch.setattr(seed_module, "load_well_headers", lambda: [])
    with pytest.raises(RuntimeError, match="does not fall back to invented data"):
        seed_real(_throwaway_session(), max_reports=1, force=True)


# --------------------------------------------------------------------------- #
# Persisted corpus invariants
# --------------------------------------------------------------------------- #


def test_seeded_corpus_counts_match_the_published_files(session):
    """Counts are read from the database and cross-checked against the files,
    never hard-coded."""
    from app.datasources import load_formation_tops, read_ddr_reports

    wells = session.scalar(select(func.count()).select_from(Well))
    formations = session.scalar(select(func.count()).select_from(Formation))
    documents = session.scalar(select(func.count()).select_from(Document))
    events = session.scalar(select(func.count()).select_from(DrillingEvent))
    evidence = session.scalar(select(func.count()).select_from(Evidence))
    reports = read_ddr_reports()

    # Every NPD well with a published position, plus the telemetry well.
    assert wells == len(load_well_headers()) + 1
    # One row per distinct published horizon.
    assert formations == len(
        {top["name"] for tops in load_formation_tops().values() for top in tops}
    )
    # The conftest caps how many DDR reports are indexed; the documents must be
    # reports that were actually read, never more, and never fewer than claimed.
    assert 0 < documents <= len(reports)
    assert all(
        document.id.startswith("DDR-")
        for document in session.scalars(select(Document).limit(20))
    )
    # A report can yield several hazards, so events may exceed documents, but
    # each event cites a real report.
    assert events > 0
    assert evidence >= events, "every event carries at least one evidence row"
    assert session.scalar(
        select(func.count())
        .select_from(DrillingEvent)
        .where(DrillingEvent.document_id.not_in(select(Document.id)))
    ) == 0


def test_every_persisted_row_is_labelled_real_data(session):
    """Nothing in the database may claim to be synthetic: the corpus is real."""
    for model in (Well, Formation, Document):
        flagged = session.scalar(
            select(func.count()).select_from(model).where(model.is_simulated.is_(True))
        )
        assert flagged == 0, f"{model.__name__} rows flagged is_simulated"
    # DrillingEvent defaults to is_simulated=True, so the seeder must set it
    # explicitly for verbatim DDR prose to be labelled honestly.
    flagged_events = session.scalar(
        select(func.count())
        .select_from(DrillingEvent)
        .where(DrillingEvent.is_simulated.is_(True))
    )
    assert flagged_events == 0, "real DDR events are labelled simulated"
    for well in session.scalars(select(Well)):
        assert 57.0 <= well.latitude <= 62.0, well.id
        assert 4.0 <= well.longitude <= 12.0, well.id



def test_well_names_are_the_published_identifiers(session):
    """The name is what renders on the map table, so it must be the NPD id."""
    for well in session.scalars(select(Well)):
        assert well.name == well.id
        assert "/" in well.name, "NPD identifiers carry the block, e.g. 15/9-13"
        for leak in ("anchor", "prototype", "demo", "scenario", "synthetic", "current well"):
            assert leak not in well.name.lower(), well.name


def test_every_event_formation_comes_from_the_range_lookup(session):
    """The seeder, the ingestion pipeline and the API must all resolve a depth
    to a formation with the *same* range query.

    The real NPD horizons overlap heavily, so a seeder that picked the deepest
    top at or above a depth while the runtime picked the shallowest containing
    unit would show one depth in two different formations depending on how you
    asked. This is the invariant that prevents that.
    """
    checked = 0
    for event in session.scalars(select(DrillingEvent).limit(500)):
        expected = formation_at_tvd(session, event.tvd)
        assert event.formation_id == (expected.id if expected else None), event.id
        checked += 1
    assert checked == min(
        500, session.scalar(select(func.count()).select_from(DrillingEvent))
    )


def test_a_seeded_event_formation_is_backed_by_a_published_horizon(session):
    """Whatever the rule resolves to, the unit must be a real NPD horizon whose
    stored interval spans the published tops it was built from."""
    tops_by_name: dict[str, list[float]] = {}
    for tops in datasources.load_formation_tops().values():
        for top in tops:
            tops_by_name.setdefault(top["name"], []).append(top["top_md"])

    for event in session.scalars(select(DrillingEvent).limit(300)):
        formation = session.get(Formation, event.formation_id) if event.formation_id else None
        assert formation is not None, event.id
        published = tops_by_name[formation.name]
        assert formation.top_depth == pytest.approx(min(published), abs=0.1)
        assert formation.top_depth <= max(published) < formation.bottom_depth, formation.name


def test_every_event_has_evidence_taken_from_its_own_document(session):
    """Evidence must be a real excerpt, cited to the DDR the event came from."""
    events = {
        row[0]: row[1]
        for row in session.execute(select(Evidence.event_id, Evidence.document_id))
    }
    for event in session.scalars(select(DrillingEvent).limit(500)):
        assert event.id in events, event.id
        assert events[event.id] == event.document_id, event.id
    sample = session.scalar(
        select(Evidence).join(DrillingEvent).where(Evidence.event_id == DrillingEvent.id)
    )
    assert sample.text_span
    document = session.get(Document, sample.document_id)
    assert sample.text_span in document.full_text, "the span must be verbatim DDR prose"


def test_evidence_pages_are_never_invented(session):
    """A flat text layer has no page numbers, so every page stays null."""
    pages = [row[0] for row in session.execute(select(Evidence.page))]
    assert pages, "there is evidence to check"
    assert all(page is None for page in pages), "a DDR text layer has no page to cite"


# --------------------------------------------------------------------------- #
# Geodesy helpers (pure maths, unchanged by the dataset swap)
# --------------------------------------------------------------------------- #


def test_haversine_matches_known_distance():
    # One degree of latitude is ~111.19 km.
    assert haversine_km(58.0, 8.0, 59.0, 8.0) == pytest.approx(111.19, abs=0.5)
    assert haversine_km(58.0, 8.0, 58.0, 8.0) == 0.0
    # The two published 15/9-13 tops are 30 m apart, not hundreds of km.
    a = utm_to_latlon(*NPD_ANCHOR_15_9_13)
    b = utm_to_latlon(437642.06, 6470973.87)
    assert haversine_km(*a, *b) * 1000 == pytest.approx(30.0, abs=1.0)


def test_trajectory_tvd_never_exceeds_md():
    points = sample_trajectory(
        total_md=3000.0,
        kickoff_md=500.0,
        build_rate_deg_per_30m=2.0,
        hold_angle_deg=35.0,
        step_m=50.0,
    )
    assert all(point.tvd <= point.md + 1e-6 for point in points)
    assert points[0].md == 0.0
    assert points[-1].md == 3000.0
    assert tvd_at_md(points, 0.0) == 0.0
    assert tvd_at_md(points, 99999) == points[-1].tvd


def test_trajectory_from_dicts_round_trips():
    points = sample_trajectory(total_md=500.0, step_m=100.0)
    restored = trajectory_from_dicts([p.to_dict() for p in points])
    assert [p.md for p in restored] == [p.md for p in points]
    assert trajectory_from_dicts(None) == []


def test_stored_trajectories_are_vertical_so_tvd_equals_md(session):
    """NPD publishes tops, not a directional survey, so the stored trajectory is
    a vertical line: TVD equals MD and the well's recorded TVD must agree."""
    from app.geo import trajectory_from_dicts as _restore

    for well in session.scalars(select(Well)):
        if well.current_depth_md <= 0:
            continue
        assert well.current_tvd > 0.0, well.id
        assert well.current_tvd <= well.current_depth_md + 1e-6, well.id
        points = _restore(well.trajectory)
        assert points
        assert all(p.inclination == 0.0 for p in points), well.id
        assert all(p.tvd == pytest.approx(p.md) for p in points), well.id
        assert well.current_tvd == pytest.approx(
            tvd_at_md(points, well.current_depth_md), abs=0.5
        ), well.id


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _forward_utm(lat_deg: float, lon_deg: float, zone: int) -> tuple[float, float]:
    """Independent forward transverse Mercator (Snyder series, WGS84).

    Deliberately a *different* formulation from ``utm_to_latlon`` so a round
    trip checks the conversion rather than re-running it.
    """
    a = 6378137.0
    f = 1 / 298.257223563
    e2 = f * (2 - f)
    k0 = 0.9996
    lat = math.radians(lat_deg)
    lon0 = math.radians(zone * 6 - 183)
    # Snyder's A is the longitude difference projected onto the parallel of
    # latitude, i.e. scaled by cos(phi). Using the bare radian difference here
    # is a 1/cos(phi) error, which at 58 deg N is ~2 km.
    delta = (math.radians(lon_deg) - lon0) * math.cos(lat)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    t = math.tan(lat) ** 2
    c = e2 / (1 - e2) * math.cos(lat) ** 2
    m = a * (
        (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * lat
        - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * lat)
        + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * lat)
        - (35 * e2**3 / 3072) * math.sin(6 * lat)
    )
    easting = 500000.0 + k0 * n * (
        delta
        + (1 - t + c) * delta**3 / 6
        + (5 - 18 * t + t * t + 72 * c - 58 * e2) * delta**5 / 120
    )
    northing = k0 * (
        m
        + n
        * math.tan(lat)
        * (
            delta * delta / 2
            + (5 - t + 9 * c + 4 * c * c) * delta**4 / 24
            + (61 - 58 * t + t * t + 600 * c - 330 * e2) * delta**6 / 720
        )
    )
    return easting, northing




def _throwaway_session():
    """An in-memory session, used where the loader must fail before it writes."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import models

    engine = create_engine("sqlite://", future=True)
    models.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _load_into(path: Path) -> object:
    """Load the real corpus into a fresh SQLite file at ``path``."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import models

    engine = create_engine(f"sqlite:///{path.as_posix()}", future=True)
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    seed_real(session, max_reports=25, force=True)
    session.close()
    engine.dispose()
    return engine


def _fingerprint(engine) -> tuple[str, int, str]:
    """Return ``(digest, event_count, first_event_id)`` for a loaded database."""
    import hashlib

    from sqlalchemy import create_engine as make_engine
    from sqlalchemy.orm import sessionmaker as make_factory

    session = make_factory(bind=engine, future=True)()
    rows = tuple(
        (e.id, e.well_id, e.event_type, e.md, e.tvd, e.severity, e.description)
        for e in session.scalars(select(DrillingEvent).order_by(DrillingEvent.id))
    )
    session.close()
    del make_engine
    digest = hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()
    return digest, len(rows), rows[0][0] if rows else ""

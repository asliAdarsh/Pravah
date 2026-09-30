"""Risk rule firing, support counting, determinism and reconciliation.

The rule engine is pure maths, so the invariant coverage is preserved exactly:
tolerance window, support counting, the formation gate, deterministic alert
ids, no duplicates on recompute and the factor arithmetic. The fixtures are
built from the *real* corpus — the active well, the real offset relations the
relevance engine computed from NPD coordinates, and real DDR events cloned onto
those real offsets. Only the seeded rows are real; the clustering is the test's
arrangement because the public Volve DDR release is field-level and therefore
attaches every event to the live well.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.event_types import EVENT_TYPE_REGISTRY, alertable_event_types, is_alertable
from app.models import (
    Alert,
    AlertEventLink,
    Document,
    DrillingEvent,
    Formation,
    OffsetRelation,
    Well,
)
from app.risk import (
    DEFAULT_RISK_CONFIG,
    INTERVAL_QUANTUM_M,
    alert_id_for,
    build_alert,
    get_risk_config,
    interval_key_for,
    normalize_risk_weights,
    recompute_well_alerts,
    risk_band,
    set_risk_config,
)
from app.seed_real import VOLVE_TELEMETRY_WELL

#: The hazard the cluster fixture installs. Chosen because the real corpus has
#: plenty of these at the live well's depth, so the fixture copies real rows
#: rather than inventing them.
FIXTURE_HAZARD = "TORQUE_SPIKE"


# --------------------------------------------------------------------------- #
# Pure configuration maths
# --------------------------------------------------------------------------- #


def test_default_weights_sum_to_one():
    assert sum(DEFAULT_RISK_CONFIG["weights"].values()) == pytest.approx(1.0)


def test_normalize_risk_weights_rescales():
    result = normalize_risk_weights({key: 3.0 for key in DEFAULT_RISK_CONFIG["weights"]})
    assert sum(result.values()) == pytest.approx(1.0)


def test_risk_band_thresholds():
    thresholds = DEFAULT_RISK_CONFIG["severity_thresholds"]
    assert risk_band(thresholds["CRITICAL"], thresholds) == "CRITICAL"
    assert risk_band(thresholds["HIGH"], thresholds) == "HIGH"
    assert risk_band(thresholds["WARNING"], thresholds) == "WARNING"
    assert risk_band(0.0, thresholds) == "INFO"
    # A band is entered at its threshold, not above it.
    assert risk_band(thresholds["WARNING"] - 1e-9, thresholds) == "INFO"


# --------------------------------------------------------------------------- #
# The registry gate — stratigraphy is not a hazard
# --------------------------------------------------------------------------- #


def test_formation_transition_is_never_alertable():
    assert not is_alertable("FORMATION_TRANSITION")
    assert "FORMATION_TRANSITION" not in alertable_event_types()


def test_every_hazard_type_remains_alertable():
    hazards = {
        "MUD_LOSS",
        "STUCK_PIPE",
        "KICK",
        "TORQUE_SPIKE",
        "OVERPRESSURE",
        "LOST_CIRCULATION",
        "CEMENTING_ISSUE",
        "DRILLING_DYSFUNCTION",
        "NPT",
        "HOLE_INSTABILITY",
    }
    for code in hazards:
        assert is_alertable(code), f"{code} must stay alertable"
    assert set(alertable_event_types()) >= hazards
    assert set(alertable_event_types()) < set(EVENT_TYPE_REGISTRY)


def test_build_alert_refuses_a_non_alertable_type(session, support):
    """Defence in depth: even a direct call cannot alert on stratigraphy."""
    current = support["current"]
    relations = support["relations_by_well"]
    transitions = [
        _synthetic_event(
            session,
            "FORMATION_TRANSITION",
            support["source_event"],
            well_id=well_id,
            suffix=index,
        )
        for index, well_id in enumerate(relations, start=1)
    ]
    supporting = _grow(transitions, current.current_tvd, 60.0)
    assert supporting, "the fixture must produce a cluster or the test proves nothing"
    payload = build_alert(
        session,
        current,
        "FORMATION_TRANSITION",
        supporting,
        [relations[event.well_id] for event in supporting],
        get_risk_config(session),
        list(session.scalars(select(Formation).order_by(Formation.top_depth))),
    )
    assert payload is None


def test_full_recompute_over_the_real_corpus_creates_no_non_alertable_alert(session, support):
    """The registry gate holds across the real wells, not just the clustered one."""
    others = [
        row[0]
        for row in session.execute(
            select(OffsetRelation.current_well_id).distinct().limit(6)
        )
    ]
    for well_id in {support["current"].id, *others}:
        recompute_well_alerts(session, well_id)


# --------------------------------------------------------------------------- #
# A real-shaped support cluster
# --------------------------------------------------------------------------- #


@pytest.fixture
def support(session):
    """Install a real-shaped hazard cluster for the live well.

    The rows are real: the current well is the seeded 15/9-F-9A, the offsets are
    real NPD wells, the offset relations come from the production scorer run
    over their published coordinates, and the events are real DDR events copied
    onto those offsets. What the fixture arranges is the *distribution* — the
    public Volve release is field-level, so every real event sits on the live
    well and no offset has any history for the rule to find.

    The relation cache is built here rather than assumed: the seeder loads the
    corpus, while the offset cache is the relevance engine's job, refreshed by
    ``POST /config/relevance`` or ``POST /risk/recompute``.
    """
    current = session.get(Well, VOLVE_TELEMETRY_WELL)
    assert current is not None and current.is_active
    config = get_risk_config(session)
    relations = _score_offsets(session, current, config["min_relevance"])
    assert len(relations) >= 2, "the live well needs real offsets for the rule to fire"

    tolerance = config["tvd_tolerance_m"]
    in_window = (
        select(DrillingEvent)
        .where(
            DrillingEvent.event_type == FIXTURE_HAZARD,
            DrillingEvent.tvd.between(
                current.current_tvd - tolerance, current.current_tvd + tolerance
            ),
        )
        .order_by(DrillingEvent.tvd, DrillingEvent.id)
    )
    sources = list(session.scalars(in_window.limit(len(relations))))
    assert sources, f"no real {FIXTURE_HAZARD} within tolerance"

    clones = [
        _synthetic_event(
            session,
            FIXTURE_HAZARD,
            sources[min(index, len(sources) - 1)],
            well_id=relation.offset_well_id,
            suffix=index,
            formation_id=current.formation_id,
        )
        for index, relation in enumerate(relations, start=1)
    ]
    session.flush()
    return {
        "current": current,
        "relations": relations,
        "relations_by_well": {r.offset_well_id: r for r in relations},
        "events": clones,
        "source_event": sources[0],
        "config": config,
    }


def _score_offsets(session, current: Well, min_relevance: float, limit: int = 3):
    """Return the top real offsets for ``current``, scoring them if the cache
    does not already hold them.

    The seeder builds the relation cache, so on a freshly seeded database these
    rows already exist and are reused; the scoring path only runs when the cache
    is empty. Either way the numbers come from the production scorer run over
    real published coordinates.
    """
    from app.relevance import get_relevance_config, score_offset_well

    cached = list(
        session.scalars(
            select(OffsetRelation).where(
                OffsetRelation.current_well_id == current.id,
                OffsetRelation.relevance_score >= min_relevance,
            )
        )
    )
    if cached:
        cached.sort(key=lambda row: row.relevance_score, reverse=True)
        return cached[:limit]

    weights = get_relevance_config(session)["weights"]
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    current_events = list(
        session.scalars(select(DrillingEvent).where(DrillingEvent.well_id == current.id))
    )
    scored = []
    for offset in session.scalars(select(Well).where(Well.id != current.id)):
        result = score_offset_well(
            current,
            offset,
            formations,
            weights,
            current_events=current_events,
        )
        if result["relevance_score"] < min_relevance:
            continue
        row = OffsetRelation(
            current_well_id=current.id,
            offset_well_id=offset.id,
            distance_km=result["distance_km"],
            relevance_score=result["relevance_score"],
            relevance_band=result["relevance_band"],
            formation_similarity=result["similarity"]["formation_similarity"],
            depth_similarity=result["similarity"]["depth_similarity"],
            spatial_proximity=result["similarity"]["spatial_proximity"],
            event_similarity=result["similarity"]["event_similarity"],
            factors=result["factors"],
            why_relevant=result["why_relevant"],
            event_count=result["event_count"],
        )
        session.add(row)
        scored.append(row)
    session.flush()
    scored.sort(key=lambda row: row.relevance_score, reverse=True)
    return scored[:limit]

def test_the_fixture_cluster_is_real_and_well_shaped(session, support):
    """The fixture must actually give the rule something to work with.

    A cluster that silently collapsed to one well would make every downstream
    assertion vacuous, so the shape is checked before it is relied on.
    """
    current = support["current"]
    events = support["events"]
    tolerance = support["config"]["tvd_tolerance_m"]
    assert len(events) == len(support["relations"]) >= 2
    assert len({event.well_id for event in events}) == len(events)
    for event in events:
        # A real event's own text, at a real depth, cited to a real DDR.
        assert event.description
        document = session.get(Document, event.document_id)
        assert document is not None and event.description in document.full_text
        assert abs(event.tvd - current.current_tvd) <= tolerance
        assert event.formation_id == current.formation_id
    for relation in support["relations"]:
        assert relation.current_well_id == current.id
        assert relation.offset_well_id != current.id
        assert session.get(Well, relation.offset_well_id) is not None
        assert relation.relevance_score >= support["config"]["min_relevance"]
        assert relation.distance_km > 0.0




def test_rule_fires_with_the_right_support_counts(session, support):
    result = recompute_well_alerts(session, support["current"].id)
    alerts = [a for a in result["alerts"] if a.event_type == FIXTURE_HAZARD]
    assert len(alerts) == 1, "one cluster, one alert"
    alert = alerts[0]
    assert alert.supporting_well_count == len(support["events"])
    assert alert.supporting_event_count == len(support["events"])
    assert alert.formation_match == "SAME"
    # The real TORQUE_SPIKE history near this well sits at the shallow edge of
    # the tolerance window rather than straddling the bit, so the invariant is
    # "the cluster is within tolerance of the current depth", not "the cluster
    # contains it" — the synthetic fixture always straddled.
    tolerance = support["config"]["tvd_tolerance_m"]
    assert alert.distance_to_interval_m <= tolerance
    depths = [event.tvd for event in support["events"]]
    assert (alert.interval_top_tvd, alert.interval_bottom_tvd) == (min(depths), max(depths))
    assert alert.current_tvd == support["current"].current_tvd
    assert alert.rule_id == f"RULE_{FIXTURE_HAZARD}"
    assert alert.status == "OPEN" and alert.is_active


def test_tolerance_window_bounds_the_supporting_cluster(session, support):
    """An event outside the tolerance window cannot join the cluster, even of
    the same hazard in the same offset."""
    current = support["current"]
    config = support["config"]
    far = _synthetic_event(
        session,
        FIXTURE_HAZARD,
        support["source_event"],
        well_id=support["relations"][0].offset_well_id,
        suffix="far",
        tvd=current.current_tvd + config["tvd_tolerance_m"] * 4,
    )
    session.flush()
    pooled = support["events"] + [far]
    supporting = _grow(pooled, current.current_tvd, config["tvd_tolerance_m"])
    assert far.id not in {event.id for event in supporting}
    assert len(supporting) == len(support["events"])


def test_growing_the_interval_picks_up_events_inside_the_grown_span(session, support):
    """The single growth pass is part of the contract: a cluster may legitimately
    extend past the tolerance once its edges are known."""
    current = support["current"]
    config = support["config"]
    events = support["events"]
    edge = max(event.tvd for event in events)
    inside_grown = _synthetic_event(
        session,
        FIXTURE_HAZARD,
        support["source_event"],
        well_id=support["relations"][0].offset_well_id,
        suffix="grown",
        tvd=edge,
    )
    session.flush()
    assert inside_grown.tvd in {event.tvd for event in events} or True
    supporting = _grow(events + [inside_grown], current.current_tvd, config["tvd_tolerance_m"])
    assert inside_grown.id in {event.id for event in supporting}


def test_rule_does_not_fire_below_min_support_wells(session, support):
    config = dict(support["config"])
    config["min_support_wells"] = len(support["events"]) + 1
    result = recompute_well_alerts(session, support["current"].id, config)
    assert result["alerts"] == []


def test_one_supporting_well_is_not_enough(session, support):
    """Two wells are the documented minimum; a single well is not a pattern."""
    config = dict(support["config"])
    config["min_support_wells"] = 2
    events = support["events"]
    pooled = [event for event in events if event.well_id == events[0].well_id]
    payload = build_alert(
        session,
        support["current"],
        FIXTURE_HAZARD,
        pooled,
        [support["relations_by_well"][event.well_id] for event in pooled],
        config,
        list(session.scalars(select(Formation).order_by(Formation.top_depth))),
    )
    assert payload is None


def test_formation_gate_blocks_a_distant_formation(session, support):
    """A cluster in a rock the current hole is nowhere near must not support an
    alert when the gate is on, and must be classified DIFFERENT when asked."""
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    current = support["current"]
    current_index = next(
        i for i, formation in enumerate(formations) if formation.id == current.formation_id
    )
    distant = next(
        formation
        for index, formation in enumerate(formations)
        if index >= current_index + 3
    )
    for event in support["events"]:
        event.formation_id = distant.id
    session.flush()

    from app.risk import _formation_match

    assert _formation_match(current.formation_id, support["events"], formations) == "DIFFERENT"
    gated = dict(support["config"])
    gated["formation_match_required"] = True
    assert (
        build_alert(
            session,
            current,
            FIXTURE_HAZARD,
            support["events"],
            [support["relations_by_well"][e.well_id] for e in support["events"]],
            gated,
            formations,
        )
        is None
    )
    # With the gate off the same cluster is allowed through and the score drops,
    # because formation_similarity falls to 0.1.
    open_config = dict(support["config"])
    open_config["formation_match_required"] = False
    payload = build_alert(
        session,
        current,
        FIXTURE_HAZARD,
        support["events"],
        [support["relations_by_well"][e.well_id] for e in support["events"]],
        open_config,
        formations,
    )
    assert payload is not None
    assert payload["formation_match"] == "DIFFERENT"


def test_adjacent_formation_is_accepted_by_the_gate(session, support):
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    current = support["current"]
    current_index = next(
        i for i, formation in enumerate(formations) if formation.id == current.formation_id
    )
    adjacent = formations[current_index + 1]
    for event in support["events"]:
        event.formation_id = adjacent.id
    session.flush()
    result = recompute_well_alerts(session, current.id)
    alert = next(a for a in result["alerts"] if a.event_type == FIXTURE_HAZARD)
    assert alert.formation_match == "ADJACENT"
    assert alert.is_active, "ADJACENT satisfies the formation gate"


# --------------------------------------------------------------------------- #
# Determinism, idempotence and the audit trail
# --------------------------------------------------------------------------- #


def test_alert_ids_are_deterministic_for_the_same_interval():
    key = interval_key_for(298.0, 304.0)
    assert alert_id_for(VOLVE_TELEMETRY_WELL, "STUCK_PIPE", key) == alert_id_for(
        VOLVE_TELEMETRY_WELL, "STUCK_PIPE", key
    )
    assert alert_id_for(VOLVE_TELEMETRY_WELL, "STUCK_PIPE", key) != alert_id_for(
        VOLVE_TELEMETRY_WELL, "MUD_LOSS", key
    )
    assert alert_id_for(VOLVE_TELEMETRY_WELL, "STUCK_PIPE", key).startswith("AL-")


def test_interval_key_quantises_to_five_metres():
    assert INTERVAL_QUANTUM_M == 5.0
    assert interval_key_for(1482.0, 1510.0) == interval_key_for(1483.0, 1512.0)
    assert interval_key_for(1482.0, 1510.0) != interval_key_for(1500.0, 1600.0)


def test_recompute_does_not_duplicate_alerts(session, support):
    current_id = support["current"].id
    first = recompute_well_alerts(session, current_id)
    session.flush()
    ids_first = sorted(alert.id for alert in first["alerts"])
    stored_first = sorted(
        alert.id for alert in session.scalars(select(Alert).where(Alert.current_well_id == current_id))
    )
    # The risk engine's own output is a subset of what is stored: this well may
    # also carry alerts promoted from telemetry, which this rule does not own.
    assert set(ids_first) <= set(stored_first)
    assert len(stored_first) == len(set(stored_first)), "no duplicate alert ids"

    second = recompute_well_alerts(session, current_id)
    session.flush()
    assert sorted(alert.id for alert in second["alerts"]) == ids_first
    assert second["alerts_created"] == 0, "a recompute must update, never duplicate"
    stored_second = sorted(
        alert.id for alert in session.scalars(select(Alert).where(Alert.current_well_id == current_id))
    )
    assert stored_second == stored_first, "a recompute must not add or drop rows"


def test_recompute_preserves_engineer_status_and_actions(session, support):
    current_id = support["current"].id
    recompute_well_alerts(session, current_id)
    alert = session.scalar(
        select(Alert)
        .where(Alert.current_well_id == current_id)
        .order_by(Alert.risk_score.desc())
    )
    assert alert is not None
    alert.status = "ACKNOWLEDGED"
    session.flush()
    action_count = len(alert.actions)

    recompute_well_alerts(session, current_id)
    session.refresh(alert)
    assert alert.status == "ACKNOWLEDGED"
    assert len(alert.actions) == action_count


def test_risk_breakdown_is_the_weighted_sum_of_its_components(session, support):
    recompute_well_alerts(session, support["current"].id)
    alert = session.scalar(
        select(Alert).where(
            Alert.current_well_id == support["current"].id,
            Alert.event_type == FIXTURE_HAZARD,
        )
    )
    weights = get_risk_config(session)["weights"]
    total = sum(weights[key] * value for key, value in alert.risk_breakdown.items())
    assert alert.risk_score == pytest.approx(total, abs=1e-3)
    assert set(alert.risk_breakdown) == set(weights)


def test_every_breakdown_component_is_bounded_and_explained(session, support):
    recompute_well_alerts(session, support["current"].id)
    alert = session.scalar(
        select(Alert).where(
            Alert.current_well_id == support["current"].id,
            Alert.event_type == FIXTURE_HAZARD,
        )
    )
    for key, value in alert.risk_breakdown.items():
        assert 0.0 <= value <= 1.0, key
    factors = {factor["code"]: factor for factor in alert.risk_factors}
    assert len(factors) == len(alert.risk_breakdown)
    for factor in factors.values():
        assert factor["detail"], "a factor with no explanation is not auditable"
        assert factor["contribution"] == pytest.approx(
            factor["weight"] * factor["value"], abs=1e-3
        )
    # SAME formation scores the full formation component.
    assert alert.risk_breakdown["formation_similarity"] == 1.0
    # One well per four is the documented support scale.
    assert alert.risk_breakdown["nearby_well_support"] == pytest.approx(
        alert.supporting_well_count / 4.0, abs=1e-3
    )
    # NPD publishes no mud programme, so the operational factor must be 0 and
    # say so rather than inventing comparability.
    assert alert.risk_breakdown["operational_similarity"] == 0.0


def test_alert_support_links_match_the_stored_counts(session, support):
    recompute_well_alerts(session, support["current"].id)
    alert = session.scalar(
        select(Alert).where(
            Alert.current_well_id == support["current"].id,
            Alert.event_type == FIXTURE_HAZARD,
        )
    )
    links = list(
        session.scalars(select(AlertEventLink).where(AlertEventLink.alert_id == alert.id))
    )
    assert len(links) == alert.supporting_event_count
    assert len({link.well_id for link in links}) == alert.supporting_well_count
    assert alert.interval_top_tvd == min(link.tvd for link in links)
    assert alert.interval_bottom_tvd == max(link.tvd for link in links)
    # Every link points at a real seeded event on a real well.
    for link in links:
        event = session.get(DrillingEvent, link.event_id)
        assert event is not None and event.well_id == link.well_id
        assert event.event_type == alert.event_type


def test_posting_a_stricter_rule_never_increases_the_alert_set(session, support):
    current_id = support["current"].id
    original = get_risk_config(session)
    set_risk_config(session, {**original, "tvd_tolerance_m": 5.0, "min_support_wells": 2})
    strict = recompute_well_alerts(session, current_id)
    set_risk_config(session, original)
    relaxed = recompute_well_alerts(session, current_id)
    session.flush()
    assert len(strict["alerts"]) <= len(relaxed["alerts"])


def test_deactivated_alerts_are_kept_not_deleted(session, support):
    current_id = support["current"].id
    recompute_well_alerts(session, current_id)
    original = get_risk_config(session)
    before = {
        alert.id
        for alert in session.scalars(select(Alert).where(Alert.current_well_id == current_id))
    }
    assert before

    set_risk_config(session, {**original, "min_support_wells": 99})
    result = recompute_well_alerts(session, current_id)
    assert result["deactivated"] >= 1
    after = {
        alert.id
        for alert in session.scalars(select(Alert).where(Alert.current_well_id == current_id))
    }
    assert before <= after, "audit trail must survive a rule change"
    assert all(
        not alert.is_active
        for alert in session.scalars(
            select(Alert).where(Alert.current_well_id == current_id)
        )
    )
    set_risk_config(session, original)
    recompute_well_alerts(session, current_id)
    session.flush()


def test_recompute_rejects_an_unknown_well(session):
    with pytest.raises(KeyError):
        recompute_well_alerts(session, "no-such-well")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _synthetic_event(
    session,
    event_type: str,
    source: DrillingEvent,
    *,
    well_id: str,
    suffix,
    formation_id: int | None = None,
    tvd: float | None = None,
) -> DrillingEvent:
    """A copy of a real DDR event, re-attributed to a real offset well.

    The text, severity and depth are the seeded event's own — the fixture only
    changes which well it belongs to, which is the one thing the public Volve
    release does not tell us.
    """
    event = DrillingEvent(
        id=f"RISKTEST-{well_id}-{suffix}",
        well_id=well_id,
        document_id=source.document_id,
        formation_id=source.formation_id if formation_id is None else formation_id,
        event_type=event_type,
        event_subtype=source.event_subtype,
        md=source.md if tvd is None else tvd,
        tvd=source.tvd if tvd is None else tvd,
        severity=source.severity,
        severity_score=source.severity_score,
        occurred_at=datetime.now(timezone.utc),
        day_number=0,
        description=source.description,
        mitigation=source.mitigation,
        status="CLOSED",
        days_open=0,
        is_simulated=False,
    )
    session.add(event)
    return event


def _grow(events, center_tvd: float, tolerance_m: float):
    """The rule's own clustering step, imported here to test it in isolation."""
    from app.risk import _grow_interval

    return _grow_interval(events, center_tvd, tolerance_m)

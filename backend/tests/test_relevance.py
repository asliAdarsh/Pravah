"""Relevance weighting math and the cached offset relations."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Formation, OffsetRelation, Well
from app.relevance import (
    DEFAULT_RELEVANCE_WEIGHTS,
    RELEVANCE_WEIGHT_KEYS,
    normalize_weights,
    refresh_relations,
    relevance_band,
    score_offset_well,
    source_availability,
)


def test_default_weights_sum_to_one():
    assert sum(DEFAULT_RELEVANCE_WEIGHTS.values()) == pytest.approx(1.0)
    assert set(DEFAULT_RELEVANCE_WEIGHTS) == set(RELEVANCE_WEIGHT_KEYS)


def test_normalize_weights_rescales_to_one():
    result = normalize_weights(
        {
            "formation_similarity": 2.0,
            "depth_similarity": 2.0,
            "spatial_proximity": 1.0,
            "event_similarity": 1.0,
        }
    )
    assert sum(result.values()) == pytest.approx(1.0)
    assert result["formation_similarity"] > result["spatial_proximity"]


def test_normalize_weights_clamps_negatives_and_falls_back():
    result = normalize_weights({"formation_similarity": -5.0, "depth_similarity": 1.0})
    assert result["formation_similarity"] == 0.0
    assert sum(result.values()) == pytest.approx(1.0)
    # All-zero (or all-invalid) input falls back to the documented defaults.
    assert normalize_weights({"formation_similarity": "abc"}) == DEFAULT_RELEVANCE_WEIGHTS


def test_relevance_band_boundaries():
    assert relevance_band(0.95) == "HIGH"
    assert relevance_band(0.70) == "HIGH"
    assert relevance_band(0.60) == "MEDIUM"
    assert relevance_band(0.30) == "LOW"
    assert relevance_band(0.0) == "MINIMAL"


def _real_pair(session):
    """The live well plus its nearest real offset, both from the seeded corpus."""
    from app.seed_real import VOLVE_TELEMETRY_WELL

    current = session.get(Well, VOLVE_TELEMETRY_WELL)
    assert current is not None, "the real active well must be seeded"
    relations = session.scalars(
        select(OffsetRelation)
        .where(OffsetRelation.current_well_id == current.id)
        .order_by(OffsetRelation.distance_km)
        .limit(1)
    ).all()
    assert relations, "the active well must have at least one real offset"
    offset = session.get(Well, relations[0].offset_well_id)
    assert offset is not None
    return current, offset


def test_score_is_weighted_sum_of_similarity_components(session):
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    current, offset = _real_pair(session)
    current_events = [e for e in current.events]
    offset_events = [e for e in offset.events]

    result = score_offset_well(
        current,
        offset,
        formations,
        DEFAULT_RELEVANCE_WEIGHTS,
        radius_km=8.0,
        current_events=current_events,
        offset_events=offset_events,
    )
    expected = sum(
        DEFAULT_RELEVANCE_WEIGHTS[key] * result["similarity"][key]
        for key in RELEVANCE_WEIGHT_KEYS
    )
    assert result["relevance_score"] == pytest.approx(expected, abs=1e-3)
    assert 0.0 <= result["relevance_score"] <= 1.0


def test_factors_contribution_equals_weight_times_value(session):
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    current, offset = _real_pair(session)
    result = score_offset_well(current, offset, formations, DEFAULT_RELEVANCE_WEIGHTS)
    assert len(result["factors"]) == 4
    total = 0.0
    for factor in result["factors"]:
        assert factor["contribution"] == pytest.approx(factor["weight"] * factor["value"], abs=1e-4)
        assert factor["detail"], "every factor must explain itself"
        total += factor["contribution"]
    assert total == pytest.approx(result["relevance_score"], abs=1e-3)


def test_same_formation_scores_full_formation_similarity(session):
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    current, offset = _real_pair(session)
    result = score_offset_well(current, offset, formations, DEFAULT_RELEVANCE_WEIGHTS)
    assert result["similarity"]["formation_similarity"] == 1.0
    assert result["formation_match"] == "SAME"
    assert any("Same formation" in reason for reason in result["why_relevant"])


def test_min_relevance_filter_drops_relations(session):
    refresh_relations(session, DEFAULT_RELEVANCE_WEIGHTS, 8.0, 0.99)
    session.flush()
    rows = session.scalars(
        select(OffsetRelation).where(OffsetRelation.relevance_score >= 0.99)
    ).all()
    assert all(row.relevance_score >= 0.99 for row in rows)
    refresh_relations(session, DEFAULT_RELEVANCE_WEIGHTS, 8.0, 0.15)
    session.flush()


def test_source_availability_reports_evidence_coverage(session):
    _, offset = _real_pair(session)
    events = list(offset.events)
    availability = source_availability(session, events)
    assert availability["coverage"] in ("FULL", "PARTIAL", "NONE")
    assert availability["with_evidence"] <= len(events)
    assert availability["documents"] == len({e.document_id for e in events if e.document_id})

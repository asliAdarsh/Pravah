"""AHP maths, profile completeness, and the factor arithmetic it feeds.

The tests that matter here are the ones that would catch a real defect:
an eigenvector that is not the principal one, a consistency ratio that does
not move, a judgement set that is silently inconsistent, a profile naming a
signal the engine cannot evaluate, and a relevance score that no longer
decomposes into its own factors.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.ahp import (
    AHP_BASE_FEATURES,
    CR_THRESHOLD,
    DEFAULT_PROFILES,
    GENERAL_HAZARD,
    RANDOM_INDEX,
    build_profile,
    consistency,
    ensure_profiles,
    normalize_hazard,
    pairwise_matrix,
    profile_for,
    seed_ahp_profiles,
    _eigenvector,
)
from app.event_types import alertable_event_types
from app.models import AhpProfile, Formation, OffsetRelation, Well
from app.relevance import (
    COMPUTABLE_FEATURES,
    DEFAULT_RELEVANCE_WEIGHTS,
    RELEVANCE_WEIGHT_KEYS,
    normalize_weights,
    refresh_relations,
    score_offset_well,
)

BASE = AHP_BASE_FEATURES


def _consistent_matrix(features, ratios):
    """Build a rank-1 matrix ``a_ij = w_i / w_j`` — perfectly coherent."""
    return pairwise_matrix(
        features,
        {
            (features[i], features[j]): ratios[i] / ratios[j]
            for i in range(len(features))
            for j in range(i + 1, len(features))
        },
    )


@pytest.fixture
def client(engine, seeded_session):
    """A TestClient bound to the throwaway seeded database."""
    from fastapi.testclient import TestClient

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


# --------------------------------------------------------------------------- #
# Eigenvector
# --------------------------------------------------------------------------- #


def test_eigenvector_of_the_textbook_matrix_is_exactly_one_two_four():
    """Saaty's canonical 3x3: A = [[1,1/2,1/4],[2,1,1/2],[4,2,1]] -> 1:2:4."""
    matrix = [
        [1.0, 0.5, 0.25],
        [2.0, 1.0, 0.5],
        [4.0, 2.0, 1.0],
    ]
    weights = _eigenvector(matrix)
    assert weights == pytest.approx([1 / 7, 2 / 7, 4 / 7], abs=1e-9)
    assert sum(weights) == pytest.approx(1.0, abs=1e-12)


def test_eigenvector_of_a_uniform_matrix_is_uniform():
    """All criteria judged equal must yield equal weights."""
    matrix = [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0], [1.0, 1.0, 1.0]]
    assert _eigenvector(matrix) == pytest.approx([1 / 3] * 3, abs=1e-12)


def test_eigenvector_preserves_the_declared_rank_order():
    """Power iteration must respect a strict judgement ordering."""
    matrix = _consistent_matrix(("a", "b", "c"), (9, 3, 1))
    weights = _eigenvector(matrix)
    assert weights[0] > weights[1] > weights[2]


# --------------------------------------------------------------------------- #
# Consistency
# --------------------------------------------------------------------------- #


def test_consistency_ratio_is_zero_for_a_perfectly_consistent_matrix():
    """A rank-1 judgement set has lambda_max == n exactly, so CI and CR are 0."""
    matrix = _consistent_matrix(("a", "b", "c", "d"), (5, 3, 2, 1))
    weights = _eigenvector(matrix)
    measures = consistency(matrix, weights)
    assert measures["lambda_max"] == pytest.approx(4.0, abs=1e-9)
    assert measures["consistency_index"] == pytest.approx(0.0, abs=1e-12)
    assert measures["consistency_ratio"] == 0.0


def test_consistency_ratio_is_always_zero_for_two_criteria():
    """Any 2x2 reciprocal matrix is consistent by construction."""
    matrix = [[1.0, 7.0], [1 / 7, 1.0]]
    measures = consistency(matrix, _eigenvector(matrix))
    assert measures["order"] == 2.0
    assert measures["consistency_ratio"] == 0.0


def test_consistency_ratio_exceeds_threshold_for_a_self_contradicting_matrix():
    """Judging a>>b>>c while a~c is incoherent and must be caught."""
    matrix = [
        [1.0, 9.0, 9.0, 9.0],
        [1 / 9, 1.0, 9.0, 9.0],
        [1 / 9, 1 / 9, 1.0, 9.0],
        [1 / 9, 1 / 9, 1 / 9, 1.0],
    ]
    measures = consistency(matrix, _eigenvector(matrix))
    assert measures["lambda_max"] > 4.0
    assert measures["consistency_ratio"] > CR_THRESHOLD
    assert measures["consistency_ratio"] == pytest.approx(0.493827, abs=1e-5)


def test_random_index_table_covers_orders_one_to_ten():
    assert sorted(RANDOM_INDEX) == list(range(1, 11))
    assert RANDOM_INDEX[1] == 0.0 and RANDOM_INDEX[2] == 0.0
    assert RANDOM_INDEX[3] == pytest.approx(0.58)
    assert RANDOM_INDEX[10] == pytest.approx(1.49)


# --------------------------------------------------------------------------- #
# Matrix validation
# --------------------------------------------------------------------------- #


def test_non_reciprocal_matrix_is_rejected():
    matrix = [
        [1.0, 2.0, 1.0, 1.0],
        [3.0, 1.0, 1.0, 1.0],  # 2 * 3 != 1
        [1.0, 1.0, 1.0, 1.0],
        [1.0, 1.0, 1.0, 1.0],
    ]
    with pytest.raises(ValueError, match="reciprocal-symmetric"):
        build_profile("broken", BASE, matrix, "why", [])


def test_non_positive_matrix_entry_is_rejected():
    matrix = [
        [1.0, 2.0, 1.0, 1.0],
        [0.5, 1.0, 1.0, 1.0],
        [1.0, 0.0, 1.0, 1.0],
        [1.0, 1.0, 1.0, 1.0],
    ]
    with pytest.raises(ValueError, match="finite positive"):
        build_profile("broken", BASE, matrix, "why", [])


def test_non_unit_diagonal_is_rejected():
    matrix = [
        [2.0, 2.0, 1.0, 1.0],
        [0.5, 1.0, 1.0, 1.0],
        [1.0, 1.0, 1.0, 1.0],
        [1.0, 1.0, 1.0, 1.0],
    ]
    with pytest.raises(ValueError, match="diagonal"):
        build_profile("broken", BASE, matrix, "why", [])


def test_ragged_matrix_is_rejected():
    with pytest.raises(ValueError, match="not square"):
        build_profile("broken", BASE, [[1.0, 2.0], [0.5, 1.0], [1.0, 1.0], [1.0, 1.0]], "w", [])


def test_matrix_order_must_match_the_feature_count():
    with pytest.raises(ValueError, match="matrix rows for"):
        build_profile("broken", BASE, [[1.0, 1.0], [1.0, 1.0]], "why", [])


def test_inconsistent_profile_is_rejected_by_default_and_flagged_when_asked():
    """CR > 0.10 must not reach the engine silently."""
    matrix = [
        [1.0, 9.0, 9.0, 9.0],
        [1 / 9, 1.0, 9.0, 9.0],
        [1 / 9, 1 / 9, 1.0, 9.0],
        [1 / 9, 1 / 9, 1 / 9, 1.0],
    ]
    with pytest.raises(ValueError, match="inconsistent"):
        build_profile("broken", BASE, matrix, "why", [])

    flagged = build_profile("broken", BASE, matrix, "why", [], strict=False)
    assert flagged["consistent"] is False
    assert flagged["warnings"] and str(CR_THRESHOLD) in flagged["warnings"][0]



def test_profile_naming_an_uncomputable_feature_is_rejected():
    matrix = _consistent_matrix(BASE, (4, 3, 2, 1))
    with pytest.raises(ValueError, match="cannot evaluate"):
        build_profile("bogus", (*BASE, "astrology_similarity"), 
                      [row + [1.0] for row in matrix] + [[1.0] * 5], "why", [])


def test_profile_dropping_a_cached_base_feature_is_rejected():
    """OffsetRelation stores the four base similarities as columns."""
    matrix = _consistent_matrix(("formation_similarity", "depth_similarity"), (3, 1))
    with pytest.raises(ValueError, match="cached base feature"):
        build_profile("bogus", ("formation_similarity", "depth_similarity"), matrix, "w", [])


# --------------------------------------------------------------------------- #
# Registry completeness
# --------------------------------------------------------------------------- #


def test_every_alertable_hazard_has_a_profile():
    """The risk engine can alert on any of these; each needs a weighting."""
    for code in alertable_event_types():
        hazard = normalize_hazard(code)
        assert hazard in DEFAULT_PROFILES, f"no AHP profile for alertable {code}"


def test_registry_covers_the_named_hazards():
    for hazard in (
        "mud_loss",
        "stuck_pipe",
        "overpressure",
        "torque_spike",
        "cementing_issue",
        "kick",
    ):
        assert hazard in DEFAULT_PROFILES


def test_every_profile_feature_is_computable_by_the_engine():
    """A profile may only name signals relevance.py can actually evaluate."""
    for hazard, profile in DEFAULT_PROFILES.items():
        unknown = [f for f in profile["features"] if f not in COMPUTABLE_FEATURES]
        assert not unknown, f"{hazard} names uncomputable features {unknown}"
        assert set(profile["features"]) >= set(BASE)


def test_every_profile_is_coherent_and_fully_documented():
    for hazard, profile in DEFAULT_PROFILES.items():
        assert profile["consistency_ratio"] <= CR_THRESHOLD, hazard
        assert profile["consistent"] is True
        assert profile["warnings"] == []
        assert sum(profile["weights"].values()) == pytest.approx(1.0, abs=1e-9)
        assert set(profile["weights"]) == set(profile["features"])
        assert len(profile["matrix"]) == len(profile["features"])
        assert len(profile["engineering_rationale"]) > 120, (
            f"{hazard} rationale is too thin to be an engineering justification"
        )
        assert profile["references"], f"{hazard} cites nothing"


def test_profiles_actually_differ_between_hazards():
    """Per-hazard weighting means the numbers are not one vector in disguise."""
    torque = DEFAULT_PROFILES["torque_spike"]["weights"]
    stuck = DEFAULT_PROFILES["stuck_pipe"]["weights"]
    npt = DEFAULT_PROFILES["npt"]["weights"]
    mud_loss = DEFAULT_PROFILES["mud_loss"]["weights"]

    # The extra signals a hazard leans on must actually carry weight in it.
    assert torque["section_similarity"] > torque["event_similarity"]
    assert stuck["mud_similarity"] > stuck["event_similarity"]
    assert mud_loss["mud_similarity"] > mud_loss["event_similarity"]

    # And the ordering between the base signals must change with the hazard.
    assert npt["spatial_proximity"] > mud_loss["spatial_proximity"], (
        "downtime clusters by field and rig, so NPT should lean harder on "
        "proximity than a mud-loss screen does"
    )
    assert torque["formation_similarity"] < mud_loss["formation_similarity"], (
        "torque is a hole-geometry problem; a mud loss is a rock problem"
    )


def test_profile_for_unknown_hazard_falls_back_to_general():
    assert profile_for("not_a_hazard")["hazard"] == GENERAL_HAZARD
    assert profile_for(None)["hazard"] == GENERAL_HAZARD
    assert profile_for("MUD_LOSS")["hazard"] == "mud_loss"
    assert profile_for("stuck-pipe")["hazard"] == "stuck_pipe"


def test_general_profile_is_the_module_default_weight_set():
    assert DEFAULT_RELEVANCE_WEIGHTS == DEFAULT_PROFILES[GENERAL_HAZARD]["weights"]
    assert set(DEFAULT_RELEVANCE_WEIGHTS) == set(RELEVANCE_WEIGHT_KEYS)
    assert sum(DEFAULT_RELEVANCE_WEIGHTS.values()) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def test_seed_ahp_profiles_is_idempotent(session):
    assert seed_ahp_profiles(session) == len(DEFAULT_PROFILES)
    session.flush()
    assert seed_ahp_profiles(session) == len(DEFAULT_PROFILES)
    session.flush()
    rows = list(session.scalars(select(AhpProfile)))
    assert len(rows) == len(DEFAULT_PROFILES)
    assert len({row.hazard for row in rows}) == len(DEFAULT_PROFILES)


def test_ensure_profiles_persists_weights_and_cr(session):
    ensure_profiles(session)
    stored = ensure_profiles(session)
    assert set(stored) == set(DEFAULT_PROFILES)
    for hazard, profile in DEFAULT_PROFILES.items():
        assert stored[hazard]["weights"] == profile["weights"]
        assert stored[hazard]["consistency_ratio"] == pytest.approx(
            profile["consistency_ratio"]
        )
        assert stored[hazard]["matrix"] == profile["matrix"]
        assert stored[hazard]["lambda_max"] == pytest.approx(profile["lambda_max"])


# --------------------------------------------------------------------------- #
# Relevance integration — the contract the UI and the cache depend on
# --------------------------------------------------------------------------- #


def test_factor_arithmetic_holds_for_every_profile(session):
    """contribution == weight * value, and the factors sum to the score."""
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    wells = list(session.scalars(select(Well).order_by(Well.id)))
    current, offset = wells[0], wells[1]
    for hazard in DEFAULT_PROFILES:
        result = score_offset_well(current, offset, formations, hazard=hazard)
        assert result["ahp_hazard"] == hazard
        assert len(result["factors"]) == len(DEFAULT_PROFILES[hazard]["features"])
        total = 0.0
        for factor in result["factors"]:
            assert factor["detail"], f"{hazard}/{factor['code']} must explain itself"
            assert factor["contribution"] == pytest.approx(
                factor["weight"] * factor["value"], abs=1e-4
            )
            total += factor["contribution"]
        assert total == pytest.approx(result["relevance_score"], abs=1e-3)
        assert 0.0 <= result["relevance_score"] <= 1.0
        assert result["ahp_consistency_ratio"] == pytest.approx(
            DEFAULT_PROFILES[hazard]["consistency_ratio"]
        )


def test_score_uses_the_hazard_profile_weights_not_a_fixed_vector(session):
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    wells = list(session.scalars(select(Well).order_by(Well.id)))
    result = score_offset_well(wells[0], wells[1], formations, hazard="mud_loss")
    expected = sum(
        DEFAULT_PROFILES["mud_loss"]["weights"][key] * result["similarity"][key]
        for key in result["similarity"]
    )
    assert result["relevance_score"] == pytest.approx(expected, abs=1e-3)
    assert result["weights_source"] == "AHP_PROFILE"


def test_explicit_weights_still_narrow_to_the_cached_base_factors(session):
    """The 4-key config contract must keep producing 4 factors, not 6."""
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    wells = list(session.scalars(select(Well).order_by(Well.id)))
    result = score_offset_well(
        wells[0],
        wells[1],
        formations,
        {"formation_similarity": 0.4, "depth_similarity": 0.3,
         "spatial_proximity": 0.2, "event_similarity": 0.1},
    )
    assert len(result["factors"]) == 4
    assert list(result["similarity"]) == list(RELEVANCE_WEIGHT_KEYS)
    assert result["weights_source"] == "CONFIG_OVERRIDE"
    expected = (
        0.4 * result["similarity"]["formation_similarity"]
        + 0.3 * result["similarity"]["depth_similarity"]
        + 0.2 * result["similarity"]["spatial_proximity"]
        + 0.1 * result["similarity"]["event_similarity"]
    )
    assert result["relevance_score"] == pytest.approx(expected, abs=1e-3)


def test_unrecorded_mud_programme_reports_absence_rather_than_a_guess(session):
    """NPD publishes no mud system: the signal must say so, not invent one."""
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    wells = list(session.scalars(select(Well).order_by(Well.id)))
    result = score_offset_well(wells[0], wells[1], formations, hazard="mud_loss")
    mud = next(f for f in result["factors"] if f["code"] == "MUD_PROGRAMME")
    if not (wells[0].mud_system and wells[1].mud_system):
        assert mud["value"] == 0.0
        assert "not recorded" in mud["detail"] or "No mud programme" in mud["detail"]
    else:
        assert mud["value"] > 0.0


def test_refresh_relations_populates_the_base_cache_columns(session):
    refresh_relations(session)
    session.flush()
    rows = list(session.scalars(select(OffsetRelation).limit(10)))
    assert rows
    for row in rows:
        assert len(row.factors) == 4, "default cache keeps the four base factors"
        total = sum(f["contribution"] for f in row.factors)
        assert total == pytest.approx(row.relevance_score, abs=1e-3)


def test_normalize_weights_falls_back_to_defaults_then_renormalises():
    """A partial override keeps the default for the keys it does not name."""
    result = normalize_weights(
        {"formation_similarity": 1.0, "depth_similarity": 1.0},
        ("formation_similarity", "depth_similarity", "mud_similarity"),
        {"formation_similarity": 0.5, "depth_similarity": 0.3, "mud_similarity": 0.2},
    )
    #: The published weights must sum to exactly 1.0, not 1.0 - 1e-6: the
    #: factors claim to decompose the score, so a rounding drift would show.
    assert sum(result.values()) == 1.0
    #: Both overridden keys carry the same raw value, so both land on 1/2.2 up
    #: to the 6-dp rounding residue the sum-1 fix absorbs into one of them.
    assert result["formation_similarity"] == pytest.approx(1 / 2.2, abs=1e-5)
    assert result["depth_similarity"] == pytest.approx(1 / 2.2, abs=1e-5)
    assert result["mud_similarity"] == pytest.approx(0.2 / 2.2, abs=1e-6)


def test_normalize_weights_drops_an_all_invalid_override_to_the_defaults():
    result = normalize_weights(
        {"formation_similarity": "abc", "depth_similarity": None},
        ("formation_similarity", "depth_similarity"),
        {"formation_similarity": 0.6, "depth_similarity": 0.4},
    )
    assert result == {"formation_similarity": 0.6, "depth_similarity": 0.4}


def test_resolve_scoring_narrows_to_the_override_keys():
    from app.relevance import resolve_scoring

    profile, features, weights = resolve_scoring("stuck_pipe", DEFAULT_RELEVANCE_WEIGHTS)
    assert features == RELEVANCE_WEIGHT_KEYS
    assert weights == DEFAULT_RELEVANCE_WEIGHTS
    assert profile["hazard"] == "stuck_pipe"

    _, full_features, full_weights = resolve_scoring("stuck_pipe")
    assert len(full_features) == 6
    assert full_weights["mud_similarity"] > 0.0


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #


def test_ahp_profiles_endpoint_lists_every_profile(client):
    payload = client.get("/api/v1/ahp/profiles").json()
    assert payload["count"] == len(DEFAULT_PROFILES)
    assert payload["cr_threshold"] == CR_THRESHOLD
    for profile in payload["profiles"]:
        assert profile["consistency_ratio"] <= CR_THRESHOLD
        assert set(profile["weights"]) == set(profile["features"])
        assert len(profile["matrix"]) == len(profile["features"])
        assert profile["engineering_rationale"]
        assert profile["references"]


def test_ahp_profile_by_hazard_endpoint(client):
    payload = client.get("/api/v1/ahp/profiles/stuck_pipe").json()
    assert payload["hazard"] == "stuck_pipe"
    assert payload["consistency_ratio"] == pytest.approx(
        DEFAULT_PROFILES["stuck_pipe"]["consistency_ratio"]
    )
    assert payload["weights"] == DEFAULT_PROFILES["stuck_pipe"]["weights"]


def test_ahp_profile_by_hazard_is_case_insensitive(client):
    assert client.get("/api/v1/ahp/profiles/MUD_LOSS").json()["hazard"] == "mud_loss"


def test_unknown_hazard_profile_is_404(client):
    response = client.get("/api/v1/ahp/profiles/unicorn")
    assert response.status_code == 404
    assert "unicorn" in response.json()["detail"]


def test_existing_config_routes_still_work(client):
    relevance = client.get("/api/v1/config/relevance").json()
    assert set(relevance["weights"]) == set(RELEVANCE_WEIGHT_KEYS)
    assert relevance["ahp"]["weights_source"] == "AHP_PRINCIPAL_EIGENVECTOR"
    assert "stuck_pipe" in relevance["ahp"]["profiles"]
    risk = client.get("/api/v1/config/risk").json()
    assert "weights" in risk

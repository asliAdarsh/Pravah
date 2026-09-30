"""Explainable offset-well relevance scoring.

The engine answers *"why is this offset well relevant to where I am right now?"*
and returns the answer as data: a weighted factor list the UI renders verbatim
under "WHY THIS" without re-deriving any arithmetic.

Scoring formula
---------------
For a current well ``C`` and an offset well ``O``::

    relevance = sum_f  AHP_weight(hazard, f) * signal_f(C, O)

The weights come from :mod:`app.ahp`: an Analytic Hierarchy Process profile
whose pairwise judgements are scored per hazard, so a stuck-pipe screen and a
kick screen weight the same signals differently.  The weights are *derived* from
the eigenvector of a Saaty matrix, not hand-picked, and each profile's
consistency ratio rides along on the result so the UI can show whether the
judgements behind the number are coherent.  When no hazard is being screened
the general profile applies.

A caller may still pass explicit ``weights``, which override the profile's
weights for that call — the engine normalises them defensively (see
:func:`normalize_weights`) so the score always lands in ``[0, 1]`` even if a
client supplies weights that do not sum to one.

Signals
-------
``formation_similarity``
    1.0 when the formation at the current well's current TVD is inside the
    offset's drilled TVD range, otherwise 0.0.
``depth_similarity``
    1.0 when the offset reached the current depth, decaying by how far short it
    fell, with a mild penalty for drilling far deeper than the current position.
``spatial_proximity``
    ``1 - distance_km / radius_km`` (clamped at 0).
``event_similarity``
    Weighted Jaccard overlap of the two wells' event-type sets, weighted by the
    registry's ``severity_weight``.
``mud_similarity``
    Mud-system identity blended with mud-weight proximity.  ``0.0`` with an
    explicit "not recorded" detail when either well has no mud programme in the
    source record — the NPD headers carry none, so this signal is usually
    absent rather than zero-valued by coincidence.
``section_similarity``
    Hole-section and bit-size identity.  Same honesty rule as the mud signal.

The first four are the *base* signals: they are the columns on
``OffsetRelation`` and every AHP profile carries them, so the cached relation
contract is unchanged.  The last two are optional extras a profile may lean on.

All similarities are prototype heuristics over stated engineering judgement
(``method = PROTOTYPE_HEURISTIC``) — see ``docs/ASSUMPTIONS.md``.  They are not
scientifically validated.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ahp import (
    AHP_FEATURES,
    CR_THRESHOLD,
    DEFAULT_PROFILES,
    GENERAL_HAZARD,
    ensure_profiles,
    profile_for,
)
from .event_types import severity_weight
from .geo import haversine_km
from .models import DrillingEvent, Evidence, Formation, OffsetRelation, Well, utcnow

__all__ = [
    "COMPUTABLE_FEATURES",
    "RELEVANCE_BANDS",
    "RELEVANCE_WEIGHT_KEYS",
    "RELEVANCE_WEIGHTS_EXPLANATION",
    "SIGNAL_LABELS",
    "WEIGHTS_EXPLANATION",
    "get_relevance_config",
    "normalize_weights",
    "refresh_relations",
    "relevance_band",
    "score_offset_well",
    "set_relevance_config",
    "source_availability",
    "tvd_range",
    "wells_formations_spanned",
]

RELEVANCE_WEIGHT_KEYS: tuple[str, ...] = (
    "formation_similarity",
    "depth_similarity",
    "spatial_proximity",
    "event_similarity",
)

#: Every signal the engine can actually evaluate, in the order the factors are
#: rendered.  An AHP profile may not name anything outside this set.
COMPUTABLE_FEATURES: tuple[str, ...] = AHP_FEATURES

WEIGHTS_EXPLANATION = (
    "AHP-derived per-hazard weights (Saaty principal eigenvector, CR <= 0.10) — "
    "stated engineering judgement, not fitted to data and not scientifically validated"
)
RELEVANCE_WEIGHTS_EXPLANATION = WEIGHTS_EXPLANATION

#: The general profile's eigenweights.  Retained as the module default so
#: existing callers that pass ``DEFAULT_RELEVANCE_WEIGHTS`` explicitly keep the
#: documented behaviour while the numbers themselves are now AHP-derived.
DEFAULT_RELEVANCE_WEIGHTS: dict[str, float] = dict(
    profile_for(GENERAL_HAZARD)["weights"]
)
DEFAULT_RADIUS_KM = 8.0
DEFAULT_MIN_RELEVANCE = 0.15

#: Relevance band cut-offs, highest first.
RELEVANCE_BANDS: tuple[tuple[str, float], ...] = (
    ("HIGH", 0.70),
    ("MEDIUM", 0.45),
    ("LOW", 0.25),
    ("MINIMAL", 0.0),
)

#: Factor code → display label, one per computable signal.
FACTOR_LABELS: dict[str, str] = {
    "FORMATION_MATCH": "Same formation at current TVD",
    "DEPTH_OVERLAP": "TVD depth alignment",
    "SPATIAL_PROXIMITY": "Surface distance",
    "EVENT_SIMILARITY": "Similar historical events",
    "MUD_PROGRAMME": "Mud programme similarity",
    "HOLE_SECTION": "Hole section and bit similarity",
}

#: Signal key → factor code.  Drives which factor rows a profile produces.
SIGNAL_CODES: dict[str, str] = {
    "formation_similarity": "FORMATION_MATCH",
    "depth_similarity": "DEPTH_OVERLAP",
    "spatial_proximity": "SPATIAL_PROXIMITY",
    "event_similarity": "EVENT_SIMILARITY",
    "mud_similarity": "MUD_PROGRAMME",
    "section_similarity": "HOLE_SECTION",
}

#: Factor code → label, exposed under the name the UI/tests look for.
SIGNAL_LABELS = FACTOR_LABELS


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp ``value`` into ``[low, high]``."""
    return max(low, min(high, value))


def normalize_weights(weights: dict[str, Any] | None) -> dict[str, float]:
    """Return a complete, non-negative, sum-1 weight dict.

    Missing keys fall back to the prototype default, negative values are clamped
    to 0 and a total of 0 falls back to the defaults entirely.  This keeps
    ``relevance_score`` inside ``[0, 1]`` no matter what a client posts.

    Args:
        weights: Partial or complete weight mapping, possibly with junk values.

    Returns:
        A dict with exactly :data:`RELEVANCE_WEIGHT_KEYS`, summing to 1.0.
    """
    merged = {key: float(DEFAULT_RELEVANCE_WEIGHTS[key]) for key in RELEVANCE_WEIGHT_KEYS}
    if weights:
        for key in RELEVANCE_WEIGHT_KEYS:
            raw = weights.get(key)
            if raw is None:
                continue
            try:
                merged[key] = max(0.0, float(raw))
            except (TypeError, ValueError):
                continue
    total = sum(merged.values())
    if total <= 0:
        return dict(DEFAULT_RELEVANCE_WEIGHTS)
    return {key: round(value / total, 6) for key, value in merged.items()}


def normalize_weights(
    weights: dict[str, Any] | None,
    keys: Sequence[str] = RELEVANCE_WEIGHT_KEYS,
    defaults: dict[str, float] | None = None,
) -> dict[str, float]:
    """Return a complete, non-negative, sum-1 weight dict over ``keys``.

    Missing keys fall back to the matching default, negative values are clamped
    to 0 and a total of 0 falls back to the defaults entirely.  This keeps
    ``relevance_score`` inside ``[0, 1]`` no matter what a client posts.

    Args:
        weights: Partial or complete weight mapping, possibly with junk values.
        keys: The feature set to produce.  Defaults to the four base signals so
            the public config contract is unchanged; a hazard profile passes its
            own feature list.
        defaults: Per-key fallback.  Defaults to
            :data:`DEFAULT_RELEVANCE_WEIGHTS` for the base keys and to 0.0 for
            any extra feature, so an override that omits an optional signal
            contributes nothing rather than an invented number.

    Returns:
        A dict with exactly ``keys``, summing to 1.0.
    """
    base = defaults if defaults is not None else DEFAULT_RELEVANCE_WEIGHTS
    merged = {
        key: max(0.0, float(base.get(key, 0.0)))
        for key in keys
    }
    if weights:
        for key in keys:
            raw = weights.get(key)
            if raw is None:
                continue
            try:
                merged[key] = max(0.0, float(raw))
            except (TypeError, ValueError):
                continue
    total = sum(merged.values())
    if total <= 0:
        return {key: round(float(base.get(key, 0.0)), 6) for key in keys}
    scaled = {key: value / total for key, value in merged.items()}
    #: Absorb the 6-dp rounding residue into the largest weight so the published
    #: weights still sum to exactly 1.0, as documented. Without this the
    #: factors' ``sum(contribution)`` drifts off ``relevance_score`` by ~1e-6.
    rounded = {key: round(value, 6) for key, value in scaled.items()}
    largest = max(rounded, key=lambda key: (rounded[key], key))
    rounded[largest] = round(1.0 - sum(v for k, v in rounded.items() if k != largest), 6)
    return rounded


def relevance_band(score: float) -> str:
    """Map a relevance score to its band label."""
    for label, threshold in RELEVANCE_BANDS:
        if score >= threshold:
            return label
    return "MINIMAL"


def _trajectory_points(well: Well) -> list[dict[str, float]]:
    """Return the well trajectory as plain dicts (the JSON column shape)."""
    return [p for p in (well.trajectory or []) if isinstance(p, dict)]


def tvd_range(well: Well) -> tuple[float, float]:
    """Return the ``(min_tvd, max_tvd)`` interval drilled by ``well``."""
    if well.current_depth_md <= 0:
        return (0.0, 0.0)
    points = _trajectory_points(well)
    tvds = [
        float(p.get("tvd", 0.0))
        for p in points
        if float(p.get("md", 0.0)) <= well.current_depth_md
    ]
    if not tvds:
        return (0.0, round(well.current_tvd, 2))
    return (round(min(tvds), 2), round(max(tvds), 2))





def wells_formations_spanned(
    well: Well, formations: Sequence[Formation]
) -> list[Formation]:
    """Return every formation whose depth range intersects the drilled interval."""
    low, high = tvd_range(well)
    if high <= low:
        return []
    return [f for f in formations if f.bottom_depth > low and f.top_depth < high]


def _event_type_weights(well: Well, events: Sequence[DrillingEvent]) -> dict[str, float]:
    """Return ``{event_type: max severity_weight}`` for a well's events."""
    weights: dict[str, float] = {}
    for event in events:
        spec_weight = severity_weight(event.event_type)
        current = weights.get(event.event_type, 0.0)
        if spec_weight > current:
            weights[event.event_type] = spec_weight
    return weights


def _formation_component(
    current_formation: Formation,
    offset_formations: Sequence[Formation],
    current_tvd: float,
) -> tuple[float, str]:
    """Return ``(similarity, detail)`` for the formation signal."""
    if not offset_formations:
        return 0.0, "Offset well has no drilled formation interval in the record."
    if current_formation.id in {f.id for f in offset_formations}:
        return 1.0, (
            f"Same formation at current TVD — {current_formation.name} (current) vs "
            f"{current_formation.name} (offset)"
        )
    offset_names = ", ".join(sorted(f.name for f in offset_formations))
    return 0.0, (
        f"Different formation at current TVD — {current_formation.name} (current) vs "
        f"{offset_names} (offset)"
    )


def _depth_component(
    current_tvd: float, offset_min_tvd: float, offset_max_tvd: float
) -> tuple[float, str]:
    """Return ``(similarity, detail)`` for the depth signal."""
    if offset_max_tvd <= 0:
        return 0.0, "Offset well has no drilled depth in the record."
    if offset_max_tvd >= current_tvd:
        gap_below = round(offset_max_tvd - current_tvd, 1)
        over = max(0.0, offset_max_tvd - current_tvd - 1500.0)
        penalty = min(0.4, over / 4000.0)
        similarity = round(1.0 - penalty, 4)
        detail = (
            f"Offset drilled to {offset_max_tvd:,.0f} m TVD, {gap_below:,.0f} m below the "
            f"current {current_tvd:,.0f} m TVD"
        )
        if penalty > 0:
            detail += f" (depth excess penalty {penalty:.2f})"
        return similarity, detail
    gap_above = round(current_tvd - offset_max_tvd, 1)
    similarity = round(_clamp(1.0 - gap_above / 1500.0), 4)
    return similarity, (
        f"Offset only reached {offset_max_tvd:,.0f} m TVD, {gap_above:,.0f} m shallower "
        f"than the current {current_tvd:,.0f} m TVD"
    )


def _spatial_component(distance_km: float, radius_km: float) -> tuple[float, str]:
    """Return ``(similarity, detail)`` for the surface-distance signal."""
    if radius_km <= 0:
        return 0.0, "No search radius configured."
    similarity = round(_clamp(1.0 - distance_km / radius_km), 4)
    return similarity, f"{distance_km:.2f} km away (search radius {radius_km:.1f} km)"


def _event_component(
    current_types: dict[str, float], offset_types: dict[str, float]
) -> tuple[float, int, str]:
    """Return ``(similarity, shared_count, detail)`` for the event-history signal."""
    if not current_types or not offset_types:
        return 0.0, 0, "No comparable event history in the record."
    shared = set(current_types) & set(offset_types)
    union = set(current_types) | set(offset_types)
    numerator = sum(current_types[t] for t in shared)
    denominator = sum(current_types.get(t, 0.0) for t in union) + sum(
        offset_types.get(t, 0.0) for t in union
    )
    similarity = round(_clamp(numerator / denominator) if denominator else 0.0, 4)
    if shared:
        labels = ", ".join(sorted(shared))
        detail = f"{len(shared)} similar historical event type(s): {labels}"
    else:
        detail = "No shared event types with the current well"
    return similarity, len(shared), detail


def _mud_component(current_well: Well, offset_well: Well) -> tuple[float, str]:
    """Return ``(similarity, detail)`` for the mud-programme signal.

    Mud-system identity dominates, blended with mud-weight proximity: two wells
    on the same system at a similar weight share the leak-off and ECD behaviour
    that decides losses, kicks and differential sticking.  The weight term is
    scaled over a 2 ppg span, which is a normal adjustment step.

    A missing mud programme is reported as absent, never imputed: the NPD well
    headers publish no mud system or mud weight, so this signal is honestly
    ``0.0`` with a "not recorded" detail for the real corpus rather than a
    fabricated value.
    """
    current_system = (current_well.mud_system or "").strip()
    offset_system = (offset_well.mud_system or "").strip()
    if not current_system or not offset_system:
        missing = current_well.id if not current_system else offset_well.id
        return 0.0, (
            f"No mud programme recorded for {missing} in the source record — "
            f"mud similarity not evaluated."
        )
    system_similarity = 1.0 if current_system.lower() == offset_system.lower() else 0.0
    current_weight = float(current_well.mud_weight_ppg or 0.0)
    offset_weight = float(offset_well.mud_weight_ppg or 0.0)
    if current_weight <= 0.0 or offset_weight <= 0.0:
        detail = (
            f"Same mud system ({current_system}) but no mud weight recorded — "
            f"system identity only"
        )
        return round(system_similarity, 4), detail
    weight_similarity = round(_clamp(1.0 - abs(current_weight - offset_weight) / 2.0), 4)
    similarity = round(0.6 * system_similarity + 0.4 * weight_similarity, 4)
    if system_similarity:
        detail = (
            f"Same mud system ({current_system}); mud weight "
            f"{current_weight:.1f} vs {offset_weight:.1f} ppg"
        )
    else:
        detail = (
            f"Different mud system — {current_system} (current) vs "
            f"{offset_system} (offset)"
        )
    return similarity, detail


def _section_component(current_well: Well, offset_well: Well) -> tuple[float, str]:
    """Return ``(similarity, detail)`` for the hole-section signal.

    Section size and bit size are both part of the picture: torque, drag and
    pack-off are section-specific, and a mismatch on either one changes the
    contact mechanics the hazard acts through.  Each recorded attribute votes
    equally; an unrecorded one is skipped and the detail says so.

    The NPD well headers publish neither, so on the real corpus this reports
    ``0.0`` with an explicit absence detail instead of guessing a hole size.
    """
    votes: list[float] = []
    parts: list[str] = []
    for attribute, label in (("section_size", "section"), ("bit_size", "bit")):
        current_value = (getattr(current_well, attribute) or "").strip()
        offset_value = (getattr(offset_well, attribute) or "").strip()
        if not current_value or not offset_value:
            missing = current_well.id if not current_value else offset_well.id
            parts.append(f"{label} size not recorded for {missing}")
            continue
        match = current_value.lower() == offset_value.lower()
        votes.append(1.0 if match else 0.0)
        parts.append(
            f"{current_value} {label} (current) vs {offset_value} {label} (offset)"
        )
    if not votes:
        return 0.0, "Hole section not recorded for either well — not evaluated."
    similarity = round(sum(votes) / len(votes), 4)
    return similarity, "; ".join(parts)

def score_offset_well(
    current_well: Well,
    offset_well: Well,
    formations: Sequence[Formation],
    weights: dict[str, float] | None = None,
    radius_km: float = DEFAULT_RADIUS_KM,
    current_events: Sequence[DrillingEvent] = (),
    offset_events: Sequence[DrillingEvent] = (),
    hazard: str | None = None,
) -> dict[str, Any]:
    """Score one offset well against the current well.

    The weighting comes from the AHP profile for ``hazard``; with no hazard the
    general profile applies.  Passing ``weights`` overrides the profile's
    weights for this call only, which is how the persisted config and a manual
    POST still take effect.

    Args:
        current_well: The well being drilled.
        offset_well: Candidate offset well.
        formations: All formations, used for range lookups.
        weights: Optional weight override; normalised internally. ``None`` means
            "use the profile's AHP weights".
        radius_km: Search radius used by the spatial signal.
        current_events: Current well's events (for the event signal).
        offset_events: Offset well's events (for the event signal).
        hazard: Hazard being screened, e.g. ``"stuck_pipe"``. An unknown hazard
            falls back to the general profile rather than failing the screen.

    Returns:
        A dict with ``distance_km``, ``relevance_score``, ``relevance_band``,
        ``similarity``, ``factors``, ``why_relevant``, ``event_count``,
        ``depth_range``, ``formations``, ``formation_match``, ``ahp_hazard``,
        ``ahp_consistency_ratio`` and ``ahp_lambda_max``.
    """
    profile, features, normalized = resolve_scoring(hazard, weights)
    distance_km = haversine_km(
        current_well.latitude,
        current_well.longitude,
        offset_well.latitude,
        offset_well.longitude,
    )
    current_formation = current_well.formation
    if current_formation is None:
        current_formation = _formation_at(formations, current_well.current_tvd)
    current_tvd = current_well.current_tvd

    offset_formations = wells_formations_spanned(offset_well, formations)
    offset_min_tvd, offset_max_tvd = tvd_range(offset_well)

    formation_sim, formation_detail = _formation_component(
        current_formation, offset_formations, current_tvd
    )
    depth_sim, depth_detail = _depth_component(current_tvd, offset_min_tvd, offset_max_tvd)
    spatial_sim, spatial_detail = _spatial_component(distance_km, radius_km)
    event_sim, shared_count, event_detail = _event_component(
        _event_type_weights(current_well, current_events),
        _event_type_weights(offset_well, offset_events),
    )
    mud_sim, mud_detail = _mud_component(current_well, offset_well)
    section_sim, section_detail = _section_component(current_well, offset_well)

    signals: dict[str, tuple[float, str]] = {
        "formation_similarity": (formation_sim, formation_detail),
        "depth_similarity": (depth_sim, depth_detail),
        "spatial_proximity": (spatial_sim, spatial_detail),
        "event_similarity": (event_sim, event_detail),
        "mud_similarity": (mud_sim, mud_detail),
        "section_similarity": (section_sim, section_detail),
    }
    #: ``similarity`` holds exactly the features this profile scored, so it is
    #: the score's decomposition: ``sum(w * v) == relevance_score``.
    similarity = {key: signals[key][0] for key in features}
    details = {key: signals[key][1] for key in features}
    score = sum(normalized[key] * similarity[key] for key in features)
    score = round(_clamp(score), 4)

    factors: list[dict[str, Any]] = []
    for key in features:
        code = SIGNAL_CODES[key]
        value = similarity[key]
        factors.append(
            {
                "code": code,
                "label": FACTOR_LABELS[code],
                "value": value,
                "weight": normalized[key],
                "contribution": round(normalized[key] * value, 4),
                "detail": details[key],
            }
        )

    why_relevant: list[str] = [factor["label"] for factor in factors if factor["value"] >= 0.5]
    if formation_sim >= 0.99:
        why_relevant.insert(
            0, f"Same formation at current TVD — {current_formation.name} (current) vs "
               f"{current_formation.name} (offset)"
        )
    why_relevant.append(spatial_detail)
    if depth_sim > 0:
        why_relevant.append(depth_detail)
    if shared_count:
        why_relevant.append(event_detail)
    # Preserve order while removing duplicates.
    seen: set[str] = set()
    why_relevant = [item for item in why_relevant if not (item in seen or seen.add(item))]

    return {
        "offset_well_id": offset_well.id,
        "distance_km": distance_km,
        "relevance_score": score,
        "relevance_band": relevance_band(score),
        "similarity": similarity,
        "factors": factors,
        "why_relevant": why_relevant,
        "event_count": len(offset_events),
        "depth_range": {
            "min_md": 0.0,
            "max_md": offset_well.current_depth_md,
            "max_tvd": offset_max_tvd,
            "min_tvd": offset_min_tvd,
        },
        "formations": [f.name for f in offset_formations],
        "formation_match": "SAME"
        if current_formation.id in {f.id for f in offset_formations}
        else ("ADJACENT" if offset_formations else "NONE"),
        "ahp_hazard": profile["hazard"],
        "ahp_consistency_ratio": profile["consistency_ratio"],
        "ahp_lambda_max": profile["lambda_max"],
        "ahp_features": list(features),
        "ahp_method": profile["method"],
        "weights_source": "AHP_PROFILE" if weights is None else "CONFIG_OVERRIDE",
    }


def resolve_scoring(
    hazard: str | None = None, weights: dict[str, Any] | None = None
) -> tuple[dict[str, Any], tuple[str, ...], dict[str, float]]:
    """Resolve which features to score and the AHP weights to score them with.

    Without a ``weights`` override the whole profile applies, extras included,
    because the AHP judgements were made about exactly those features.  With an
    override the scored set is narrowed to the features the override actually
    names, so a persisted four-key config keeps producing the four cached base
    factors rather than silently blending profile weights into a manual setting.

    Args:
        hazard: Hazard being screened; unknown values fall back to general.
        weights: Optional weight override.

    Returns:
        ``(profile, features, normalized_weights)``.
    """
    profile = profile_for(hazard)
    features = tuple(profile["features"])
    if not weights:
        return profile, features, normalize_weights(None, features, profile["weights"])
    override_keys = tuple(key for key in features if key in weights)
    extra_keys = tuple(
        key
        for key in weights
        if key not in override_keys and key in COMPUTABLE_FEATURES
    )
    keys = override_keys + extra_keys or RELEVANCE_WEIGHT_KEYS
    return profile, keys, normalize_weights(weights, keys, profile["weights"])


def _formation_at(formations: Sequence[Formation], tvd: float) -> Formation:
    """Range lookup: return the formation containing ``tvd``."""
    for formation in sorted(formations, key=lambda f: f.top_depth):
        if formation.top_depth <= tvd < formation.bottom_depth:
            return formation
    if not formations:
        raise ValueError("No formations registered")
    return formations[0]


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

CONFIG_KEY = "relevance"


def get_relevance_config(session: Session) -> dict[str, Any]:
    """Return the persisted relevance configuration or the prototype defaults.

    The AHP block is always included: which profiles exist, which one supplies
    the default weights, and its consistency ratio, so a client can tell that
    the numbers behind a relevance score came from a coherent judgement set.
    """
    from .models import AppConfig

    profile = profile_for(None)
    ahp_block: dict[str, Any] = {
        "weights_source": "AHP_PRINCIPAL_EIGENVECTOR",
        "profiles": sorted(DEFAULT_PROFILES),
        "default_hazard": profile["hazard"],
        "consistency_ratio": profile["consistency_ratio"],
        "cr_threshold": CR_THRESHOLD,
    }
    row = session.scalar(select(AppConfig).where(AppConfig.key == CONFIG_KEY))
    if row is None:
        return {
            "weights": dict(DEFAULT_RELEVANCE_WEIGHTS),
            "radius_km": DEFAULT_RADIUS_KM,
            "min_relevance": DEFAULT_MIN_RELEVANCE,
            "weights_explanation": WEIGHTS_EXPLANATION,
            "ahp": ahp_block,
            "version": "1",
        }
    value = dict(row.value or {})
    return {
        "weights": normalize_weights(value.get("weights")),
        "radius_km": float(value.get("radius_km", DEFAULT_RADIUS_KM)),
        "min_relevance": float(value.get("min_relevance", DEFAULT_MIN_RELEVANCE)),
        "weights_explanation": WEIGHTS_EXPLANATION,
        "ahp": ahp_block,
        "version": row.version,
    }


def set_relevance_config(
    session: Session,
    weights: dict[str, Any] | None = None,
    radius_km: float | None = None,
    min_relevance: float | None = None,
    version: str = "1",
) -> dict[str, Any]:
    """Persist the relevance configuration and refresh the relation cache."""
    from .models import AppConfig

    current = get_relevance_config(session)
    payload = {
        "weights": normalize_weights(weights if weights is not None else current["weights"]),
        "radius_km": float(radius_km if radius_km is not None else current["radius_km"]),
        "min_relevance": float(
            min_relevance if min_relevance is not None else current["min_relevance"]
        ),
    }
    row = session.scalar(select(AppConfig).where(AppConfig.key == CONFIG_KEY))
    if row is None:
        row = AppConfig(key=CONFIG_KEY, value=payload, version=version, description=WEIGHTS_EXPLANATION)
        session.add(row)
    else:
        row.value = payload
        row.version = version
        row.description = WEIGHTS_EXPLANATION
    session.flush()
    return get_relevance_config(session)


def refresh_relations(
    session: Session,
    weights: dict[str, Any] | None = None,
    radius_km: float | None = None,
    min_relevance: float | None = None,
    hazard: str | None = None,
) -> int:
    """Rebuild the whole ``offset_relations`` cache for every well.

    Each well is scored against every other well, so the response for any well
    (current or offset) is a table lookup rather than an O(n²) recompute.

    Args:
        session: Open session; the caller commits.
        weights: Weight override.  ``None`` scores with the AHP profile for
            ``hazard``; naming ``hazard`` takes precedence over the persisted
            config, and naming neither falls back to the persisted config.
        radius_km: Radius override, or ``None`` for the persisted value.
        min_relevance: When given, relations below the threshold are not stored
            (the cache then only holds relevant offsets).
        hazard: Hazard whose AHP profile weights the cache.  ``None`` uses the
            general profile.

    Returns:
        The number of relation rows written.
    """
    config = get_relevance_config(session)
    effective_weights = weights
    if effective_weights is None and hazard is None:
        effective_weights = normalize_weights(config["weights"])
    #: Persist the AHP registry alongside the relations so the API and UI can
    #: read the weights and their CR without recomputing them.
    ensure_profiles(session)
    effective_radius = float(radius_km if radius_km is not None else config["radius_km"])
    threshold = (
        float(min_relevance) if min_relevance is not None else config["min_relevance"]
    )

    wells = list(session.scalars(select(Well).order_by(Well.id)))
    formations = list(session.scalars(select(Formation).order_by(Formation.top_depth)))
    events_by_well: dict[str, list[DrillingEvent]] = {}
    for event in session.scalars(select(DrillingEvent).order_by(DrillingEvent.id)):
        events_by_well.setdefault(event.well_id, []).append(event)

    # Flush anything pending before snapshotting the existing pairs, so a
    # caller that refreshes twice in one session updates rather than re-inserts.
    session.flush()
    existing = {
        (row.current_well_id, row.offset_well_id): row
        for row in session.scalars(select(OffsetRelation))
    }
    seen_pairs: set[tuple[str, str]] = set()
    written = 0
    for current in wells:
        current_events = events_by_well.get(current.id, [])
        for offset in wells:
            if offset.id == current.id:
                continue
            result = score_offset_well(
                current,
                offset,
                formations,
                effective_weights,
                radius_km=effective_radius,
                current_events=current_events,
                offset_events=events_by_well.get(offset.id, []),
                hazard=hazard,
            )
            if result["relevance_score"] < threshold:
                continue
            pair = (current.id, offset.id)
            seen_pairs.add(pair)
            row = existing.get(pair)
            if row is None:
                row = OffsetRelation(current_well_id=current.id, offset_well_id=offset.id)
                session.add(row)
            row.distance_km = result["distance_km"]
            row.relevance_score = result["relevance_score"]
            row.relevance_band = result["relevance_band"]
            row.formation_similarity = result["similarity"]["formation_similarity"]
            row.depth_similarity = result["similarity"]["depth_similarity"]
            row.spatial_proximity = result["similarity"]["spatial_proximity"]
            row.event_similarity = result["similarity"]["event_similarity"]
            row.factors = result["factors"]
            row.why_relevant = result["why_relevant"]
            row.event_count = result["event_count"]
            row.computed_at = utcnow()
            written += 1

    for pair, row in existing.items():
        if pair not in seen_pairs:
            session.delete(row)
    session.flush()
    return written



def source_availability(
    session: Session, events: Sequence[DrillingEvent]
) -> dict[str, Any]:
    """Summarise how well a set of events is backed by documents and evidence.

    ``coverage`` is ``FULL`` when every event has at least one evidence row,
    ``PARTIAL`` when only some do and ``NONE`` when none do.  The UI shows this so
    an engineer can tell a well-backed answer from a bare one.
    """
    event_ids = [event.id for event in events]
    documents = len({event.document_id for event in events if event.document_id})
    if not event_ids:
        return {"documents": 0, "with_evidence": 0, "coverage": "NONE"}
    rows = session.execute(
        select(Evidence.event_id).where(Evidence.event_id.in_(event_ids)).distinct()
    ).scalars()
    with_evidence = len(set(rows))
    if with_evidence >= len(event_ids):
        coverage = "FULL"
    elif with_evidence > 0:
        coverage = "PARTIAL"
    else:
        coverage = "NONE"
    return {
        "documents": documents,
        "with_evidence": with_evidence,
        "coverage": coverage,
    }

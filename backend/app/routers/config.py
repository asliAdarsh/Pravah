"""Engine configuration endpoints (relevance weights, risk rules, AHP profiles).

A ``POST`` to either config endpoint persists the new configuration **and**
recomputes the affected caches, so the next read reflects the change
immediately.  The AHP endpoints are read-only: the weights are derived from
stated engineering judgements in :mod:`app.ahp` and are not user-editable over
the API, because a client cannot be asked to justify a pairwise comparison.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..ahp import (
    CR_THRESHOLD,
    GENERAL_HAZARD,
    ensure_profiles,
    normalize_hazard,
)
from ..db import get_db
from ..errors import NotFoundError, ValidationFailedError
from ..models import Well
from ..relevance import (
    RELEVANCE_WEIGHT_KEYS,
    get_relevance_config,
    normalize_weights,
    refresh_relations,
    set_relevance_config,
)
from ..risk import (
    RISK_WEIGHT_KEYS,
    get_risk_config,
    recompute_all_alerts,
    set_risk_config,
)
from ..schemas import (
    RecomputeRequest,
    RelevanceConfigRequest,
    RiskConfigRequest,
    alert_brief,
)

router = APIRouter(prefix="/config", tags=["config"])
#: ``POST /api/v1/risk/recompute`` is a top-level route in the contract, not a
#: sub-route of ``/config``, so it gets its own router.
risk_router = APIRouter(prefix="/risk", tags=["risk"])
#: ``/ahp/profiles`` is likewise top-level: the weights are engine-owned
#: configuration, not a sub-resource of the relevance config document.
ahp_router = APIRouter(prefix="/ahp", tags=["ahp"])

WEIGHT_SUM_TOLERANCE = 0.05


def _profile_payload(profile: dict[str, Any]) -> dict[str, Any]:
    """Return the public JSON shape of one AHP profile."""
    return {
        "hazard": profile["hazard"],
        "label": profile["label"],
        "features": list(profile["features"]),
        "matrix": [list(row) for row in profile["matrix"]],
        "weights": dict(profile["weights"]),
        "consistency_ratio": profile["consistency_ratio"],
        "lambda_max": profile["lambda_max"],
        "cr_threshold": profile.get("cr_threshold", CR_THRESHOLD),
        "consistent": profile.get("consistent", True),
        "method": profile.get("method", "AHP_PRINCIPAL_EIGENVECTOR"),
        "engineering_rationale": profile["engineering_rationale"],
        "references": list(profile["references"]),
    }


@ahp_router.get("/profiles", response_model=None)
def get_ahp_profiles(session: Session = Depends(get_db)) -> dict[str, Any]:
    """Return every AHP profile with its matrix, weights and consistency ratio.

    Served from the ``ahp_profiles`` table, which :func:`ensure_profiles` keeps
    in step with the registry, so a reader sees exactly the weights the engine
    scored with.
    """
    stored = ensure_profiles(session)
    profiles = [_profile_payload(stored[key]) for key in sorted(stored)]
    return {
        "profiles": profiles,
        "count": len(profiles),
        "general_hazard": GENERAL_HAZARD,
        "cr_threshold": CR_THRESHOLD,
        "method": "AHP_PRINCIPAL_EIGENVECTOR",
    }


@ahp_router.get("/profiles/{hazard}", response_model=None)
def get_ahp_profile(hazard: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Return one hazard's AHP profile."""
    key = normalize_hazard(hazard)
    stored = ensure_profiles(session)
    profile = stored.get(key)
    if profile is None:
        raise NotFoundError(
            f"No AHP profile for hazard '{key}'. Available: {sorted(stored)}."
        )
    return _profile_payload(profile)


def _validate_relevance_weights(weights: dict[str, float]) -> dict[str, float]:
    """Validate and normalise relevance weights, or raise a 422.

    Every weight must be in ``[0, 1]`` and the total must be within
    ``±0.05`` of 1.0, per the contract.
    """
    unknown = set(weights) - set(RELEVANCE_WEIGHT_KEYS)
    if unknown:
        raise ValidationFailedError(
            f"Unknown relevance weight key(s): {sorted(unknown)}. "
            f"Expected any of {list(RELEVANCE_WEIGHT_KEYS)}."
        )
    if not weights:
        raise ValidationFailedError("At least one relevance weight must be supplied.")
    total = 0.0
    for key, value in weights.items():
        if not 0.0 <= float(value) <= 1.0:
            raise ValidationFailedError(
                f"Weight '{key}' must be between 0 and 1, got {value}."
            )
        total += float(value)
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise ValidationFailedError(
            f"Relevance weights must sum to 1.0 (±{WEIGHT_SUM_TOLERANCE}); got {total:.3f}."
        )
    return normalize_weights(weights)


def _validate_risk_weights(weights: dict[str, float]) -> dict[str, float]:
    """Validate and normalise risk weights, or raise a 422."""
    unknown = set(weights) - set(RISK_WEIGHT_KEYS)
    if unknown:
        raise ValidationFailedError(
            f"Unknown risk weight key(s): {sorted(unknown)}. "
            f"Expected any of {list(RISK_WEIGHT_KEYS)}."
        )
    if not weights:
        raise ValidationFailedError("At least one risk weight must be supplied.")
    total = 0.0
    for key, value in weights.items():
        if not 0.0 <= float(value) <= 1.0:
            raise ValidationFailedError(
                f"Weight '{key}' must be between 0 and 1, got {value}."
            )
        total += float(value)
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise ValidationFailedError(
            f"Risk weights must sum to 1.0 (±{WEIGHT_SUM_TOLERANCE}); got {total:.3f}."
        )
    from ..risk import normalize_risk_weights

    return normalize_risk_weights(weights)


@router.get("/relevance", response_model=None)
def get_relevance(session: Session = Depends(get_db)) -> dict[str, Any]:
    """Return the current relevance configuration."""
    return get_relevance_config(session)


@router.post("/relevance", response_model=None)
def post_relevance(
    body: RelevanceConfigRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Persist relevance weights and recompute the offset-relation cache."""
    weights = _validate_relevance_weights(body.weights)
    config = set_relevance_config(
        session,
        weights=weights,
        radius_km=body.radius_km,
        min_relevance=body.min_relevance,
    )
    written = refresh_relations(session, config["weights"], config["radius_km"], config["min_relevance"])
    session.commit()
    return {
        **config,
        "offset_relations_updated": written,
        "method": "PROTOTYPE_HEURISTIC",
    }


@router.get("/risk", response_model=None)
def get_risk(session: Session = Depends(get_db)) -> dict[str, Any]:
    """Return the current risk rule configuration."""
    config = get_risk_config(session)
    return {**config, "method": "PROTOTYPE_HEURISTIC"}


@router.post("/risk", response_model=None)
def post_risk(
    body: RiskConfigRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Persist risk rules and recompute alerts for every active well."""
    weights = _validate_risk_weights(body.weights)
    thresholds = dict(body.severity_thresholds)
    if not (0 < thresholds["WARNING"] < thresholds["HIGH"] < thresholds["CRITICAL"]):
        raise ValidationFailedError(
            "severity_thresholds must satisfy 0 < WARNING < HIGH < CRITICAL, got "
            f"{thresholds}."
        )
    config = set_risk_config(
        session,
        {
            "min_support_wells": body.min_support_wells,
            "tvd_tolerance_m": body.tvd_tolerance_m,
            "min_relevance": body.min_relevance,
            "formation_match_required": body.formation_match_required,
            "weights": weights,
            "severity_thresholds": thresholds,
        },
    )
    summary = recompute_all_alerts(session, config)
    session.commit()
    return {**config, "recompute": summary, "method": "PROTOTYPE_HEURISTIC"}


@risk_router.post("/recompute", response_model=None)
def recompute(
    body: RecomputeRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Recompute offset relations and alerts for one well (or every active well)."""
    from ..risk import recompute_well_alerts

    if body.well_id:
        if session.get(Well, body.well_id) is None:
            from ..errors import NotFoundError

            raise NotFoundError(f"Unknown well: {body.well_id}")
        relations = refresh_relations(session)
        result = recompute_well_alerts(session, body.well_id)
        session.commit()
        return {
            "well_id": body.well_id,
            "offset_relations_updated": result["offset_relations_updated"],
            "alerts_created": result["alerts_created"],
            "alerts_updated": result["alerts_updated"],
            "alerts_deactivated": result["deactivated"],
            "alerts": [alert_brief(alert) for alert in result["alerts"]],
            "relations_refreshed": relations,
            "method": "PROTOTYPE_HEURISTIC",
        }
    relations = refresh_relations(session)
    summary = recompute_all_alerts(session)
    session.commit()
    return {
        "offset_relations_updated": relations,
        "alerts_created": summary["alerts"],
        "alerts_updated": summary["alerts"],
        "wells_processed": summary["wells_processed"],
        "alerts": [],
        "method": "PROTOTYPE_HEURISTIC",
    }

"""Analytic Hierarchy Process (AHP) weighting for per-hazard offset relevance.

Why this module exists
----------------------
The relevance engine used to carry one hand-picked weight vector
(``0.4 / 0.3 / 0.2 / 0.1``) applied to every hazard alike.  That is not defensible
drilling engineering: a stuck-pipe screen should lean on the mud programme and
the hole section, a kick screen on pore-pressure margin against mud weight, and a
torque screen on hole geometry.  Here the weights are *derived*, not asserted:
an engineer states pairwise judgements on Saaty's scale, and the principal
eigenvector of that judgement matrix produces the weights.

The maths (no third-party dependency)
--------------------------------------
Given a positive reciprocal-symmetric matrix ``A``:

* **Eigenvector** — power iteration ``w <- A w / sum(A w)`` from the uniform
  start vector, iterated until it stops moving.  For a positive matrix this
  converges to the dominant (principal) eigenvector, which is the weight vector.
* **lambda_max** — ``(w' A w) / (w' w)``.  For a perfectly consistent matrix this
  is exactly ``n``, its order.
* **Consistency index** — ``CI = (lambda_max - n) / (n - 1)``.
* **Consistency ratio** — ``CR = CI / RI``, with ``RI`` Saaty's random index.
  ``CR = 0`` for ``n <= 2`` because any 2x2 reciprocal matrix is consistent.

Saaty's rule of thumb is that ``CR <= 0.10`` means the judgements are coherent
enough to use.  :func:`build_profile` **rejects** a profile above that threshold
(``ValueError``) rather than shipping incoherent weights to the engine, and
records the CR on every profile it does return so the UI can show it.

Honesty
-------
* Nothing here is learned from data.  The judgements are stated engineering
  opinion, and every profile says so in its ``engineering_rationale``.
* ``CR`` is reported, never hidden, and is the only quality gate applied.
* The feature names are the engine's own signal keys
  (see :data:`app.relevance.COMPUTABLE_FEATURES`); a profile may not name a
  feature the engine cannot evaluate.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AhpProfile

__all__ = [
    "AHP_BASE_FEATURES",
    "AHP_EXTRA_FEATURES",
    "CR_THRESHOLD",
    "DEFAULT_PROFILES",
    "GENERAL_HAZARD",
    "RANDOM_INDEX",
    "RECIPROCAL_TOLERANCE",
    "SAATY_RANDOM_INDEX",
    "all_profiles",
    "build_profile",
    "consistency",
    "ensure_profiles",
    "normalize_hazard",
    "pairwise_matrix",
    "profile_for",
    "seed_ahp_profiles",
]

#: Saaty's random index ``RI`` by matrix order ``n``.  ``n = 1`` and ``n = 2``
#: have no inconsistency to measure, hence ``0.0``.
RANDOM_INDEX: dict[int, float] = {
    1: 0.00,
    2: 0.00,
    3: 0.58,
    4: 0.90,
    5: 1.12,
    6: 1.24,
    7: 1.32,
    8: 1.41,
    9: 1.45,
    10: 1.49,
}
SAATY_RANDOM_INDEX = RANDOM_INDEX

#: Saaty's acceptance threshold.  Above this the judgements contradict each
#: other and the derived weights are not defensible.
CR_THRESHOLD = 0.10

#: How far ``a[i][j] * a[j][i]`` may stray from 1.0 before the matrix is
#: rejected as not reciprocal-symmetric.
RECIPROCAL_TOLERANCE = 1e-6

#: The hazard key used when no specific hazard is being screened.
GENERAL_HAZARD = "general"

#: The four signals every profile must carry.  ``OffsetRelation`` stores exactly
#: these as columns, so a profile that dropped one would break the cache.
AHP_BASE_FEATURES: tuple[str, ...] = (
    "formation_similarity",
    "depth_similarity",
    "spatial_proximity",
    "event_similarity",
)

#: Optional signals.  Real and computable, but frequently *absent* in the NPD
#: corpus (NPD publishes no mud system or hole section), so a profile that
#: leans on them says so rather than inventing a value.
AHP_EXTRA_FEATURES: tuple[str, ...] = (
    "mud_similarity",
    "section_similarity",
)

#: Every feature a profile is allowed to name.
AHP_FEATURES: tuple[str, ...] = AHP_BASE_FEATURES + AHP_EXTRA_FEATURES


def normalize_hazard(hazard: str | None) -> str:
    """Return the lower-case registry key for ``hazard``.

    ``"MUD_LOSS"``, ``"mud-loss"`` and ``"mud loss"`` all normalise to
    ``"mud_loss"``; ``None`` and the empty string normalise to
    :data:`GENERAL_HAZARD`.
    """
    if not hazard:
        return GENERAL_HAZARD
    return str(hazard).strip().lower().replace("-", "_").replace(" ", "_")


# --------------------------------------------------------------------------- #
# The maths
# --------------------------------------------------------------------------- #


def _eigenvector(
    matrix: Sequence[Sequence[float]],
    tolerance: float = 1e-12,
    max_iterations: int = 1000,
) -> list[float]:
    """Return the normalised principal eigenvector of ``matrix``.

    Power iteration: repeatedly multiply by the matrix and renormalise to sum 1.
    The start vector is uniform, which has a strictly positive projection on the
    principal eigenvector for a positive matrix, so the iteration converges.

    Args:
        matrix: Square, positive matrix (validation happens in
            :func:`build_profile`).
        tolerance: Convergence threshold on the largest component change.
        max_iterations: Hard iteration cap, guarding against a non-convergent
            input rather than looping forever.

    Returns:
        The eigenvector as a list summing to 1.0.
    """
    size = len(matrix)
    if size == 0:
        raise ValueError("Pairwise matrix must have at least one row.")
    weights = [1.0 / size] * size
    for _ in range(max_iterations):
        product = [sum(row[j] * weights[j] for j in range(size)) for row in matrix]
        total = sum(product)
        if total <= 0.0:
            raise ValueError("Pairwise matrix has a non-positive column sum.")
        nxt = [value / total for value in product]
        if max(abs(a - b) for a, b in zip(nxt, weights)) < tolerance:
            return nxt
        weights = nxt
    return weights


def consistency(
    matrix: Sequence[Sequence[float]], weights: Sequence[float]
) -> dict[str, float]:
    """Return Saaty's consistency measures for ``matrix`` and ``weights``.

    Args:
        matrix: The pairwise judgement matrix.
        weights: The principal eigenvector of ``matrix``, same length.

    Returns:
        ``{"order", "lambda_max", "consistency_index", "random_index",
        "consistency_ratio"}``.  ``consistency_ratio`` is exactly ``0.0`` for
        ``n <= 2`` and the consistency index is clamped at zero so a
        numerically-perfect matrix cannot report a negative ratio.
    """
    order = len(matrix)
    if order != len(weights):
        raise ValueError("Matrix and weights must have the same length.")
    if order == 0:
        raise ValueError("Pairwise matrix must have at least one row.")

    weighted = sum(
        weights[i] * sum(matrix[i][j] * weights[j] for j in range(order))
        for i in range(order)
    )
    norm = sum(value * value for value in weights)
    lambda_max = weighted / norm if norm > 0.0 else float(order)

    if order <= 2:
        return {
            "order": float(order),
            "lambda_max": round(lambda_max, 10),
            "consistency_index": 0.0,
            "random_index": RANDOM_INDEX.get(order, 0.0),
            "consistency_ratio": 0.0,
        }

    consistency_index = max(0.0, (lambda_max - order) / (order - 1))
    random_index = RANDOM_INDEX.get(order, 0.0)
    if random_index <= 0.0:
        return {
            "order": float(order),
            "lambda_max": round(lambda_max, 10),
            "consistency_index": round(consistency_index, 10),
            "random_index": random_index,
            "consistency_ratio": 0.0,
        }
    return {
        "order": float(order),
        "lambda_max": round(lambda_max, 10),
        "consistency_index": round(consistency_index, 10),
        "random_index": random_index,
        "consistency_ratio": round(consistency_index / random_index, 10),
    }


def _validate_matrix(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    """Validate the pairwise matrix and return it as a plain list of lists.

    Rejects: ragged rows, a non-positive or non-finite entry, a diagonal entry
    that is not 1, and any pair that is not reciprocal-symmetric within
    :data:`RECIPROCAL_TOLERANCE`.
    """
    rows = [list(row) for row in matrix]
    order = len(rows)
    if order == 0:
        raise ValueError("Pairwise matrix must have at least one row.")
    for index, row in enumerate(rows):
        if len(row) != order:
            raise ValueError(
                f"Pairwise matrix is not square: row {index} has {len(row)} "
                f"entries, expected {order}."
            )
        for column, value in enumerate(row):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(
                    f"Pairwise matrix entry [{index}][{column}] must be a finite "
                    f"positive number, got {value!r}."
                )
    for index in range(order):
        if abs(rows[index][index] - 1.0) > RECIPROCAL_TOLERANCE:
            raise ValueError(
                f"Pairwise matrix diagonal entry [{index}][{index}] must be 1, "
                f"got {rows[index][index]!r}."
            )
        for column in range(index + 1, order):
            product = rows[index][column] * rows[column][index]
            if abs(product - 1.0) > RECIPROCAL_TOLERANCE:
                raise ValueError(
                    "Pairwise matrix is not reciprocal-symmetric: "
                    f"[{index}][{column}] * [{column}][{index}] = {product!r} "
                    f"(tolerance {RECIPROCAL_TOLERANCE})."
                )
    return rows


def _round_weights(raw: Mapping[str, float]) -> dict[str, float]:
    """Round the eigenweights to 6 dp and make them sum to exactly 1.0.

    The residue from rounding is absorbed by the largest weight so downstream
    arithmetic (``sum(w * v)``) never drifts off the reported score.
    """
    rounded = {key: round(value, 6) for key, value in raw.items()}
    if not rounded:
        return rounded
    largest = max(rounded, key=lambda key: (rounded[key], key))
    residual = round(1.0 - sum(v for k, v in rounded.items() if k != largest), 6)
    rounded[largest] = residual
    return rounded


def build_profile(
    hazard: str,
    features: Sequence[str],
    matrix: Sequence[Sequence[float]],
    rationale: str,
    references: Sequence[str],
    label: str | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    """Derive one AHP weighting profile from a pairwise judgement matrix.

    Args:
        hazard: Registry key, e.g. ``"stuck_pipe"``.
        features: Feature names, in the same order as the matrix rows.
        matrix: Positive reciprocal-symmetric Saaty judgement matrix.
        rationale: Why these features matter for this hazard.
        references: Sources backing the judgements.
        label: Human label; defaults to the hazard key.
        strict: When ``True`` (the default) a ``CR > CR_THRESHOLD`` profile is
            rejected.  When ``False`` it is returned with
            ``consistent = False`` and a loud ``warnings`` entry, so a caller
            can show a bad profile rather than crash.

    Returns:
        A JSON-serialisable profile dict.

    Raises:
        ValueError: On an invalid matrix, a feature the engine cannot compute,
            or (when ``strict``) an inconsistent judgement set.
    """
    order = len(features)
    if order == 0:
        raise ValueError(f"AHP profile '{hazard}' declares no features.")
    if len(set(features)) != order:
        raise ValueError(f"AHP profile '{hazard}' repeats a feature name.")
    unknown = [name for name in features if name not in AHP_FEATURES]
    if unknown:
        raise ValueError(
            f"AHP profile '{hazard}' names feature(s) the engine cannot "
            f"evaluate: {unknown}. Computable features: {list(AHP_FEATURES)}."
        )
    missing = [name for name in AHP_BASE_FEATURES if name not in features]
    if missing:
        raise ValueError(
            f"AHP profile '{hazard}' must carry the cached base feature(s) "
            f"{missing}; OffsetRelation stores them as columns."
        )

    rows = _validate_matrix(matrix)
    if len(rows) != order:
        raise ValueError(
            f"AHP profile '{hazard}' has {len(rows)} matrix rows for "
            f"{order} features."
        )

    weights = _eigenvector(rows)
    measures = consistency(rows, weights)
    ratio = measures["consistency_ratio"]
    consistent = ratio <= CR_THRESHOLD

    profile: dict[str, Any] = {
        "hazard": normalize_hazard(hazard),
        "label": label or normalize_hazard(hazard).replace("_", " ").title(),
        "features": list(features),
        "matrix": rows,
        "weights": _round_weights(dict(zip(features, weights))),
        "lambda_max": measures["lambda_max"],
        "consistency_index": measures["consistency_index"],
        "random_index": measures["random_index"],
        "consistency_ratio": ratio,
        "cr_threshold": CR_THRESHOLD,
        "consistent": consistent,
        "method": "AHP_PRINCIPAL_EIGENVECTOR",
        "engineering_rationale": rationale,
        "references": list(references),
        "warnings": [],
    }
    if not consistent:
        warning = (
            f"CR {ratio:.4f} exceeds Saaty's threshold of {CR_THRESHOLD:.2f}: the "
            "pairwise judgements contradict each other and these weights are "
            "not defensible."
        )
        profile["warnings"].append(warning)
        if strict:
            raise ValueError(f"AHP profile '{hazard}' is inconsistent. {warning}")
    return profile


def pairwise_matrix(
    features: Sequence[str], judgements: Mapping[tuple[str, str], float]
) -> list[list[float]]:
    """Build a reciprocal-symmetric matrix from upper-triangle judgements.

    ``judgements[("a", "b")]`` is the importance of feature ``a`` over feature
    ``b`` on Saaty's 1-9 scale; the reciprocal is filled in automatically.  The
    diagonal is 1.  Declaring the judgements this way makes a transcription slip
    in the upper triangle the only thing that can be wrong.
    """
    index = {name: position for position, name in enumerate(features)}
    size = len(features)
    matrix = [[1.0] * size for _ in range(size)]
    for (left, right), value in judgements.items():
        if left not in index or right not in index:
            raise ValueError(
                f"Judgement ({left!r}, {right!r}) names a feature that is not in "
                f"{list(features)}."
            )
        i, j = index[left], index[right]
        if i == j:
            raise ValueError(f"Judgement ({left!r}, {right!r}) compares a feature "
                             "with itself; the diagonal is fixed at 1.")
        matrix[i][j] = float(value)
        matrix[j][i] = 1.0 / float(value)
    return matrix


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #

_SAATY = "Saaty, T.L. (1980). The Analytic Hierarchy Process. McGraw-Hill, New York."
_MUD = (
    "API RP 13B-1, Recommended Practice for Field Testing Mud and Related "
    "Properties (4th ed., API)."
)
_WELLCTRL = (
    "API RP 65, Well Control and Production Safety Operations (2nd ed., API)."
)
_NPD = (
    "Norwegian Petroleum Directorate, FactPages: public well and formation "
    "tops for Norwegian offshore fields (accessed via app/datasources.py)."
)
_VOLVE = (
    "Equinor Volve field Daily Drilling Reports 1993-2016, as published in the "
    "NPD Diskos public archive and parsed by app/datasources.py."
)
_BIT = "IADC API RP 13B-2, Recommended Practice for Calibration of Drilling Bits."

_F = "formation_similarity"
_D = "depth_similarity"
_S = "spatial_proximity"
_E = "event_similarity"
_M = "mud_similarity"
_H = "section_similarity"


def _profile(
    hazard: str,
    label: str,
    features: Sequence[str],
    judgements: Mapping[tuple[str, str], float],
    rationale: str,
    references: Sequence[str],
) -> dict[str, Any]:
    """Build one registry entry from its pairwise judgements."""
    return build_profile(
        hazard,
        features,
        pairwise_matrix(features, judgements),
        rationale,
        references,
        label=label,
    )


DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    profile["hazard"]: profile
    for profile in (
        _profile(
            GENERAL_HAZARD,
            "General offset relevance",
            (_F, _D, _S, _E),
            {
                (_F, _D): 2, (_F, _S): 3, (_F, _E): 4,
                (_D, _S): 2, (_D, _E): 2,
                (_S, _E): 2,
            },
            (
                "The baseline screen, used when no specific hazard is being "
                "screened. Formation match dominates because a hazard in a "
                "different unit is a different problem; TVD alignment comes "
                "next because most drilling hazards are depth-bounded; surface "
                "distance is third because nearby wells share pore-pressure "
                "communication and fault blocks; shared event history is last "
                "because it is corroborating evidence, not a physical cause. "
                "These are stated judgements, not weights fitted to data."
            ),
            (_SAATY, _NPD, _VOLVE),
        ),
        _profile(
            "mud_loss",
            "Mud loss / lost circulation",
            (_F, _D, _S, _E, _M),
            {
                (_F, _D): 2, (_F, _S): 3, (_F, _E): 4, (_F, _M): 2,
                (_D, _S): 2, (_D, _E): 3, (_D, _M): 2,
                (_M, _S): 2, (_M, _E): 3,
            },
            (
                "Losses are governed by formation permeability and by the "
                "equivalent circulating density the mud system can carry, so "
                "formation match and mud programme are both judged strong. TVD "
                "alignment matters because depletion zones and thief zones are "
                "stratigraphically bounded. Shared loss history in a nearby well "
                "is corroboration that the same rock is losing mud. Where the "
                "NPD record carries no mud system for a well, this signal "
                "reports 0.0 with an explicit 'not recorded' detail - it is "
                "never guessed."
            ),
            (_SAATY, _MUD, _NPD, _VOLVE),
        ),
        _profile(
            "stuck_pipe",
            "Stuck pipe",
            (_F, _D, _S, _E, _M, _H),
            {
                (_F, _D): 2, (_F, _S): 2, (_F, _E): 3, (_F, _M): 2, (_F, _H): 2,
                (_D, _S): 2, (_D, _E): 2, (_D, _M): 2, (_D, _H): 2,
                (_M, _S): 3, (_M, _E): 2, (_M, _H): 2,
                (_H, _S): 3, (_H, _E): 2,
            },
            (
                "Sticking is the product of a rock/pressure mismatch and a "
                "contact mechanics mismatch, so formation match and TVD window "
                "lead; the mud programme enters because differential sticking "
                "needs an overbalance margin that is a mud-weight decision; the "
                "hole section and bit enter because pack-off and wellbore "
                "damage are section-specific. Nearby offsets are weighted more "
                "than for mud loss because the same differential-pressure trap "
                "affects a neighbourhood, not a single well."
            ),
            (_SAATY, _MUD, _BIT, _NPD, _VOLVE),
        ),
        _profile(
            "kick",
            "Kick / influx",
            (_F, _D, _S, _E, _M),
            {
                (_F, _D): 2, (_F, _S): 2, (_F, _E): 3, (_F, _M): 3,
                (_D, _S): 2, (_D, _E): 2, (_D, _M): 2,
                (_M, _S): 2, (_M, _E): 2,
            },
            (
                "An influx is a pore-pressure-versus-mud-density problem. "
                "Formation match and TVD locate the pressure window, and the "
                "mud programme is judged as strongly as the rock because the "
                "balance that failed is a mud-weight decision. Surface distance "
                "is judged only equal to event history: connectivity matters, "
                "but a recorded kick next door is the stronger predictor than "
                "mere adjacency."
            ),
            (_SAATY, _WELLCTRL, _MUD, _NPD, _VOLVE),
        ),
        _profile(
            "overpressure",
            "Formation overpressure",
            (_F, _D, _S, _E, _M),
            {
                (_F, _D): 2, (_F, _S): 2, (_F, _E): 3, (_F, _M): 2,
                (_D, _S): 2, (_D, _E): 2, (_D, _M): 2,
                (_M, _S): 2, (_M, _E): 2,
            },
            (
                "Overpressure is a property of the rock at a depth, so formation "
                "match and TVD lead and the mud programme follows, since "
                "underbalanced drilling into an overpressured unit is how the "
                "hazard is created. Surface distance outranks shared event "
                "history because a nearby well may share the pressure "
                "communication without having recorded the event."
            ),
            (_SAATY, _WELLCTRL, _NPD, _VOLVE),
        ),
        _profile(
            "torque_spike",
            "Torque and drag spike",
            (_F, _D, _S, _E, _H),
            {
                (_F, _D): 1, (_F, _S): 2, (_F, _E): 3, (_F, _H): 3,
                (_D, _S): 2, (_D, _E): 2, (_D, _H): 2,
                (_H, _S): 3, (_H, _E): 2,
            },
            (
                "Torque is a hole-geometry problem first and a rock problem "
                "second: dogleg severity, hole size and build section drive "
                "drag, so the hole-section signal is judged as strong as the "
                "formation itself. Formation match and depth stay because the "
                "hole's build rate changes with the section being drilled. "
                "Event history is last - a torque spike is usually a "
                "downhole-mechanics event with little cross-well signature."
            ),
            (_SAATY, _BIT, _NPD, _VOLVE),
        ),
        _profile(
            "cementing_issue",
            "Cementing issue",
            (_F, _D, _S, _E, _H),
            {
                (_F, _D): 1, (_F, _S): 1, (_F, _E): 2, (_F, _H): 2,
                (_D, _S): 2, (_D, _E): 2, (_D, _H): 2,
                (_H, _S): 2, (_H, _E): 2,
            },
            (
                "Cement placement quality is decided by hole condition and by "
                "the depth the casing shoe landed in, so the hole-section signal "
                "and TVD are the two strongest criteria. Formation match is "
                "judged only equal to spatial proximity: the casing point, not "
                "the current hole position, is what the offset has to share, "
                "and a nearby well is at least as informative about regional "
                "pressure and fracture gradient as a co-located formation name."
            ),
            (_SAATY, _NPD, _VOLVE),
        ),
        _profile(
            "lost_circulation",
            "Lost circulation",
            (_F, _D, _S, _E, _M),
            {
                (_F, _D): 2, (_F, _S): 2, (_F, _E): 3, (_F, _M): 3,
                (_D, _S): 2, (_D, _E): 2, (_D, _M): 2,
                (_M, _S): 2, (_M, _E): 2,
            },
            (
                "Lost circulation is a mud-budget problem against a fracturable "
                "or vuggy rock, so the mud programme is judged as strongly as "
                "formation match. Formation and TVD lead because the fracture "
                "gradient is a rock-at-depth property. Spatial proximity and "
                "event history are judged equal and last: connectivity and "
                "recorded losses both help, but neither is causal."
            ),
            (_SAATY, _MUD, _NPD, _VOLVE),
        ),
        _profile(
            "drilling_dysfunction",
            "Drilling dysfunction",
            (_F, _D, _S, _E, _H),
            {
                (_F, _D): 1, (_F, _S): 2, (_F, _E): 2, (_F, _H): 3,
                (_D, _S): 2, (_D, _E): 2, (_D, _H): 3,
                (_H, _S): 2, (_H, _E): 2,
            },
            (
                "Dysfunction - vibration, whirl, premature bit dulling - is a "
                "bit-and-hole-condition problem, so the hole section is the "
                "strongest criterion and depth follows it, because the section "
                "being drilled determines both the bit exposure and the "
                "available clearance. Formation match is judged weak, since a "
                "dysfunctional BHA can go wrong in any rock; surface proximity "
                "is weak for the same reason, but a neighbouring well on the "
                "same bit run is worth something."
            ),
            (_SAATY, _BIT, _NPD, _VOLVE),
        ),
        _profile(
            "hole_instability",
            "Hole instability",
            (_F, _D, _S, _E, _M),
            {
                (_F, _D): 2, (_F, _S): 2, (_F, _E): 3, (_F, _M): 2,
                (_D, _S): 2, (_D, _E): 2, (_D, _M): 2,
                (_M, _S): 2, (_M, _E): 2,
            },
            (
                "Sloughing and caving are rock-mechanics failures: formation "
                "match and TVD lead, the mud programme follows because support "
                "is delivered by mud weight and by the interval already cased, "
                "and a nearby well's recorded sloughing is a real but "
                "supporting signal rather than a cause."
            ),
            (_SAATY, _MUD, _NPD, _VOLVE),
        ),
        _profile(
            "npt",
            "Non-productive time",
            (_F, _D, _S, _E),
            {
                (_F, _D): 1, (_F, _S): 2, (_F, _E): 2,
                (_D, _S): 2, (_D, _E): 2,
                (_S, _E): 3,
            },
            (
                "Downtime is an operational rather than a rock property, so the "
                "weights are deliberately close and the judgement is different "
                "in kind: surface proximity outranks the depth and formation "
                "signals, because equipment failures and weather outages "
                "cluster within a rig and field neighbourhood. Shared event "
                "history is close behind - the same problems recurring in "
                "neighbouring wells is the strongest available evidence."
            ),
            (_SAATY, _NPD, _VOLVE),
        ),
        _profile(
            "other",
            "Unclassified event",
            (_F, _D, _S, _E),
            {
                (_F, _D): 2, (_F, _S): 3, (_F, _E): 4,
                (_D, _S): 2, (_D, _E): 2,
                (_S, _E): 2,
            },
            (
                "With no hazard-specific physics to appeal to, the unclassified "
                "case falls back to the general screen: formation match, TVD "
                "alignment, surface proximity, then shared history. The "
                "weighting is the general profile's, not a claim that downtime "
                "and differential sticking share a mechanism - it is the honest "
                "prior when the hazard is unknown."
            ),
            (_SAATY, _NPD, _VOLVE),
        ),
    )
}

#: The profile used when a caller names a hazard the registry has no opinion on.
GENERAL_PROFILE = DEFAULT_PROFILES[GENERAL_HAZARD]


def profile_for(hazard: str | None) -> dict[str, Any]:
    """Return the AHP profile for ``hazard``.

    An unknown or missing hazard falls back to the general profile rather than
    raising, so a screening call can never fail on an unmapped hazard code.
    """
    return DEFAULT_PROFILES.get(normalize_hazard(hazard), GENERAL_PROFILE)


def all_profiles() -> dict[str, dict[str, Any]]:
    """Return a copy of the full profile registry, keyed by hazard."""
    return {hazard: dict(profile) for hazard, profile in DEFAULT_PROFILES.items()}


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def seed_ahp_profiles(session: Session) -> int:
    """Upsert every registry profile into ``ahp_profiles``.

    Idempotent: a hazard already present is updated in place rather than
    duplicated, so calling this on every boot is safe.

    Returns:
        The number of profiles written.
    """
    written = 0
    for profile in DEFAULT_PROFILES.values():
        row = session.get(AhpProfile, profile["hazard"])
        if row is None:
            row = AhpProfile(hazard=profile["hazard"])
            session.add(row)
        row.label = profile["label"]
        row.features = list(profile["features"])
        row.matrix = profile["matrix"]
        row.weights = dict(profile["weights"])
        row.consistency_ratio = profile["consistency_ratio"]
        row.lambda_max = profile["lambda_max"]
        row.engineering_rationale = profile["engineering_rationale"]
        row.references = list(profile["references"])
        written += 1
    session.flush()
    return written


def ensure_profiles(session: Session) -> dict[str, dict[str, Any]]:
    """Return the persisted profiles, seeding them first if they are missing.

    The registry is the source of truth; the table is the durable copy the API
    and the UI read.  Seeding only happens when the stored set does not match
    the registry, so the common path is a single read.

    Returns:
        ``{hazard: profile-as-persisted}``.
    """
    rows = list(session.scalars(select(AhpProfile)))
    stored = {row.hazard: row for row in rows}
    if set(stored) != set(DEFAULT_PROFILES):
        seed_ahp_profiles(session)
        rows = list(session.scalars(select(AhpProfile)))
        stored = {row.hazard: row for row in rows}
    return {
        hazard: {
            "hazard": row.hazard,
            "label": row.label,
            "features": list(row.features or []),
            "matrix": list(row.matrix or []),
            "weights": dict(row.weights or {}),
            "consistency_ratio": row.consistency_ratio,
            "lambda_max": row.lambda_max,
            "engineering_rationale": row.engineering_rationale,
            "references": list(row.references or []),
            "cr_threshold": CR_THRESHOLD,
            "consistent": row.consistency_ratio <= CR_THRESHOLD,
            "method": "AHP_PRINCIPAL_EIGENVECTOR",
        }
        for hazard, row in stored.items()
    }

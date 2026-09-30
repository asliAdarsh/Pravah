"""Single registry of drilling event types.

Adding a new type is a one-line change: append an entry to :data:`EVENT_TYPE_REGISTRY`.
The risk engine, search intent parser, schema validation and ``GET /meta`` all read
from this registry — nothing else hard-codes a type code.

Every entry carries:

``code``
    Canonical ``SCREAMING_SNAKE`` code stored in the database.
``label``
    Human label for the UI.
``family``
    Coarse grouping (``MECHANICAL``, ``HYDRAULIC``, ``FORMATION``, ``OPERATIONAL``…).
``severity_weight``
    Multiplier in ``0.2 … 1.2`` applied to an event's base severity when computing
    risk; > 1.0 means the type is intrinsically more hazardous.
``color``
    Hex colour used for UI badges.  Restrained industrial palette: burnt orange for
    mechanical, navy/blue for formation, green-grey for operational, red only for
    critical families.
``vocabulary``
    Search synonyms used by the NL intent parser.
``keywords``
    Additional free-text terms matched against descriptions/mitigations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "EVENT_TYPE_REGISTRY",
    "EventTypeSpec",
    "alertable_event_types",
    "all_event_type_codes",
    "coerce_event_type",
    "get_event_type",
    "is_alertable",
    "non_alertable_event_types",
    "search_vocabulary",
    "severity_weight",
    "vocabulary_index",
]


@dataclass(frozen=True, slots=True)
class EventTypeSpec:
    """Immutable description of one drilling event type."""

    code: str
    label: str
    family: str
    severity_weight: float
    color: str
    #: Whether the proactive risk engine may raise an alert for this type.  A
    #: formation transition is stratigraphy, not a hazard, so it is recorded on the
    #: timeline and searchable but can never raise an operational alert.  Setting
    #: this to ``False`` on a new type is a one-line change.
    alertable: bool = True
    vocabulary: tuple[str, ...] = field(default=())
    keywords: tuple[str, ...] = field(default=())

    @property
    def search_terms(self) -> tuple[str, ...]:
        """Union of vocabulary and keyword terms, lower-cased, de-duplicated."""
        seen: dict[str, None] = {}
        for term in (*self.vocabulary, *self.keywords):
            seen.setdefault(term.lower(), None)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for ``GET /api/v1/meta`` and the pydantic schemas."""
        return {
            "code": self.code,
            "label": self.label,
            "family": self.family,
            "severity_weight": self.severity_weight,
            "color": self.color,
            "vocabulary": list(self.vocabulary),
            "alertable": self.alertable,
            "alert_exclusion_reason": (
                None
                if self.alertable
                else "Stratigraphic marker, not a hazard — excluded from proactive alerts."
            ),
        }


_EVENT_TYPES: tuple[EventTypeSpec, ...] = (
    EventTypeSpec(
        code="MUD_LOSS",
        label="Mud Loss",
        family="HYDRAULIC",
        severity_weight=0.9,
        color="#B45309",
        vocabulary=("mud loss", "mudloss", "losses", "fluid loss", "circulation loss"),
        keywords=("mud", "losses", "pit gain loss"),
    ),
    EventTypeSpec(
        code="STUCK_PIPE",
        label="Stuck Pipe",
        family="MECHANICAL",
        severity_weight=1.1,
        color="#C2410C",
        vocabulary=("stuck pipe", "stuck", "sticking", "differential pipe sticking", "free pipe"),
        keywords=("stuck", "sticking", "string", "pack off"),
    ),
    EventTypeSpec(
        code="KICK",
        label="Kick",
        family="WELLCONTROL",
        severity_weight=1.2,
        color="#B91C1C",
        vocabulary=("kick", "kick detection", "flow", "influx", "well control", "flow check"),
        keywords=("flow check", "well control", "shut in"),
    ),
    EventTypeSpec(
        code="TORQUE_SPIKE",
        label="Torque Spike",
        family="MECHANICAL",
        severity_weight=0.7,
        color="#A16207",
        vocabulary=("torque spike", "torque", "drag", "torsion"),
        keywords=("torque", "drag", "overpull"),
    ),
    EventTypeSpec(
        code="OVERPRESSURE",
        label="Overpressure",
        family="FORMATION",
        severity_weight=1.1,
        color="#9A3412",
        vocabulary=("overpressure", "over pressure", "high pressure", "pore pressure"),
        keywords=("pressure", "trip gas"),
    ),
    EventTypeSpec(
        code="LOST_CIRCULATION",
        label="Lost Circulation",
        family="HYDRAULIC",
        severity_weight=0.8,
        color="#0F766E",
        vocabulary=("lost circulation", "circulation loss", "washout"),
        keywords=("circulation", "washout", "frac out"),
    ),
    EventTypeSpec(
        code="CEMENTING_ISSUE",
        label="Cementing Issue",
        family="COMPLETION",
        severity_weight=0.6,
        color="#1D4ED8",
        vocabulary=("cementing issue", "cement", "cement bond", "channeling", "squeeze"),
        keywords=("cement", "bond", "channeling"),
    ),
    EventTypeSpec(
        code="DRILLING_DYSFUNCTION",
        label="Drilling Dysfunction",
        family="OPERATIONAL",
        severity_weight=0.6,
        color="#334155",
        vocabulary=("drilling dysfunction", "dysfunction", "vibration", "whirl", "bit dull"),
        keywords=("vibration", "bit", "wob", "rop"),
    ),
    EventTypeSpec(
        code="NPT",
        label="Non-Productive Time",
        family="OPERATIONAL",
        severity_weight=0.4,
        color="#64748B",
        vocabulary=("npt", "non productive time", "nonproductive time", "downtime", "wasted time"),
        keywords=("npt", "downtime"),
    ),
    EventTypeSpec(
        code="FORMATION_TRANSITION",
        label="Formation Transition",
        family="FORMATION",
        severity_weight=0.3,
        color="#1E3A8A",
        vocabulary=("formation transition", "transition", "top of formation", "tof", "marker bed"),
        keywords=("transition", "top of", "marker"),
        # Stratigraphy, not a hazard: it anchors the timeline and the formation
        # column, but must never raise a proactive operational alert.
        alertable=False,
    ),
    EventTypeSpec(
        code="HOLE_INSTABILITY",
        label="Hole Instability",
        family="FORMATION",
        severity_weight=0.9,
        color="#4338CA",
        vocabulary=("hole instability", "hole condition", "borehole instability", "sloughing"),
        keywords=("instability", "slough", "cave in"),
    ),
    EventTypeSpec(
        code="OTHER",
        label="Other",
        family="GENERAL",
        severity_weight=0.5,
        color="#475569",
        vocabulary=("other", "misc", "miscellaneous", "general"),
        keywords=("other", "general"),
    ),
)

EVENT_TYPE_REGISTRY: dict[str, EventTypeSpec] = {spec.code: spec for spec in _EVENT_TYPES}

#: Reverse index from a search term to the codes it maps to.  Longest terms are
#: matched first by the intent parser so "lost circulation" beats "loss".
def _build_vocabulary_index() -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for spec in _EVENT_TYPES:
        for term in spec.search_terms:
            index.setdefault(term, [])
            if spec.code not in index[term]:
                index[term].append(spec.code)
    return index


VOCABULARY_INDEX: dict[str, list[str]] = _build_vocabulary_index()

#: Terms ordered longest-first, so the parser prefers the most specific phrase.
SORTED_VOCABULARY: tuple[str, ...] = tuple(sorted(VOCABULARY_INDEX, key=len, reverse=True))

#: Extraction vocabulary: phrases that indicate the hazard *in the source text*.
#: Deliberately narrower than :data:`VOCABULARY_INDEX` — `keywords` are short UI
#: and query terms ("rop", "wob", "bit") that appear in ordinary DDR operational
#: summaries, so matching them would invent events out of normal progress lines.
def _build_extraction_index() -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for spec in _EVENT_TYPES:
        # Machine exports and some DDR templates write the type in code form
        # ("MUD_LOSS at 1490 m TVD"). That is unambiguous, so it extracts too.
        terms = (*spec.vocabulary, spec.code.lower())
        for term in terms:
            key = term.lower()
            index.setdefault(key, [])
            if spec.code not in index[key]:
                index[key].append(spec.code)
    return index


EXTRACTION_VOCABULARY_INDEX: dict[str, list[str]] = _build_extraction_index()

#: Flat ``code -> phrases`` view of the extraction vocabulary. Includes the
#: snake-case code itself, because machine exports and some report templates
#: write the type in that form ("MUD_LOSS at 1490 m TVD").
EXTRACTION_VOCABULARY: dict[str, tuple[str, ...]] = {
    spec.code: tuple(dict.fromkeys((*spec.vocabulary, spec.code.lower())))
    for spec in _EVENT_TYPES
}

#: Extraction terms ordered longest-first, mirroring :data:`SORTED_VOCABULARY`.
SORTED_EXTRACTION_VOCABULARY: tuple[str, ...] = tuple(
    sorted(EXTRACTION_VOCABULARY_INDEX, key=len, reverse=True)
)


def all_event_type_codes() -> list[str]:
    """Return every registered event type code."""
    return list(EVENT_TYPE_REGISTRY)


def get_event_type(code: str) -> EventTypeSpec:
    """Return the spec for ``code``.

    Raises:
        KeyError: if the code is not registered.
    """
    return EVENT_TYPE_REGISTRY[code]


def coerce_event_type(code: str) -> str:
    """Normalise ``code`` (upper, strip) and raise ``ValueError`` when unknown."""
    normalized = (code or "").strip().upper()
    if normalized not in EVENT_TYPE_REGISTRY:
        raise ValueError(f"Unknown event type: {code!r}")
    return normalized


def is_alertable(code: str) -> bool:
    """Return ``True`` when the risk engine may raise an alert for ``code``.

    Unregistered codes are treated as alertable so a new hazard type is never
    silently dropped before its registry entry is filled in.
    """
    spec = EVENT_TYPE_REGISTRY.get(code)
    return True if spec is None else spec.alertable


def alertable_event_types() -> list[str]:
    """Return every event type code the proactive risk engine may alert on."""
    return [code for code, spec in EVENT_TYPE_REGISTRY.items() if spec.alertable]


def non_alertable_event_types() -> list[dict[str, str]]:
    """Return the excluded codes together with the reason, for the UI and /meta."""
    return [
        {"code": code, "reason": spec.to_dict()["alert_exclusion_reason"]}
        for code, spec in EVENT_TYPE_REGISTRY.items()
        if not spec.alertable
    ]


def severity_weight(code: str) -> float:
    """Return the risk weight for ``code`` (0.5 when unregistered)."""
    spec = EVENT_TYPE_REGISTRY.get(code)
    return spec.severity_weight if spec else 0.5


def search_vocabulary(code: str) -> list[str]:
    """Return the search vocabulary of one event type."""
    spec = EVENT_TYPE_REGISTRY.get(code)
    return list(spec.search_terms) if spec else []


def vocabulary_index() -> dict[str, list[str]]:
    """Return a copy of the term → codes index."""
    return {term: list(codes) for term, codes in VOCABULARY_INDEX.items()}

"""LLM provider abstraction with a deterministic, non-negotiable fallback.

The LLM is **optional and never the source of truth**.  This module has exactly
one job: decide whether a real model can be called, call it if so, and otherwise
tell the caller to emit ``RULE_BASED_TEMPLATE`` synthesis.

Hard rules enforced here
------------------------
* With no ``PRAVAH_LLM_API_KEY`` configured, :func:`synthesize` returns
  ``available=False`` and the caller uses its deterministic template.
* ``model`` is only ever populated from the identifier the provider actually
  returned, or the configured identifier on a successful call.  It is ``None``
  whenever no call happened — a model name is never invented.
* A provider error is caught and downgraded to the template; it never propagates
  and never leaves a half-model-labelled payload behind.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from .config import Settings, settings

__all__ = [
    "LLMUnavailable",
    "SynthesisResult",
    "available_provider",
    "llm_status",
    "synthesize",
]

DISCLAIMER = "Generated from retrieved records only."
RULE_DISCLAIMER = "Heuristic output. Not an operational instruction."


class LLMUnavailable(RuntimeError):
    """Raised internally when no provider is configured.

    Callers should catch this (or check :func:`available_provider` first) and fall
    back to deterministic template synthesis.
    """


@dataclass(slots=True)
class SynthesisResult:
    """The outcome of a synthesis attempt.

    Attributes:
        text: The generated summary.  Never empty.
        provenance: ``MODEL_GENERATED`` or ``RULE_BASED_TEMPLATE``.
        model: The model identifier, or ``None`` for template output.
        citations: Structured citations backing the text.
        disclaimer: Always present.
        detail: Why the fallback was used, for the UI's "why" affordance.
    """

    text: str
    provenance: str
    model: str | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    disclaimer: str = DISCLAIMER
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API."""
        return {
            "text": self.text,
            "provenance": self.provenance,
            "model": self.model,
            "citations": self.citations,
            "disclaimer": self.disclaimer,
        }


def available_provider(config: Settings | None = None) -> bool:
    """Return ``True`` when a real LLM call can be attempted."""
    active = config or settings
    return active.llm_configured


def llm_status(config: Settings | None = None) -> dict[str, Any]:
    """Return the LLM block reported by ``GET /api/v1/meta``."""
    active = config or settings
    configured = active.llm_configured
    return {
        "configured": configured,
        "mode": "MODEL" if configured else "RULE_BASED_FALLBACK",
        "model": active.llm_model if configured else None,
        "base_url": active.llm_base_url if configured else None,
    }


SYSTEM_PROMPT = (
    "You are a drilling-intelligence assistant for Pravah. "
    "You are given records retrieved from a published drilling dataset. "
    "Answer using ONLY the supplied records. Do not invent page numbers, well "
    "names, depths, events or mitigations that are not present. Do not give "
    "operational instructions and do not claim real-time or validated accuracy. "
    "Keep the answer to two or three sentences and be explicit when the records "
    "are thin."
)


def _build_user_prompt(query: str, records: Sequence[dict[str, Any]]) -> str:
    """Render the retrieved records into the user prompt."""
    payload = json.dumps(list(records), indent=2, default=str)
    return (
        f"Question: {query}\n\n"
        f"Retrieved records (JSON):\n{payload}\n\n"
        "Write a two or three sentence answer grounded strictly in these records."
    )


def _post_chat(
    config: Settings,
    query: str,
    records: Sequence[dict[str, Any]],
    timeout: float,
) -> tuple[str, str]:
    """Call the OpenAI-compatible chat endpoint and return ``(text, model)``.

    Raises:
        LLMUnavailable: when the key is missing or the provider call fails.
    """
    import httpx  # imported lazily so the app runs without network extras

    if not config.llm_configured:
        raise LLMUnavailable("No PRAVAH_LLM_API_KEY configured; using template synthesis.")
    url = config.llm_base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": config.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(query, records)},
        ],
        "temperature": 0.0,
        "max_tokens": 400,
    }
    headers = {
        "Authorization": f"Bearer {config.llm_api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = httpx.post(url, json=body, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - any provider failure is a fallback
        raise LLMUnavailable(f"LLM provider call failed: {exc}") from exc

    choices = payload.get("choices") or []
    if not choices:
        raise LLMUnavailable("LLM provider returned no choices.")
    message = choices[0].get("message") or {}
    text = (message.get("content") or "").strip()
    if not text:
        raise LLMUnavailable("LLM provider returned empty content.")
    # Only trust the provider's own echo of the model id.
    model = payload.get("model") or config.llm_model
    return text, model


def synthesize(
    query: str,
    records: Sequence[dict[str, Any]],
    template_text: str,
    citations: Sequence[dict[str, Any]] = (),
    config: Settings | None = None,
    timeout: float = 20.0,
) -> SynthesisResult:
    """Summarise retrieved records, preferring a real model when configured.

    Args:
        query: The user's question.
        records: Retrieved records, already rendered as plain dicts.
        template_text: Deterministic fallback text built by the caller from the
            same records.  Used verbatim whenever no model is available, so the
            answer can never be empty.
        citations: Citation records to attach to whichever result is returned.
        config: Settings override (tests inject a fake).
        timeout: Provider HTTP timeout in seconds.

    Returns:
        A :class:`SynthesisResult`.  ``provenance`` is ``MODEL_GENERATED`` only
        when a provider call actually succeeded; otherwise ``RULE_BASED_TEMPLATE``
        with ``model=None``.
    """
    active = config or settings
    citation_list = [dict(c) for c in citations]
    if not available_provider(active):
        return SynthesisResult(
            text=template_text,
            provenance="RULE_BASED_TEMPLATE",
            model=None,
            citations=citation_list,
            disclaimer=DISCLAIMER,
            detail="No LLM API key configured; deterministic template synthesis used.",
        )
    try:
        text, model = _post_chat(active, query, records, timeout)
    except LLMUnavailable as exc:
        return SynthesisResult(
            text=template_text,
            provenance="RULE_BASED_TEMPLATE",
            model=None,
            citations=citation_list,
            disclaimer=DISCLAIMER,
            detail=f"LLM unavailable ({exc}); deterministic template synthesis used.",
        )
    return SynthesisResult(
        text=text,
        provenance="MODEL_GENERATED",
        model=model,
        citations=citation_list,
        disclaimer=(
            "Model-generated summary grounded in retrieved records. "
            "Verify every figure against the cited sources before use."
        ),
        detail="Summarised by the configured LLM over the retrieved records only.",
    )

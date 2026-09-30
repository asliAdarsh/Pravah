"""Domain errors mapped to HTTP responses by the app's exception handlers.

Routers raise these instead of building ``HTTPException`` inline, so the error
contract (``{"detail": ...}``) lives in exactly one place.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "DomainError",
    "EngineerRequiredError",
    "IngestError",
    "NotFoundError",
    "ValidationFailedError",
]


class DomainError(Exception):
    """Base class for expected, user-facing failures."""

    status_code = 400

    def __init__(self, message: str, detail: Any | None = None) -> None:
        """Store the message and an optional structured detail payload."""
        super().__init__(message)
        self.message = message
        self.detail = detail

    def payload(self) -> Any:
        """Return the ``detail`` value sent to the client."""
        if self.detail is not None:
            return {"message": self.message, **({"detail": self.detail} if isinstance(self.detail, dict) else {})}
        return self.message


class NotFoundError(DomainError):
    """A referenced entity does not exist → 404."""

    status_code = 404

    def __init__(self, message: str) -> None:
        """Store the 404 message."""
        super().__init__(message)


class ValidationFailedError(DomainError):
    """The request was syntactically valid but semantically rejected → 422."""

    status_code = 422

    def __init__(self, message: str, detail: Any | None = None) -> None:
        """Store the 422 message and structured reason."""
        super().__init__(message, detail)


class IngestError(DomainError):
    """The ingest payload could not be read at all → 400."""

    status_code = 400

    def __init__(self, message: str) -> None:
        """Store the 400 message."""
        super().__init__(message)


class EngineerRequiredError(DomainError):
    """An engineer action was attempted without naming an engineer → 422."""

    status_code = 422

"""Environment-driven settings for Pravah.

No secrets are stored in code.  Every value can be overridden with an ``PRAVAH_``
prefixed environment variable (or a ``.env`` file placed next to ``run.py``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "pravah.db"

__all__ = [
    "BACKEND_DIR",
    "DEFAULT_DB_PATH",
    "Settings",
    "get_settings",
    "settings",
]

APP_NAME = "Pravah"
APP_VERSION = "1.0.0"
ENGINE_VERSION = "rule-engine-0.1.0"
DATASET_LABEL = "Real public data"
DATA_PROVENANCE = "REAL_PUBLIC_DATA"
DEFAULT_SEED = 20240517


class Settings(BaseSettings):
    """Runtime configuration.

    Attributes:
        database_url: SQLAlchemy URL.  Defaults to a SQLite file under
            ``backend/data/pravah.db``.
        reset_db: When true the schema is dropped and the real dataset reloaded
            on the next startup.
        llm_api_key: Optional OpenAI-compatible API key.  Absent means the backend
            always emits deterministic ``RULE_BASED_TEMPLATE`` synthesis.
        llm_base_url: OpenAI-compatible ``/chat/completions`` base URL.
        llm_model: Model identifier.  Only ever echoed back when a real call
            succeeded — never invented.
        cors_origins: Comma separated list of allowed browser origins.
        max_ddr_reports: How many real Volve DDR reports to load and index. The
            published corpus holds ~30k; the default keeps startup under ten
            seconds while still indexing well over a thousand real events.
    """

    model_config = SettingsConfigDict(
        env_prefix="PRAVAH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"
    reset_db: bool = False
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_ddr_reports: int = 4000

    @property
    def cors_origin_list(self) -> list[str]:
        """Return the CORS allow-list as a list of origins."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def llm_configured(self) -> bool:
        """True only when an API key is present, i.e. a real model can be called."""
        return bool(self.llm_api_key and self.llm_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


settings = get_settings()

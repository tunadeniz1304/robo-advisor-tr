"""Core configuration for the Otonom Finansal Danışman (Robo-Advisor).

The configuration layer is the single source of truth for all runtime settings.
It loads real LLM API keys (OpenAI or Anthropic) from a local ``.env`` file via
python-dotenv. No hard-coded secrets are allowed anywhere else in the codebase.

Design notes:
    * Secrets are never logged and never serialized into responses.
    * The ``Settings`` object is immutable (frozen dataclass) so that a running
      process cannot mutate its own configuration by accident.
    * ``Settings.load()`` is a lazy factory: it is called once at application
      startup and the resulting instance is shared through the dependency
      injection container.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Root of the repository (one level above the ``core`` package).
BASE_DIR: Path = Path(__file__).resolve().parent.parent


def _env_path() -> Path:
    """Return the resolved path of the project ``.env`` file."""
    return BASE_DIR / ".env"


def load_dotenv_file(verbose: bool = True) -> None:
    """Load environment variables from ``.env`` into ``os.environ``.

    python-dotenv only sets variables that are not already present in the
    process environment, so an injected key (e.g. from a CI pipeline or Docker
    Compose) always wins over the file.
    """
    path = _env_path()
    if path.is_file():
        load_dotenv(dotenv_path=path, override=False, verbose=verbose)


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration snapshot.

    Attributes:
        app_name: Human readable application name.
        version: Application version string, reported on the health endpoint.
        api_v1_prefix: URL prefix under which all versioned routes are mounted.
        database_url: SQLAlchemy async database URL (SQLite or PostgreSQL).
        openai_api_key: Real OpenAI API key, or ``None`` when not configured.
        anthropic_api_key: Real Anthropic API key, or ``None`` when not configured.
        log_level: Root structlog logging level (DEBUG/INFO/WARNING/ERROR).
        log_json: When ``True`` emit JSON logs (prod), otherwise pretty console.
        request_timeout_seconds: Timeout used by outbound HTTP calls.
    """

    app_name: str = "Otonom Finansal Danışman"
    version: str = "1.0.0"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite+aiosqlite:///./advisor.db"
    openai_api_key: Optional[str] = field(default=None)
    anthropic_api_key: Optional[str] = field(default=None)
    log_level: str = "INFO"
    log_json: bool = False
    request_timeout_seconds: float = 30.0

    # -- Derived properties -------------------------------------------------

    @property
    def has_llm_credentials(self) -> bool:
        """Whether at least one real LLM provider credential is present."""
        return bool(self.openai_api_key or self.anthropic_api_key)

    @property
    def llm_provider_name(self) -> str:
        """Name of the active LLM provider ('openai', 'anthropic' or 'none')."""
        if self.openai_api_key:
            return "openai"
        if self.anthropic_api_key:
            return "anthropic"
        return "none"

    # -- Factory ------------------------------------------------------------

    @classmethod
    def load(cls) -> "Settings":
        """Load configuration from environment variables.

        The :func:`load_dotenv_file` is invoked first so that a local
        ``.env`` file is honoured in development.
        """
        load_dotenv_file()
        return cls(
            app_name=os.getenv("APP_NAME", "Otonom Finansal Danışman"),
            version=os.getenv("APP_VERSION", "1.0.0"),
            api_v1_prefix=os.getenv("API_V1_PREFIX", "/api/v1"),
            database_url=os.getenv(
                "DATABASE_URL", "sqlite+aiosqlite:///./advisor.db"
            ),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_json=os.getenv("LOG_JSON", "false").lower() in {"1", "true", "yes"},
            request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30.0")),
        )

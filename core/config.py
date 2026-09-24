"""Core configuration for the Otonom Finansal Danışman (Robo-Advisor).

The configuration layer is the single source of truth for all runtime settings.
Environment variables are loaded from ``.env`` via python-dotenv; nothing else
in the code base reads LLM related variables with ``os.getenv``.

``.env`` lookup order:
    1. ``<proje kökü>/.env``
    2. ``<proje kökü>/../.env`` (INGHACK kökü)

Real environment variables always win (``override=False``).

Design notes:
    * Secrets are never logged and never serialized into responses; use
      :func:`mask_secret` whenever a key must be referenced in a message.
    * ``Settings`` is immutable (frozen dataclass).
    * LLM variables accept several aliases (first non-empty wins), so a key
      named ``LLM_API_KEY``, ``DEEPSEEK_API_KEY``, ``EVREN_API_KEY`` or
      ``OPENAI_API_KEY`` works without extra configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

# Root of the repository (one level above the ``core`` package).
BASE_DIR: Path = Path(__file__).resolve().parent.parent

DEFAULT_LLM_BASE_URL = "https://evren-llmapi.ssyz.org.tr/v1"
DEFAULT_LLM_MODEL = "deepseek-v4-flash"

# Alias zincirleri: ilk dolu değer kazanır.
LLM_KEY_ALIASES: tuple[str, ...] = (
    "LLM_API_KEY",
    "DEEPSEEK_API_KEY",
    "EVREN_API_KEY",
    "OPENAI_API_KEY",
)
LLM_BASE_URL_ALIASES: tuple[str, ...] = (
    "LLM_BASE_URL",
    "DEEPSEEK_BASE_URL",
    "EVREN_BASE_URL",
    "OPENAI_BASE_URL",
)
LLM_MODEL_ALIASES: tuple[str, ...] = ("LLM_MODEL", "DEEPSEEK_MODEL")

LLM_MODES = ("auto", "live", "demo")
ENVIRONMENTS = ("dev", "test", "prod")
SHARED_BACKENDS = ("memory", "redis")
LLM_PROVIDERS = ("openai_compatible", "anthropic")


def dotenv_candidates() -> list[Path]:
    """Return the ``.env`` lookup order (project root first, then parent)."""
    return [BASE_DIR / ".env", BASE_DIR.parent / ".env"]


def load_dotenv_file(verbose: bool = False) -> Path | None:
    """Load environment variables from the first existing ``.env`` file.

    python-dotenv only sets variables that are not already present in the
    process environment, so an injected key (CI, Docker ``env_file``) always
    wins over the file. The file content is never printed.

    Args:
        verbose: Forwarded to python-dotenv (warns when the file is missing).

    Returns:
        The path of the loaded file, or ``None`` when no file exists.
    """
    for path in dotenv_candidates():
        if path.is_file():
            load_dotenv(dotenv_path=path, override=False, verbose=verbose)
            return path
    return None


def mask_secret(value: str | None) -> str:
    """Mask a secret for display (``sk-…abcd``); never returns the full value."""
    if not value:
        return ""
    tail = value[-4:] if len(value) > 8 else ""
    head = value[:3] if len(value) > 12 else ""
    return f"{head}…{tail}"


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _env_str(name: str, default: str) -> str:
    """String env var where an empty value means "use the default"."""
    raw = os.getenv(name)
    return raw.strip() if raw is not None and raw.strip() else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "evet"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _env_tuple(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


class ConfigurationError(RuntimeError):
    """Raised at startup when the configuration is inconsistent."""


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration snapshot.

    Attributes:
        app_name: Human readable application name.
        version: Application version string.
        environment: ``dev`` | ``test`` | ``prod``.
        api_v1_prefix: URL prefix for versioned routes.
        database_url: SQLAlchemy async database URL (SQLite or PostgreSQL).
        log_level: Root logging level.
        log_json: Emit JSON logs when ``True``.
        llm_provider: ``openai_compatible`` (default) or ``anthropic``.
        llm_api_key: API key of the OpenAI-compatible endpoint (secret).
        llm_base_url: Base URL of the OpenAI-compatible endpoint.
        llm_model: Chat model name.
        llm_mode: ``auto`` | ``live`` | ``demo``.
        llm_timeout_seconds: Per-call timeout (also used for outbound HTTP).
        llm_max_retries: SDK level retry count.
        llm_temperature: Sampling temperature.
        llm_max_tokens: Completion token cap.
        llm_disable_thinking: Ask reasoning models (DeepSeek V4) to skip thinking.
        anthropic_api_key: Optional Anthropic key (``LLM_PROVIDER=anthropic``).
        jwt_secret: HMAC secret for access/refresh tokens.
        jwt_access_ttl_minutes: Access token lifetime.
        jwt_refresh_ttl_minutes: Refresh token lifetime.
        pii_encryption_key: Fernet key for PII columns (dev key when empty).
        cors_origins: Allowed CORS origins.
        rate_limit_enabled: Toggle for rate limiting.
        rate_limit_default: Default per-client limit (``limits`` syntax).
        rate_limit_login: Stricter limit for the login endpoint.
        bootstrap_admin_username: Admin created on first start (if no users).
        bootstrap_admin_password: Its password (required in prod).
        scheduler_enabled: Start APScheduler jobs on startup.
        data_mode: ``auto`` (live → snapshot), ``live`` or ``snapshot``.
        market_cache_ttl_seconds: Shared market cache TTL.
        evds_api_key: TCMB EVDS key (optional, secret).
        checkpoint_db: SQLite file of the durable LangGraph checkpointer
            (ignored when ``database_url`` is PostgreSQL: the checkpointer then
            uses the same database).
        auto_migrate: Run Alembic migrations on startup.
        metrics_public: Serve ``/metrics`` without authentication.
        metrics_token: Bearer token for ``/metrics`` (when not public).
        lock_backend: ``memory`` (single process) or ``redis`` (shared).
        rate_limit_storage: ``memory`` or ``redis``.
        redis_url: Redis URL for the shared backends.
    """

    app_name: str = "Otonom Finansal Danışman"
    version: str = "2.0.0"
    environment: str = "dev"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite+aiosqlite:///./advisor.db"
    log_level: str = "INFO"
    log_json: bool = False

    # --- LLM -------------------------------------------------------------
    llm_provider: str = "openai_compatible"
    llm_api_key: str | None = field(default=None, repr=False)
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_model: str = DEFAULT_LLM_MODEL
    llm_mode: str = "auto"
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1500
    llm_disable_thinking: bool = True
    anthropic_api_key: str | None = field(default=None, repr=False)

    # --- Güvenlik --------------------------------------------------------
    jwt_secret: str = field(default="dev-only-jwt-secret-change-me-32bytes-min", repr=False)
    jwt_access_ttl_minutes: int = 60
    jwt_refresh_ttl_minutes: int = 60 * 24 * 7
    pii_encryption_key: str | None = field(default=None, repr=False)
    cors_origins: tuple[str, ...] = ("http://localhost:8000", "http://127.0.0.1:8000")
    rate_limit_enabled: bool = True
    rate_limit_default: str = "300/minute"
    rate_limit_login: str = "10/minute"
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str | None = field(default=None, repr=False)

    # --- Altyapı ---------------------------------------------------------
    scheduler_enabled: bool = False
    data_mode: str = "auto"
    market_cache_ttl_seconds: int = 300
    evds_api_key: str | None = field(default=None, repr=False)
    checkpoint_db: str | None = None
    auto_migrate: bool = True
    seed_demo: bool = False
    metrics_public: bool = False
    metrics_token: str | None = field(default=None, repr=False)
    lock_backend: str = "memory"
    rate_limit_storage: str = "memory"
    redis_url: str | None = field(default=None, repr=False)

    # -- Derived properties ------------------------------------------------

    @property
    def request_timeout_seconds(self) -> float:
        """Backward compatible alias of :attr:`llm_timeout_seconds`."""
        return self.llm_timeout_seconds

    @property
    def has_llm_credentials(self) -> bool:
        """Whether a key for the configured provider is present."""
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        return bool(self.llm_api_key)

    @property
    def effective_llm_mode(self) -> str:
        """Resolve ``auto`` to ``live`` or ``demo``."""
        if self.llm_mode == "demo":
            return "demo"
        if self.llm_mode == "live":
            return "live"
        return "live" if self.has_llm_credentials else "demo"

    @property
    def llm_base_url_host(self) -> str:
        """Host part of the LLM base URL (safe to display)."""
        return urlparse(self.llm_base_url).hostname or ""

    @property
    def llm_provider_name(self) -> str:
        """Human readable provider/mode label used in startup logs."""
        return self.llm_provider if self.effective_llm_mode == "live" else "demo"

    @property
    def is_production(self) -> bool:
        return self.environment == "prod"

    def validate(self) -> None:
        """Fail fast on inconsistent configuration (Turkish messages).

        Raises:
            ConfigurationError: When ``LLM_MODE=live`` without a key, an
                unknown mode/provider is configured or production secrets
                are missing.
        """
        if self.llm_mode not in LLM_MODES:
            raise ConfigurationError(
                f"Geçersiz LLM_MODE='{self.llm_mode}'. Geçerli değerler: {', '.join(LLM_MODES)}."
            )
        if self.llm_provider not in LLM_PROVIDERS:
            raise ConfigurationError(
                f"Geçersiz LLM_PROVIDER='{self.llm_provider}'. "
                f"Geçerli değerler: {', '.join(LLM_PROVIDERS)}."
            )
        if self.llm_mode == "live" and not self.has_llm_credentials:
            raise ConfigurationError(
                "LLM_MODE=live seçildi ancak API anahtarı bulunamadı. .env içine "
                "LLM_API_KEY (veya DEEPSEEK_API_KEY / EVREN_API_KEY / OPENAI_API_KEY) "
                "ekleyin ya da LLM_MODE=auto/demo kullanın."
            )
        if self.data_mode not in {"auto", "live", "snapshot"}:
            raise ConfigurationError(
                f"Geçersiz DATA_MODE='{self.data_mode}'. Geçerli değerler: auto, live, snapshot."
            )
        if self.environment not in ENVIRONMENTS:
            raise ConfigurationError(
                f"Geçersiz APP_ENV='{self.environment}'. Geçerli değerler: {', '.join(ENVIRONMENTS)}."
            )
        for name, value in (
            ("LOCK_BACKEND", self.lock_backend),
            ("RATE_LIMIT_STORAGE", self.rate_limit_storage),
        ):
            if value not in SHARED_BACKENDS:
                raise ConfigurationError(
                    f"Geçersiz {name}='{value}'. Geçerli değerler: memory, redis."
                )
            if value == "redis" and not self.redis_url:
                raise ConfigurationError(f"{name}=redis için REDIS_URL tanımlanmalıdır.")
        if self.is_production:
            self._validate_production_secrets()

    def _validate_production_secrets(self) -> None:
        """Prod refuses to start with weak or missing secrets."""
        from core.security import secret_is_strong

        if not secret_is_strong(self.jwt_secret):
            raise ConfigurationError(
                "Prod ortamında JWT_SECRET en az 32 karakter, yeterince çeşitli (entropili) ve "
                "varsayılandan farklı olmalıdır."
            )
        if not self.pii_encryption_key:
            raise ConfigurationError(
                "Prod ortamında PII_ENCRYPTION_KEY (Fernet anahtarı) zorunludur; geliştirme "
                "anahtarı yalnızca dev/test ortamında kullanılabilir."
            )
        try:
            from cryptography.fernet import Fernet

            Fernet(self.pii_encryption_key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(
                "PII_ENCRYPTION_KEY geçerli bir Fernet anahtarı değil."
            ) from exc

    # -- Factory -----------------------------------------------------------

    @classmethod
    def load(cls) -> Settings:
        """Load configuration from the environment (after ``.env``)."""
        load_dotenv_file()
        return cls(
            app_name=_env_str("APP_NAME", "Otonom Finansal Danışman"),
            version=_env_str("APP_VERSION", "2.0.0"),
            environment=_env_str("APP_ENV", "dev").lower(),
            api_v1_prefix=_env_str("API_V1_PREFIX", "/api/v1"),
            database_url=_env_str("DATABASE_URL", "sqlite+aiosqlite:///./advisor.db"),
            log_level=_env_str("LOG_LEVEL", "INFO").upper(),
            log_json=_env_bool("LOG_JSON", False),
            llm_provider=_env_str("LLM_PROVIDER", "openai_compatible").lower(),
            llm_api_key=_first_env(LLM_KEY_ALIASES),
            llm_base_url=_first_env(LLM_BASE_URL_ALIASES) or DEFAULT_LLM_BASE_URL,
            llm_model=_first_env(LLM_MODEL_ALIASES) or DEFAULT_LLM_MODEL,
            llm_mode=_env_str("LLM_MODE", "auto").lower(),
            llm_timeout_seconds=_env_float(
                "LLM_TIMEOUT_SECONDS", _env_float("REQUEST_TIMEOUT_SECONDS", 20.0)
            ),
            llm_max_retries=_env_int("LLM_MAX_RETRIES", 2),
            llm_temperature=_env_float("LLM_TEMPERATURE", 0.2),
            llm_max_tokens=_env_int("LLM_MAX_TOKENS", 1500),
            llm_disable_thinking=_env_bool("LLM_DISABLE_THINKING", True),
            anthropic_api_key=_first_env(("ANTHROPIC_API_KEY",)),
            jwt_secret=os.getenv("JWT_SECRET") or "dev-only-jwt-secret-change-me-32bytes-min",
            jwt_access_ttl_minutes=_env_int("JWT_ACCESS_TTL_MINUTES", 60),
            jwt_refresh_ttl_minutes=_env_int("JWT_REFRESH_TTL_MINUTES", 60 * 24 * 7),
            pii_encryption_key=_first_env(("PII_ENCRYPTION_KEY",)),
            cors_origins=_env_tuple(
                "CORS_ORIGINS", ("http://localhost:8000", "http://127.0.0.1:8000")
            ),
            rate_limit_enabled=_env_bool("RATE_LIMIT_ENABLED", True),
            rate_limit_default=_env_str("RATE_LIMIT_DEFAULT", "300/minute"),
            rate_limit_login=_env_str("RATE_LIMIT_LOGIN", "10/minute"),
            bootstrap_admin_username=_env_str("ADMIN_USERNAME", "admin"),
            bootstrap_admin_password=_first_env(("ADMIN_PASSWORD",)),
            scheduler_enabled=_env_bool("SCHEDULER_ENABLED", True),
            data_mode=_env_str("DATA_MODE", "auto").lower(),
            market_cache_ttl_seconds=_env_int("MARKET_CACHE_TTL_SECONDS", 300),
            evds_api_key=_first_env(("EVDS_API_KEY",)),
            checkpoint_db=os.getenv("CHECKPOINT_DB") or str(BASE_DIR / "checkpoints.sqlite"),
            auto_migrate=_env_bool("AUTO_MIGRATE", True),
            seed_demo=_env_bool("SEED_DEMO", False),
            metrics_public=_env_bool("METRICS_PUBLIC", False),
            metrics_token=_first_env(("METRICS_TOKEN",)),
            lock_backend=_env_str("LOCK_BACKEND", "memory").lower(),
            rate_limit_storage=_env_str("RATE_LIMIT_STORAGE", "memory").lower(),
            redis_url=_first_env(("REDIS_URL",)),
        )


__all__ = [
    "BASE_DIR",
    "ConfigurationError",
    "DEFAULT_LLM_BASE_URL",
    "DEFAULT_LLM_MODEL",
    "Settings",
    "dotenv_candidates",
    "load_dotenv_file",
    "mask_secret",
]

"""Real LLM client layer for the Otonom Finansal Danışman.

This layer exclusively uses *real* LLM providers (OpenAI and Anthropic) with
API keys loaded through python-dotenv (``core.config.Settings``). No fake or
mock chat models exist anywhere in the production code path.

Design:
    * :class:`LLMClient` — abstract interface every provider implements. The
      whole application (agents, advisor service) depends only on this
      interface, so:
        - production wiring uses :class:`OpenAIClient` or
          :class:`AnthropicClient` (selected from the configured key);
        - integration tests inject a deterministic stub that satisfies the
          exact same interface — legitimate dependency injection, **not**
          a FakeListChatModel.
    * :func:`get_llm_client` — the DI factory. It raises
      :class:`LLMConfigurationError` with a clear, actionable message when no
      API key is configured, so a misconfigured deployment fails loudly
      instead of silently degrading.

Providers are constructed lazily with the official asynchronous SDK clients.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.config import Settings
from core.logging import get_logger

logger = get_logger("otonom.llm")


class LLMConfigurationError(RuntimeError):
    """Raised when an LLM provider cannot be built from the configuration."""


class LLMClient(ABC):
    """Common interface for a real chat-completion provider.

    All agent nodes talk to this interface; they never import a concrete SDK
    client. This keeps the orchestration layer provider-agnostic and makes the
    system testable with an injected deterministic implementation.
    """

    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 800,
        temperature: float = 0.4,
    ) -> str:
        """Run a single chat completion and return the assistant's text.

        Args:
            system: System prompt (role/instructions).
            user: User message (the actual task content).
            max_tokens: Upper bound on completion tokens.
            temperature: Sampling temperature (lower = more deterministic).

        Returns:
            The model's textual response, stripped of surrounding whitespace.

        Raises:
            LLMProviderError: On transport/API/validation failures — wrapped so
                callers can handle provider errors uniformly.
        """
        raise NotImplementedError


class LLMProviderError(RuntimeError):
    """Raised when an upstream LLM call fails (network, API, rate limit…)."""


class OpenAIClient(LLMClient):
    """Real OpenAI chat-completions client (``openai`` SDK, async)."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", timeout: float = 60.0) -> None:
        if not api_key or not api_key.strip():
            raise LLMConfigurationError("OPENAI_API_KEY boş. .env dosyasını kontrol edin.")
        self._model = model
        # Lazy import: the ``openai`` package is only required when this
        # provider is actually selected.
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout)

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 800,
        temperature: float = 0.4,
    ) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                temperature=temperature,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            content = response.choices[0].message.content or ""
            return content.strip()
        except LLMProviderError:
            raise
        except Exception as exc:  # network, auth, rate-limit, validation
            logger.warning("openai_completion_failed", error=str(exc))
            raise LLMProviderError(f"OpenAI çağrısı başarısız: {exc}") from exc


class AnthropicClient(LLMClient):
    """Real Anthropic messages client (``anthropic`` SDK, async)."""

    def __init__(
        self, api_key: str, model: str = "claude-3-5-haiku-latest", timeout: float = 60.0
    ) -> None:
        if not api_key or not api_key.strip():
            raise LLMConfigurationError("ANTHROPIC_API_KEY boş. .env dosyasını kontrol edin.")
        self._model = model
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout)

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 800,
        temperature: float = 0.4,
    ) -> str:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            parts = [
                block.text for block in response.content if getattr(block, "type", None) == "text"
            ]
            return "\n".join(parts).strip()
        except LLMProviderError:
            raise
        except Exception as exc:
            logger.warning("anthropic_completion_failed", error=str(exc))
            raise LLMProviderError(f"Anthropic çağrısı başarısız: {exc}") from exc


def get_llm_client(settings: Settings) -> LLMClient:
    """DI factory: build the real LLM client selected by the configuration.

    Provider selection order (one key is enough):
        1. ``OPENAI_API_KEY`` present → :class:`OpenAIClient`.
        2. ``ANTHROPIC_API_KEY`` present → :class:`AnthropicClient`.
        3. Neither → :class:`LLMConfigurationError` with an actionable message.

    Args:
        settings: Application settings (loaded from ``.env``).

    Returns:
        A concrete :class:`LLMClient` implementation.

    Raises:
        LLMConfigurationError: If no provider credential is configured.
    """
    if settings.openai_api_key:
        logger.info("llm_provider_selected", provider="openai")
        return OpenAIClient(api_key=settings.openai_api_key)
    if settings.anthropic_api_key:
        logger.info("llm_provider_selected", provider="anthropic")
        return AnthropicClient(api_key=settings.anthropic_api_key)
    raise LLMConfigurationError(
        "LLM sağlayıcısı yapılandırılmadı: .env içine OPENAI_API_KEY veya "
        "ANTHROPIC_API_KEY tanımlayın (bkz. .env.example)."
    )


__all__ = [
    "LLMClient",
    "LLMProviderError",
    "LLMConfigurationError",
    "OpenAIClient",
    "AnthropicClient",
    "get_llm_client",
]

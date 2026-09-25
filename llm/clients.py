"""LLM client layer: one interface, a live OpenAI-compatible client, optional
Anthropic client and the deterministic demo client.

Design:
    * :class:`LLMClient` — the single chat interface every provider
      implements (``chat`` for messages/tools/JSON mode, ``complete`` as a
      convenience wrapper kept for backward compatibility).
    * :class:`OpenAICompatibleClient` — ``openai.AsyncOpenAI`` pointed at any
      OpenAI-compatible Chat Completions endpoint (default: DeepSeek V4 Flash
      via the Evren provider). DeepSeek's ``reasoning_content`` is ignored.
    * :class:`AnthropicClient` — optional path (``LLM_PROVIDER=anthropic``).
    * :func:`get_llm_client` — DI factory: returns the live client when the
      effective mode is ``live`` and the deterministic demo client otherwise.
      It never raises for a missing key in ``auto`` mode (no more 503s).

Every provider failure is normalised into :class:`LLMProviderError` with a
machine readable ``kind`` so the gateway can decide between repair and
fallback. Error messages never contain the API key.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from core.config import Settings, mask_secret
from core.logging import get_logger

logger = get_logger("otonom.llm")

# LLMProviderError.kind değerleri
ERROR_KINDS = (
    "timeout",
    "rate_limit",
    "server",
    "auth",
    "bad_request",
    "network",
    "invalid_json",
    "validation",
    "number_guard",
    "tools_unsupported",
    "config",
    "unknown",
)


class LLMConfigurationError(RuntimeError):
    """Raised when an LLM provider cannot be built from the configuration."""


class LLMProviderError(RuntimeError):
    """Raised when an upstream LLM call fails.

    Attributes:
        kind: Normalised failure category (see :data:`ERROR_KINDS`).
    """

    def __init__(self, message: str, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind if kind in ERROR_KINDS else "unknown"


@dataclass
class ToolCall:
    """A single tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_message(self) -> dict[str, Any]:
        """OpenAI ``tool_calls`` entry representation."""
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": json.dumps(self.arguments)},
        }


@dataclass
class LLMResponse:
    """Normalised chat completion result."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    latency_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMClient(ABC):
    """Common chat interface for live and demo providers."""

    #: ``live`` for network providers, ``demo`` for the deterministic client.
    mode: str = "live"
    model: str = ""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Run one chat completion.

        Args:
            messages: OpenAI style message list.
            json_mode: Request a JSON object response when supported.
            tools: Optional OpenAI tool (function) definitions.
            max_tokens: Completion token cap (``None`` → client default).
            temperature: Sampling temperature (``None`` → client default).

        Returns:
            The normalised :class:`LLMResponse`.

        Raises:
            LLMProviderError: On any transport/API failure.
        """
        raise NotImplementedError

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 800,
        temperature: float = 0.4,
    ) -> str:
        """Single system+user completion returning plain text."""
        response = await self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.content.strip()


def _classify_openai_error(exc: Exception) -> str:
    """Map an ``openai`` SDK exception to an error kind."""
    import openai

    if isinstance(exc, openai.APITimeoutError):
        return "timeout"
    if isinstance(exc, openai.RateLimitError):
        return "rate_limit"
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return "auth"
    if isinstance(exc, openai.BadRequestError):
        return "bad_request"
    if isinstance(exc, openai.InternalServerError):
        return "server"
    if isinstance(exc, openai.APIStatusError):
        return "server" if getattr(exc, "status_code", 500) >= 500 else "bad_request"
    if isinstance(exc, openai.APIConnectionError):
        return "network"
    return "unknown"


class OpenAICompatibleClient(LLMClient):
    """Live client for any OpenAI-compatible Chat Completions endpoint."""

    mode = "live"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 20.0,
        max_retries: int = 2,
        temperature: float = 0.2,
        max_tokens: int = 1500,
        disable_thinking: bool = True,
    ) -> None:
        if not api_key or not api_key.strip():
            raise LLMConfigurationError("LLM API anahtarı boş. .env dosyasını kontrol edin.")
        from openai import AsyncOpenAI

        self.model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._json_mode_supported = True
        # DeepSeek V4 (vLLM) muhakeme modunu kapatır: daha hızlı, token bütçesi
        # içeriğe kalır. Desteklemeyen sağlayıcıda 400 gelirse kendiliğinden kapanır.
        self._extra_body: dict[str, Any] | None = (
            {"chat_template_kwargs": {"thinking": False}} if disable_thinking else None
        )
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries
        )
        self._key_mask = mask_secret(api_key)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self._temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self._max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        use_json = json_mode and self._json_mode_supported and not tools
        if use_json:
            kwargs["response_format"] = {"type": "json_object"}
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body

        started = time.perf_counter()
        response = None
        for _attempt in range(3):
            try:
                response = await self._client.chat.completions.create(**kwargs)
                break
            except Exception as exc:
                kind = _classify_openai_error(exc)
                if kind == "bad_request" and "extra_body" in kwargs:
                    # Sağlayıcı ek parametreyi tanımıyor → bir daha gönderme.
                    logger.info("llm_extra_body_unsupported", model=self.model)
                    self._extra_body = None
                    kwargs.pop("extra_body", None)
                elif kind == "bad_request" and "response_format" in kwargs:
                    # Sağlayıcı response_format desteklemiyor → düz metne düş.
                    logger.info("llm_json_mode_unsupported_fallback_text", model=self.model)
                    self._json_mode_supported = False
                    kwargs.pop("response_format", None)
                elif kind == "bad_request" and tools:
                    raise LLMProviderError(
                        "Sağlayıcı araç çağrısını desteklemiyor.", kind="tools_unsupported"
                    ) from exc
                else:
                    raise self._wrap(exc) from exc
        if response is None:
            raise LLMProviderError("LLM isteği kabul edilmedi.", kind="bad_request")

        latency_ms = (time.perf_counter() - started) * 1000.0
        choice = response.choices[0]
        message = choice.message
        # DeepSeek ``reasoning_content`` alanı bilinçli olarak yok sayılır.
        content = message.content or ""
        tool_calls: list[ToolCall] = []
        for call in message.tool_calls or []:
            fn = getattr(call, "function", None)
            if fn is None:
                continue
            try:
                args = json.loads(fn.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=call.id, name=fn.name, arguments=args))
        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=content.strip(),
            tool_calls=tool_calls,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            model=str(getattr(response, "model", self.model) or self.model),
            latency_ms=latency_ms,
        )

    def _wrap(self, exc: Exception) -> LLMProviderError:
        kind = _classify_openai_error(exc)
        # Hata mesajında anahtar geçmemesi için yalnızca tür ve sınıf adı.
        logger.warning("llm_call_failed", kind=kind, error_type=type(exc).__name__)
        return LLMProviderError(f"LLM çağrısı başarısız ({kind}).", kind=kind)


class AnthropicClient(LLMClient):
    """Optional live Anthropic Messages client (``LLM_PROVIDER=anthropic``)."""

    mode = "live"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-haiku-4-5-20251001",
        timeout: float = 20.0,
        max_retries: int = 2,
        temperature: float = 0.2,
        max_tokens: int = 1500,
    ) -> None:
        if not api_key or not api_key.strip():
            raise LLMConfigurationError("ANTHROPIC_API_KEY boş. .env dosyasını kontrol edin.")
        from anthropic import AsyncAnthropic

        self.model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        if tools:
            raise LLMProviderError(
                "Anthropic yolu araç çağrısı sunmuyor.", kind="tools_unsupported"
            )
        system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
        convo = [
            {"role": m["role"], "content": str(m.get("content") or "")}
            for m in messages
            if m.get("role") in {"user", "assistant"}
        ]
        started = time.perf_counter()
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens or self._max_tokens,
                temperature=self._temperature if temperature is None else temperature,
                system=system,
                messages=convo,  # type: ignore[arg-type]  # SDK expects TypedDicts; plain dicts are valid
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("anthropic_call_failed", error_type=type(exc).__name__)
            raise LLMProviderError("Anthropic çağrısı başarısız.", kind="unknown") from exc
        parts = [
            str(getattr(b, "text", ""))
            for b in response.content
            if getattr(b, "type", None) == "text"
        ]
        usage = getattr(response, "usage", None)
        return LLMResponse(
            content="\n".join(parts).strip(),
            prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )


def build_live_client(settings: Settings) -> LLMClient:
    """Build the live client for the configured provider.

    Raises:
        LLMConfigurationError: When the provider's key is missing.
    """
    if settings.llm_provider == "anthropic":
        return AnthropicClient(
            api_key=settings.anthropic_api_key or "",
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
    return OpenAICompatibleClient(
        api_key=settings.llm_api_key or "",
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        disable_thinking=settings.llm_disable_thinking,
    )


def get_llm_client(settings: Settings) -> LLMClient:
    """DI factory: live client in ``live`` mode, deterministic demo otherwise.

    Args:
        settings: Application settings.

    Returns:
        A concrete :class:`LLMClient`; never raises in ``auto``/``demo``.

    Raises:
        LLMConfigurationError: Only when ``LLM_MODE=live`` without a key.
    """
    from llm.demo import DeterministicLLM

    mode = settings.effective_llm_mode
    if mode == "live":
        if not settings.has_llm_credentials:
            raise LLMConfigurationError(
                "LLM_MODE=live ancak API anahtarı yok. .env içine LLM_API_KEY ekleyin."
            )
        return build_live_client(settings)
    return DeterministicLLM()


__all__ = [
    "AnthropicClient",
    "ERROR_KINDS",
    "LLMClient",
    "LLMConfigurationError",
    "LLMProviderError",
    "LLMResponse",
    "OpenAICompatibleClient",
    "ToolCall",
    "build_live_client",
    "get_llm_client",
]

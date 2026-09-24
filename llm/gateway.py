"""LLM gateway — the only path from services to a language model.

Pipeline for a grounded task (:meth:`LLMGateway.generate`):

    context ─► redact (KVKK) ─► prompt (<data> block, [GOREV] marker)
            ─► live client (JSON mode) ─► JSON extraction ─► pydantic
            ─► number guard ─► (1 repair attempt) ─► result
                                  │ failure (timeout/429/5xx/invalid JSON…)
                                  └─► deterministic demo output, mode="fallback"

The gateway never raises for provider problems: callers always get a valid,
schema-conforming object plus ``mode`` (``live`` / ``demo`` / ``fallback``)
and ``error_kind``. Usage (latency, tokens, mode, error) is recorded in memory
for ``/llm/status``, in Prometheus and — through an optional async recorder —
in the ``llm_usage`` table.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from core.config import Settings
from core.logging import get_logger
from llm.clients import LLMClient, LLMProviderError, LLMResponse
from llm.demo import DeterministicLLM
from llm.guard import NumberGuardError, check_numbers, texts_of
from llm.prompts import build_messages, repair_message, schema_for
from llm.redaction import redact

logger = get_logger("otonom.llm.gateway")

T = TypeVar("T", bound=BaseModel)

UsageRecorder = Callable[[dict[str, Any]], Awaitable[None]]

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Extract the first JSON object from model text (handles code fences).

    Raises:
        LLMProviderError: ``kind="invalid_json"`` when nothing parses.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(cleaned)
        if not match:
            raise LLMProviderError("Yanıtta JSON bulunamadı.", kind="invalid_json") from None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMProviderError("Yanıttaki JSON geçersiz.", kind="invalid_json") from exc
    if not isinstance(value, dict):
        raise LLMProviderError("Yanıt JSON nesnesi değil.", kind="invalid_json")
    return value


@dataclass
class GenerationResult(Generic[T]):
    """Outcome of a grounded generation."""

    data: T
    mode: str  # live | demo | fallback
    error_kind: str | None = None
    latency_ms: float = 0.0
    tokens: int = 0

    def meta(self) -> dict[str, Any]:
        """Serializable metadata attached to API responses."""
        return {"llm_mode": self.mode, "llm_error_kind": self.error_kind}


@dataclass
class LLMStats:
    """In-memory counters surfaced on ``GET /llm/status``."""

    calls: int = 0
    failures: int = 0
    fallbacks: int = 0
    last_latency_ms: float | None = None
    last_error_kind: str | None = None
    by_mode: dict[str, int] = field(default_factory=dict)


class LLMGateway:
    """Validated, guarded, fail-safe access to the configured LLM."""

    def __init__(
        self,
        settings: Settings,
        client: LLMClient | None = None,
        *,
        demo: DeterministicLLM | None = None,
        recorder: UsageRecorder | None = None,
    ) -> None:
        from llm.clients import get_llm_client

        self._settings = settings
        self._demo = demo or DeterministicLLM()
        self._client = client if client is not None else get_llm_client(settings)
        self._recorder = recorder
        self.stats = LLMStats()

    # -- properties --------------------------------------------------------

    @property
    def mode(self) -> str:
        """``live`` when a network client is configured, else ``demo``."""
        return "live" if getattr(self._client, "mode", "demo") == "live" else "demo"

    @property
    def client(self) -> LLMClient:
        return self._client

    @property
    def demo(self) -> DeterministicLLM:
        return self._demo

    def set_recorder(self, recorder: UsageRecorder | None) -> None:
        """Attach the async usage recorder (DB writer) after startup."""
        self._recorder = recorder

    def status(self) -> dict[str, Any]:
        """Safe status payload (never includes the API key)."""
        return {
            "mode": self.mode,
            "model": self._client.model if self.mode == "live" else self._demo.model,
            "configured_model": self._settings.llm_model,
            "base_url_host": self._settings.llm_base_url_host,
            "key_present": self._settings.has_llm_credentials,
            "last_latency_ms": (
                round(self.stats.last_latency_ms, 1)
                if self.stats.last_latency_ms is not None
                else None
            ),
            "calls": self.stats.calls,
            "failures": self.stats.failures,
            "fallbacks": self.stats.fallbacks,
            "last_error_kind": self.stats.last_error_kind,
        }

    # -- grounded generation ------------------------------------------------

    async def generate(
        self,
        task: str,
        context: dict[str, Any],
        *,
        schema: type[T] | None = None,
        names: tuple[str, ...] = (),
    ) -> GenerationResult[T]:
        """Run a grounded task and return a validated object.

        Args:
            task: Task key of :data:`llm.prompts.TASKS`.
            context: Deterministic facts (JSON-serializable) for the model.
            schema: Output schema (defaults to the task's schema).
            names: Personal names to scrub from free text (KVKK).

        Returns:
            A :class:`GenerationResult`; never raises for provider issues.
        """
        model_cls: type[T] = schema or schema_for(task)  # type: ignore[assignment]
        safe_context = redact(context, names)
        messages = build_messages(task, safe_context)

        if self.mode == "live":
            started = time.perf_counter()
            try:
                obj, response = await self._live_attempts(messages, model_cls, safe_context)
                latency = (time.perf_counter() - started) * 1000.0
                await self._record(task, "live", response, latency, None)
                return GenerationResult(
                    data=obj, mode="live", latency_ms=latency, tokens=response.total_tokens
                )
            except LLMProviderError as exc:
                latency = (time.perf_counter() - started) * 1000.0
                logger.warning("llm_fallback", task=task, error_kind=exc.kind)
                await self._record(task, "fallback", None, latency, exc.kind)
                obj = await self._demo_generate(messages, model_cls)
                return GenerationResult(
                    data=obj, mode="fallback", error_kind=exc.kind, latency_ms=latency
                )

        started = time.perf_counter()
        obj = await self._demo_generate(messages, model_cls)
        latency = (time.perf_counter() - started) * 1000.0
        await self._record(task, "demo", None, latency, None)
        return GenerationResult(data=obj, mode="demo", latency_ms=latency)

    async def _live_attempts(
        self, messages: list[dict[str, Any]], model_cls: type[T], context: dict[str, Any]
    ) -> tuple[T, LLMResponse]:
        convo = list(messages)
        last_error: LLMProviderError | None = None
        for attempt in range(2):  # ilk deneme + 1 onarım
            response = await self._client.chat(convo, json_mode=True)
            try:
                obj = self._validate(response.content, model_cls, context)
                return obj, response
            except LLMProviderError as exc:
                last_error = exc
                logger.info("llm_output_rejected", kind=exc.kind, attempt=attempt)
                convo = convo + [
                    {"role": "assistant", "content": response.content},
                    repair_message(str(exc)),
                ]
        assert last_error is not None
        raise last_error

    @staticmethod
    def _validate(content: str, model_cls: type[T], context: dict[str, Any]) -> T:
        payload = extract_json(content)
        try:
            obj = model_cls.model_validate(payload)
        except ValidationError as exc:
            raise LLMProviderError(
                f"Şema doğrulaması başarısız: {exc.error_count()} hata", kind="validation"
            ) from exc
        try:
            for text in texts_of(obj.model_dump()):
                check_numbers(text, context)
        except NumberGuardError as exc:
            raise LLMProviderError(str(exc), kind="number_guard") from exc
        return obj

    async def _demo_generate(self, messages: list[dict[str, Any]], model_cls: type[T]) -> T:
        response = await self._demo.chat(messages, json_mode=True)
        return model_cls.model_validate(extract_json(response.content))

    # -- raw chat (copilot) -------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
        purpose: str = "chat",
        force_demo: bool = False,
    ) -> tuple[LLMResponse, str, str | None]:
        """Raw chat with live→demo fallback, returning ``(response, mode, error_kind)``.

        ``tools_unsupported`` errors are re-raised so the caller can switch
        to the ReAct text protocol.
        """
        if self.mode == "live" and not force_demo:
            started = time.perf_counter()
            try:
                response = await self._client.chat(messages, tools=tools, json_mode=json_mode)
                await self._record(purpose, "live", response, response.latency_ms, None)
                return response, "live", None
            except LLMProviderError as exc:
                if exc.kind == "tools_unsupported":
                    raise
                latency = (time.perf_counter() - started) * 1000.0
                await self._record(purpose, "fallback", None, latency, exc.kind)
                response = await self._demo.chat(messages, tools=tools, json_mode=json_mode)
                return response, "fallback", exc.kind
        response = await self._demo.chat(messages, tools=tools, json_mode=json_mode)
        await self._record(purpose, "demo", response, response.latency_ms, None)
        return response, "demo", None

    # -- bookkeeping ----------------------------------------------------------

    async def _record(
        self,
        purpose: str,
        mode: str,
        response: LLMResponse | None,
        latency_ms: float,
        error_kind: str | None,
    ) -> None:
        self.stats.calls += 1
        self.stats.by_mode[mode] = self.stats.by_mode.get(mode, 0) + 1
        if mode != "demo":
            self.stats.last_latency_ms = latency_ms
        if error_kind:
            self.stats.failures += 1
            self.stats.last_error_kind = error_kind
        if mode == "fallback":
            self.stats.fallbacks += 1
        tokens_in = response.prompt_tokens if response else 0
        tokens_out = response.completion_tokens if response else 0
        try:
            from core.metrics import record_llm_call

            record_llm_call(purpose, mode, latency_ms, tokens_in + tokens_out, error_kind)
        except Exception:  # noqa: BLE001 - metrik hatası akışı bozmamalı
            pass
        logger.info(
            "llm_call",
            purpose=purpose,
            mode=mode,
            latency_ms=round(latency_ms, 1),
            tokens=tokens_in + tokens_out,
            error_kind=error_kind,
        )
        if self._recorder is not None:
            try:
                await self._recorder(
                    {
                        "purpose": purpose,
                        "mode": mode,
                        "model": (response.model if response else "") or "",
                        "prompt_tokens": tokens_in,
                        "completion_tokens": tokens_out,
                        "latency_ms": latency_ms,
                        "error_kind": error_kind,
                    }
                )
            except Exception:  # noqa: BLE001
                logger.warning("llm_usage_record_failed")


__all__ = ["GenerationResult", "LLMGateway", "LLMStats", "extract_json"]

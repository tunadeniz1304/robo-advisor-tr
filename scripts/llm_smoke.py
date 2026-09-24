"""LLM duman testi: yapılandırılmış modelle tek bir gerçek çağrı yapar.

Kullanım::

    python scripts/llm_smoke.py

Çıktı:
    * anahtar varsa  → ``OK model=deepseek-v4-flash latency=812ms`` (exit 0)
    * anahtar yoksa  → ``DEMO modu: API anahtarı yok ...`` (exit 0)
    * çağrı başarısız → ``HATA kind=timeout ...`` (exit 1)

Anahtar hiçbir koşulda ekrana yazdırılmaz.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except AttributeError:  # pragma: no cover
        pass

from core.config import Settings  # noqa: E402
from llm.clients import LLMProviderError, build_live_client  # noqa: E402


async def main() -> int:
    settings = Settings.load()
    if settings.effective_llm_mode != "live":
        reason = "LLM_MODE=demo" if settings.llm_mode == "demo" else "API anahtarı yok"
        print(f"DEMO modu: {reason}; uygulama deterministik demo çıktısıyla çalışır.")
        return 0
    client = build_live_client(settings)
    started = time.perf_counter()
    try:
        response = await client.chat(
            [
                {"role": "system", "content": "Kısa yanıt ver."},
                {"role": "user", "content": "Yalnızca OK yaz."},
            ],
            max_tokens=16,
            temperature=0.0,
        )
    except LLMProviderError as exc:
        print(f"HATA kind={exc.kind} host={settings.llm_base_url_host} model={settings.llm_model}")
        return 1
    latency = (time.perf_counter() - started) * 1000.0
    model = response.model or settings.llm_model
    print(f"OK model={model} latency={latency:.0f}ms host={settings.llm_base_url_host}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

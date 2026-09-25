"""Otonom Finansal Danışman (Robo-Advisor) — uvicorn giriş noktası.

Adım 5: Bu dosya, uvicorn sunucusunu kök modülden bağlar.

    python main.py                 # geliştirme sunucusu (reload ile)
    uvicorn main:app --reload      # alternatif

Windows tr-TR konsolunda Türkçe karakterlerin doğru yazdırılabilmesi için
stdout/stderr utf-8 olarak yapılandırılır (Python 3.11 + cp1254 locale).

Uygulama (FastAPI) ``core.app.create_app`` ile üretilir; loglama structlog
ile, ayarlar ``.env`` üzerinden python-dotenv ile yüklenir.
"""

import sys

# Windows cp1254 konsolunda Türkçe çıktıların çökmemesi için utf-8'e sabitle.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # TextIO lacks it; real streams have it
    except AttributeError:  # pragma: no cover - bazı ortamlarda yok
        pass

from core.app import create_app  # noqa: E402
from core.config import load_dotenv_file  # noqa: E402
from core.logging import configure_logging_from_env, get_logger  # noqa: E402

load_dotenv_file()
configure_logging_from_env()

logger = get_logger("otonom.main")

app = create_app()

if __name__ == "__main__":  # pragma: no cover - elle çalıştırılan geliştirme sunucusu
    import uvicorn

    logger.info("starting_uvicorn", host="0.0.0.0", port=8000)
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )

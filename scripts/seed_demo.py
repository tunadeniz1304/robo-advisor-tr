"""Demo verisini yükler (3 persona + danışman). Kullanım: ``python scripts/seed_demo.py``."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import Settings  # noqa: E402
from core.container import build_container  # noqa: E402
from core.crypto import configure_encryption  # noqa: E402
from core.database import adopt_engine, create_engine_from_url, init_db  # noqa: E402
from services.demo_seed import seed_demo  # noqa: E402
from services.reference_data import seed_reference_data  # noqa: E402


async def main() -> int:
    settings = Settings.load()
    configure_encryption(settings.pii_encryption_key)
    engine = create_engine_from_url(settings.database_url)
    adopt_engine(engine)
    await init_db(engine, settings)
    await seed_reference_data()
    container = build_container(settings)
    try:
        print(json.dumps(await seed_demo(container), ensure_ascii=False))
    finally:
        await container.aclose()
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

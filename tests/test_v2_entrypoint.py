"""``main.py`` giriş noktası: uygulamayı üretir, gerçek ``.env`` dosyasını okumaz."""

from __future__ import annotations

import importlib
import sys

import pytest
from fastapi import FastAPI


def test_main_module_builds_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.config

    calls: list[bool] = []
    monkeypatch.setattr(core.config, "load_dotenv_file", lambda *a, **k: calls.append(True))
    monkeypatch.delitem(sys.modules, "main", raising=False)
    module = importlib.import_module("main")
    assert isinstance(module.app, FastAPI)
    assert calls  # .env yükleyicisi çağrıldı ama testte etkisiz (stub)

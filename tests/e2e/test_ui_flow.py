"""Browser E2E (audit D.20): the SPA driven through a real Chromium.

login → onboarding questionnaire → new goal → rebalance proposal → approval
→ the proposal shows "Yürütüldü" and the truthful header badges are visible.

The app is started in a subprocess (``uvicorn main:app``) on a free loopback
port with a temp SQLite database, snapshot data, demo LLM and the demo seed.
Skipped cleanly when Playwright or its Chromium build is not installed:

    pip install pytest-playwright && python -m playwright install chromium
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
DEMO_USER = ("demo.genc", "Demo!2345")
STARTUP_TIMEOUT_S = 90
UI_TIMEOUT_MS = 30_000

pytestmark = pytest.mark.e2e
sync_api.expect.set_options(timeout=UI_TIMEOUT_MS)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_healthy(url: str, proc: subprocess.Popen[bytes], log: Path) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = log.read_text(encoding="utf-8", errors="replace")[-3000:]
            pytest.fail(f"Sunucu başlarken kapandı (kod {proc.returncode}):\n{tail}")
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    pytest.fail(f"Sunucu {STARTUP_TIMEOUT_S} sn içinde hazır olmadı.")


@pytest.fixture(scope="module")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Run the real app in a subprocess; yield its base URL."""
    tmp = tmp_path_factory.mktemp("e2e-server")
    port = _free_port()
    env = {
        **os.environ,
        "APP_ENV": "dev",
        "LLM_MODE": "demo",
        "DATA_MODE": "snapshot",
        "SEED_DEMO": "true",
        "SCHEDULER_ENABLED": "false",
        "BCRYPT_ROUNDS": "4",
        "RATE_LIMIT_ENABLED": "false",
        "LOCK_BACKEND": "memory",
        "RATE_LIMIT_STORAGE": "memory",
        "LOG_LEVEL": "WARNING",
        "DATABASE_URL": f"sqlite+aiosqlite:///{(tmp / 'e2e.db').as_posix()}",
        "CHECKPOINT_DB": str(tmp / "checkpoints.sqlite"),
        "PYTHONIOENCODING": "utf-8",
    }
    log = tmp / "server.log"
    cmd = [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1"]
    with log.open("wb") as out:
        proc = subprocess.Popen(
            [*cmd, "--port", str(port)], cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT
        )
        url = f"http://127.0.0.1:{port}"
        try:
            _wait_healthy(url, proc, log)
            yield url
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    """Headless Chromium, or skip when the browser build is missing."""
    try:
        pw = sync_api.sync_playwright().start()
    except Exception as exc:  # noqa: BLE001 - sürücü yoksa atla
        pytest.skip(f"Playwright başlatılamadı: {exc}")
    try:
        chromium = pw.chromium.launch(headless=True)
    except Exception as exc:  # noqa: BLE001 - tarayıcı kurulu değilse atla
        pw.stop()
        pytest.skip(f"Chromium kurulu değil (python -m playwright install chromium): {exc}")
    try:
        yield chromium
    finally:
        chromium.close()
        pw.stop()


@pytest.fixture()
def page(browser: Any, live_server: str) -> Iterator[Any]:
    context = browser.new_context(base_url=live_server, locale="tr-TR")
    pg = context.new_page()
    pg.set_default_timeout(UI_TIMEOUT_MS)
    errors: list[str] = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.console_errors = errors  # type: ignore[attr-defined]
    try:
        yield pg
    finally:
        context.close()


def _login(page: Any, username: str, password: str) -> None:
    page.goto("/")
    page.get_by_label("Kullanıcı adı").fill(username)
    page.get_by_label("Şifre").fill(password)
    page.get_by_role("button", name="Giriş yap").click()
    sync_api.expect(page.locator("#nav")).to_be_visible()
    sync_api.expect(page.locator("#kpi-value")).not_to_have_text("–")


def _open(page: Any, name: str) -> None:
    page.locator("#nav").get_by_role("button", name=name, exact=True).click()


def _fill_questionnaire(page: Any) -> None:
    _open(page, "Risk profili")
    body = page.locator("#q-body")
    next_btn = page.locator("#q-next")
    sync_api.expect(body.locator("input[type=radio]").first).to_be_visible()
    for _ in range(40):
        options = body.locator("input[type=radio]")
        options.nth(options.count() // 2).check()
        if next_btn.inner_text().strip() == "Gönder":
            next_btn.click()
            break
        next_btn.click()
    result = page.locator("#q-result")
    sync_api.expect(result).not_to_be_empty()
    confirm = page.locator("#q-confirm")
    if confirm.count():
        confirm.click()
    sync_api.expect(result).to_contain_text("Risk seviyeniz")


def _add_goal(page: Any, name: str) -> None:
    _open(page, "Hedefler")
    form = page.locator("#goal-form")
    form.get_by_label("Hedef türü").select_option("ev")
    form.get_by_label("Ad", exact=True).fill(name)
    form.get_by_label("Hedef (bugünkü TL)").fill("2000000")
    form.get_by_label("Vade (yıl)").fill("7")
    form.get_by_label("Aylık katkı (TL)").fill("10000")
    form.get_by_role("button", name="Ekle").click()
    sync_api.expect(page.locator("#goal-list")).to_contain_text(name)
    sync_api.expect(page.locator("#goal-summary")).to_contain_text("Başarı olasılığı")


def _propose_and_approve(page: Any) -> None:
    _open(page, "Yeniden dengeleme")
    page.get_by_role("button", name="Öneri oluştur").click()
    approve = page.locator("#proposal button[data-approve]")
    sync_api.expect(approve).to_be_visible(timeout=60_000)
    sync_api.expect(page.locator("#proposal .status-pill")).to_have_text(
        re.compile("Onay bekliyor")
    )
    approve.click()
    pill = page.locator("#proposal .status-pill")
    try:
        sync_api.expect(pill).to_have_attribute("data-status", "YURUTULDU", timeout=60_000)
    except AssertionError as exc:
        status = page.locator("#app-status").inner_text()
        shown = page.locator("#proposal").inner_text()[:300]
        raise AssertionError(f"Öneri yürütülmedi. Durum: {status!r}; öneri: {shown!r}") from exc
    sync_api.expect(pill).to_have_text(re.compile("Yürütüldü"))


def _assert_badges(page: Any) -> None:
    data = page.locator("#badge-data")
    sync_api.expect(data).to_be_visible()
    sync_api.expect(data).to_have_text(re.compile(r"^Veri: (gerçek|snapshot|yaklaşık)$"))
    sync_api.expect(data).to_have_attribute("title", re.compile("Kaynak"))
    sync_api.expect(page.locator("#badge-quality")).to_have_text(
        re.compile(r"^Veri kalitesi: (iyi|uyarı|hata)$")
    )
    sync_api.expect(page.locator("#badge-ai")).to_have_text("AI: Demo")


def test_customer_journey_in_browser(page: Any) -> None:
    _login(page, *DEMO_USER)
    _assert_badges(page)
    _fill_questionnaire(page)
    _add_goal(page, "E2E Yazlık")
    _propose_and_approve(page)
    _assert_badges(page)
    assert not page.console_errors, page.console_errors


def test_every_screen_renders_without_console_errors(page: Any) -> None:
    _login(page, *DEMO_USER)
    for name in ("Analitik", "Stres lab", "Copilot", "Veri kalitesi", "Panel"):
        _open(page, name)
        sync_api.expect(
            page.locator("#nav").get_by_role("button", name=name, exact=True)
        ).to_have_attribute("aria-current", "page")
    _open(page, "Veri kalitesi")
    sync_api.expect(page.locator("#dq-table tbody tr").first).to_be_visible()
    _open(page, "Analitik")
    sync_api.expect(page.locator("#tearsheet")).to_contain_text("TWR (kümülatif)")
    sync_api.expect(page.locator("#tearsheet")).to_contain_text("MWR (yıllık, XIRR)")
    page.get_by_role("button", name="Çalıştır").click()
    sync_api.expect(page.locator("#backtest")).to_contain_text("BIST 100", timeout=60_000)
    sync_api.expect(page.locator("#tbl-backtest tbody tr").first).to_be_attached()
    _open(page, "Stres lab")
    page.get_by_role("button", name="Uygula", exact=True).click()
    sync_api.expect(page.locator("#tbl-stress tbody tr").first).to_be_attached()
    _open(page, "Risk kaynakları")
    sync_api.expect(page.locator("#tbl-risk-classes tbody tr").first).to_be_attached()
    sync_api.expect(page.locator("#risk-factors")).to_contain_text("Beta")
    _open(page, "Vergi")
    sync_api.expect(page.locator("#tax-lots tbody tr").first).to_be_visible()
    sync_api.expect(page.locator("#tax-harvest")).to_contain_text("Toplam tahmini tasarruf")
    page.get_by_role("button", name="Simüle et", exact=True).click()
    sync_api.expect(page.locator("#tax-sim")).to_contain_text("HIFO")
    _open(page, "Model portföyler")
    sync_api.expect(page.locator("#tbl-models tbody tr")).to_have_count(13)
    sync_api.expect(page.locator("#tbl-models")).to_contain_text("sizin seviyeniz")
    sync_api.expect(page.locator("#tbl-model-curve tbody tr").first).to_be_attached()
    assert not page.console_errors, page.console_errors


def test_advisor_sees_staff_panel(page: Any) -> None:
    _login(page, "danisman", "Danisman!2345")
    _open(page, "Danışman paneli")
    sync_api.expect(page.locator("#queue")).not_to_be_empty()
    _open(page, "Analitik")
    page.locator("#bt-mode").select_option("walk_forward")
    sync_api.expect(page.locator("#bt-method")).to_be_visible()
    page.locator("#bt-years").fill("3")
    page.get_by_role("button", name="Çalıştır").click()
    sync_api.expect(page.locator("#backtest")).to_contain_text("Walk-forward", timeout=120_000)
    sync_api.expect(page.locator("#backtest-info")).to_contain_text("Test dönemi")
    assert not page.console_errors, page.console_errors

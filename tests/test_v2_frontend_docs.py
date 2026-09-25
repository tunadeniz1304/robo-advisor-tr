"""v2 audit findings D.18–D.19 (frontend) and E.22–E.25 (honest docs).

Static checks over tracked files; no network, no browser. The browser E2E
flow lives in ``tests/e2e`` (Playwright, skipped when not installed).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"


def _js_files() -> list[Path]:
    return sorted(p for p in FRONTEND.rglob("*.js") if "vendor" not in p.parts)


def test_frontend_is_split_into_modules() -> None:
    js = FRONTEND / "js"
    for name in ("api.js", "state.js", "charts.js", "format.js"):
        assert (js / name).is_file(), name
    views = list((js / "views").glob("*.js"))
    assert len(views) >= 4
    assert not (FRONTEND / "app.js").exists() or (FRONTEND / "app.js").stat().st_size < 4_000


def test_frontend_lines_are_readable() -> None:
    offenders: list[str] = []
    for path in _js_files():
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if len(line) > 100:
                offenders.append(f"{path.name}:{no} uzun satır")
            code = re.sub(r"(['\"`]).*?\1", "", line.split("//")[0])
            if re.search(r";\s*\S", code) and not code.strip().startswith("for"):
                offenders.append(f"{path.name}:{no} birden fazla ifade")
    assert not offenders, offenders[:10]


class _A11y(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.label_depth = 0
        self.label_for: set[str] = set()
        self.controls: list[dict[str, str | None]] = []
        self.canvases: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "label":
            self.label_depth += 1
            if a.get("for"):
                self.label_for.add(str(a["for"]))
        if tag in {"input", "select", "textarea"} and a.get("type") != "hidden":
            self.controls.append({**a, "_wrapped": "1" if self.label_depth else None})
        if tag == "canvas":
            self.canvases.append(a)

    def handle_endtag(self, tag: str) -> None:
        if tag == "label":
            self.label_depth = max(0, self.label_depth - 1)


def test_every_form_control_has_a_label_and_charts_have_tables() -> None:
    parser = _A11y()
    parser.feed((FRONTEND / "index.html").read_text(encoding="utf-8"))
    unlabeled = [
        c
        for c in parser.controls
        if not (
            c["_wrapped"]
            or c.get("aria-label")
            or c.get("aria-labelledby")
            or c.get("id") in parser.label_for
        )
    ]
    assert not unlabeled, unlabeled
    for canvas in parser.canvases:
        assert canvas.get("role") == "img" and canvas.get("aria-label"), canvas
        assert canvas.get("data-table"), f"{canvas.get('id')}: tablo alternatifi yok"


def test_focus_ring_and_skip_link() -> None:
    css = (FRONTEND / "app.css").read_text(encoding="utf-8")
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert 'class="skip-link"' in html


def _tracked_files() -> list[Path]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("git deposu yok")
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True, encoding="utf-8"
    ).stdout
    return [ROOT / line for line in out.splitlines() if line]


INTERNAL_HOST_MARKER = "ss" + "yz"  # iç LLM sunucusunun alan adı parçası (dosyada düz yazılmaz)


def test_internal_llm_host_is_not_in_tracked_files() -> None:
    hits = []
    for path in _tracked_files():
        if path.suffix in {".gz", ".png", ".jpg", ".ico", ".parquet"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if INTERNAL_HOST_MARKER in text:
            hits.append(str(path.relative_to(ROOT)))
    assert not hits, hits


def test_readme_is_honest() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Sınırlamalar" in readme
    assert "## İlham alınan desenler" in readme
    lowered = readme.lower()
    for claim in ("kurumsal seviye", "production-ready", "üretime hazır", "enterprise-grade"):
        assert claim not in lowered, claim
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert "kurumsal seviye" not in pyproject


def test_data_doc_lists_sources_and_licences() -> None:
    doc = (ROOT / "docs" / "DATA.md").read_text(encoding="utf-8")
    for needle in ("TEFAS", "Yahoo", "TÜİK", "TCMB", "Lisans", "is_proxy"):
        assert needle in doc, needle


REG_PATTERN = re.compile(
    r"(Kanun|Tebliğ|Yönetmelik|sayılı|SPK|KVKK|MASAK|III-\d|GIPS|MiFID|ISO\s?\d+)", re.IGNORECASE
)


def _blocks(text: str) -> list[str]:
    """Paragraphs and list items (a wrapped bullet is one block)."""
    blocks: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        new_block = not stripped or stripped.startswith(("* ", "- ", "#", "|")) or not blocks
        if new_block:
            blocks.append([])
        if stripped:
            blocks[-1].append(stripped)
    return [" ".join(b) for b in blocks if b]


@pytest.mark.parametrize("name", ["COMPLIANCE.md", "METHODOLOGY.md"])
def test_regulatory_references_are_sourced_or_flagged(name: str) -> None:
    text = (ROOT / "docs" / name).read_text(encoding="utf-8")
    unsourced = [
        block
        for block in _blocks(text)
        if REG_PATTERN.search(block) and "http" not in block and "doğrulanmadı" not in block.lower()
    ]
    assert not unsourced, unsourced[:5]

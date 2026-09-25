"""v2 §2.28: walk-forward tracking of the model portfolios."""

from __future__ import annotations

from fastapi.testclient import TestClient

from core.app import create_app
from tests.conftest import FakeMarketSource, auth_headers, login


def test_model_portfolio_performance_endpoint(settings) -> None:  # noqa: ANN001
    app = create_app(settings=settings, market_source=FakeMarketSource(n_days=1_200))
    with TestClient(app) as client:
        client.headers.update(auth_headers(login(client)))
        body = client.get("/api/v1/model-portfolios/performance", params={"levels": "2,8"}).json()
        bad = client.get("/api/v1/model-portfolios/performance", params={"levels": "0,11"})
    assert bad.status_code == 422
    assert body["source"] == "computed" and set(body["levels"]) == {"2", "8"}
    low, high = body["levels"]["2"], body["levels"]["8"]
    assert low["test_start"] == high["test_start"]  # aynı test dönemi
    # (Sahte rastgele fiyatlarda oynaklık sıralaması anlamsız; gerçek veri testi aşağıda.)
    assert low["walk_forward"]["rebalances"] >= 1 and high["static_model"]["cagr"] is not None
    assert {"XU100", "TUFE+3", "60/40"} <= set(body["benchmarks"])


def test_shipped_model_performance_matches_snapshot() -> None:
    import json

    from services.market_data.sources import SNAPSHOT_DIR, SnapshotSource

    data = json.loads((SNAPSHOT_DIR / "model_performance.json").read_text(encoding="utf-8"))
    assert data["snapshot_end"] == SnapshotSource().meta()["end"]
    vols = [data["levels"][str(lv)]["walk_forward"]["volatility"] for lv in range(1, 11)]
    assert vols == sorted(vols)  # risk seviyesiyle monoton artan oynaklık

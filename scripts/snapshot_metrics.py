"""Bir snapshot dizini için anahtar sayıları üretir (önce / sonra karşılaştırması).

Kullanım::

    python scripts/snapshot_metrics.py                       # data/snapshots
    python scripts/snapshot_metrics.py --dir eski_snapshot --json out.json

Üretilen sayılar: seviye 5 model portföy backtest'i (bant politikası, 5 yıl),
seviye 5 HRP walk-forward, güncel HRP optimizasyonu, hedef Monte Carlo'su,
stres senaryoları, rejim, risksiz faiz ve yıllık TÜFE. ``docs/FINAL_REPORT_v2.md``
içindeki "önce / sonra" tablosu bu betikle üretilmiştir.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # TextIO lacks it; real streams have it
    except AttributeError:  # pragma: no cover
        pass

import structlog  # noqa: E402

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(40))

from core.policy import get_policy  # noqa: E402
from services.analytics.backtest import run_backtest  # noqa: E402
from services.analytics.walkforward import walk_forward  # noqa: E402
from services.market_data.service import MarketDataService  # noqa: E402
from services.market_data.sources import SNAPSHOT_DIR, SnapshotSource  # noqa: E402
from services.market_data.universe import CLASS_REPRESENTATIVE  # noqa: E402
from services.optimization.service import OptimizationRequest, OptimizationService  # noqa: E402
from services.planning.service import GoalPlanningService  # noqa: E402
from services.regime import detect_regime  # noqa: E402
from services.stress import run_stress  # noqa: E402

LEVEL = 5
YEARS = 5
PORTFOLIO_TL = 1_000_000.0


def _r(x: Any, n: int = 4) -> Any:
    return round(float(x), n) if isinstance(x, int | float) and x is not None else x


async def collect(directory: Path) -> dict[str, Any]:
    policy = get_policy()
    snap = SnapshotSource(directory)
    market = MarketDataService(snap, snapshot=snap)
    out: dict[str, Any] = {
        "snapshot": snap.meta().get("end"),
        "macro": snap.meta().get("macro_source"),
    }
    out["risk_free_rate"] = _r(market.risk_free_rate())
    out["inflation_yoy"] = _r(market.inflation_yoy())

    weights = {CLASS_REPRESENTATIVE[c]: w for c, w in policy.model_weights(LEVEL).items()}
    prices = (await market.history(sorted(weights))).tail(YEARS * 252)
    rf = market.mean_risk_free_rate(prices.index[0], prices.index[-1])
    bt = run_backtest(prices, weights, policy_name="band", risk_free_rate=rf)
    s = bt["stats"]
    out["backtest_model_l5_band_5y"] = {
        "cagr": _r(s["cagr"]),
        "volatility": _r(s["volatility"]),
        "sharpe": _r(s["sharpe"], 3),
        "max_drawdown": _r(s["max_drawdown"]),
        "rebalances": bt["rebalances"],
    }

    opt = OptimizationService(market)
    req = OptimizationRequest(level=LEVEL, method="hrp")
    res = await opt.optimize(req)
    out["optimizer_hrp_l5"] = {
        "expected_return": _r(res.expected_return),
        "volatility": _r(res.volatility),
        "cvar_95_daily": _r(res.cvar_95),
        "para_piyasasi": _r(res.class_weights.get("para_piyasasi", 0.0)),
        "binding": [b["name"] for b in res.params["binding_constraints"]],
    }

    symbols = opt.allowed_symbols(req)
    window = int(policy.backtest["window_days"])
    panel = (await market.history(symbols)).tail(YEARS * 252 + window + 1)
    full = [c for c in symbols if c in panel.columns and panel[c].notna().all()]
    bench = await market.history(
        [policy.backtest["benchmark_equity"], policy.backtest["benchmark_bond"]]
    )
    wf = walk_forward(
        panel[full],
        lambda train, as_of: (
            opt.optimize_on_returns(req, train, rf=market.risk_free_rate_at(as_of)).weights
        ),
        risk_free_rate=market.mean_risk_free_rate(panel.index[window], panel.index[-1]),
        benchmark_prices=bench,
        cpi=market.macro().get("TUFE"),
    )
    out["walk_forward_hrp_l5"] = {
        "test": f"{wf['test_start']} → {wf['test_end']}",
        "cagr": _r(wf["stats"]["cagr"]),
        "volatility": _r(wf["stats"]["volatility"]),
        "sharpe": _r(wf["stats"]["sharpe"], 3),
        "max_drawdown": _r(wf["stats"]["max_drawdown"]),
        "benchmarks_cagr": {k: _r(v["stats"]["cagr"]) for k, v in wf["benchmarks"].items()},
    }

    planner = GoalPlanningService(market)
    spec = planner.spec(initial=100_000, monthly=5_000, target=1_000_000, years=10)
    mc = await planner.run(spec, LEVEL)
    out["monte_carlo_goal_l5"] = {
        "success_probability": _r(mc.get("success_probability")),
        "p50_real": _r(mc["p50_real"], 0),
        "required_monthly": _r(mc["required_monthly_contribution"], 0),
    }

    values = {s: PORTFOLIO_TL * w for s, w in weights.items()}
    stress = {}
    for name in ("usdtry_up_30", "bist_down_25", "covid_2020", "kur_soku_2021"):
        try:
            r = await run_stress(market, values, 0.0, scenario=name)
            stress[name] = _r(r["pnl_orani"])
        except ValueError as exc:
            stress[name] = f"hata: {exc}"
    out["stress_l5_pct"] = stress

    regime = await detect_regime(market)
    out["regime"] = {"label": regime["label"], "probabilities": regime.get("probabilities")}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=str(SNAPSHOT_DIR))
    parser.add_argument("--json", default=None)
    args = parser.parse_args()
    result = asyncio.run(collect(Path(args.dir)))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

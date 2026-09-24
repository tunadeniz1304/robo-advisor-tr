"""Optimisation service: from a risk level to explainable target weights.

Pipeline for :meth:`OptimizationService.optimize`:

    1. **Universe** — active instruments allowed by the suitability gate for
       the level (instrument risk ≤ limit), minus exclusions (ESG/personal)
       and classes the model portfolio does not hold.
    2. **Estimation** — ≥3 years of daily TL returns; Ledoit-Wolf covariance;
       James-Stein (or CAPM-prior) shrunk expected returns; the real TL
       risk-free rate from the macro series.
    3. **Constraints** — long-only; asset-class sums within the model
       portfolio weight ± band; single risky instrument ≤ policy maximum.
    4. **Strategy** — ``hrp`` | ``black_litterman`` | ``min_cvar`` |
       ``risk_parity`` | ``mean_variance``.
    5. **Explainability** — risk contributions ``w_i (Σw)_i / σ²``, class
       weights vs model, expected return/volatility/Sharpe/CVaR and the BL
       prior vs posterior per instrument.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from core.logging import get_logger
from core.metrics import OPTIMIZER_SECONDS
from core.policy import InvestmentPolicy, get_policy
from services.market_data.service import MarketDataService
from services.market_data.universe import BENCHMARK_SYMBOL, BY_SYMBOL, UNIVERSE
from services.optimization import strategies as st
from services.optimization.estimators import expected_returns, ledoit_wolf
from services.suitability.scoring import GateResult, allowed_instrument_risk, check_allocation

logger = get_logger("otonom.optimization")

METHODS: dict[str, str] = {
    "hrp": "Hiyerarşik Risk Paritesi (HRP)",
    "black_litterman": "Black-Litterman (ev görüşleriyle)",
    "min_cvar": "Minimum CVaR (%95)",
    "risk_parity": "Risk Paritesi (eşit risk katkısı)",
    "mean_variance": "Kısıtlı Ortalama-Varyans (maks. Sharpe)",
}
TRADING_DAYS = 252
RISKY_THRESHOLD = 5  # risk skoru ≥5 olan tekil enstrümanlara tavan uygulanır


@dataclass(frozen=True)
class View:
    """An absolute Black-Litterman view on one instrument."""

    symbol: str
    expected_return: float
    confidence: float
    rationale: str = ""


@dataclass
class OptimizationRequest:
    level: int
    method: str | None = None
    model_level: int | None = None
    views: list[View] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    esg_only: bool = False
    override_ack: bool = False
    regime_tilt: float = 0.0  # + riskli sınıflara, − güvenli sınıflara (P1.2)


@dataclass
class OptimizationResult:
    method: str
    method_label: str
    level: int
    model_level: int
    weights: dict[str, float]
    class_weights: dict[str, float]
    model_class_weights: dict[str, float]
    risk_contributions: dict[str, float]
    class_risk_contributions: dict[str, float]
    expected_return: float
    volatility: float
    sharpe: float
    cvar_95: float
    risk_free_rate: float
    universe: list[str]
    excluded: list[str]
    params: dict[str, Any]
    gate: GateResult
    bl: dict[str, Any] | None = None
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        r = lambda d: {k: round(float(v), 6) for k, v in d.items()}  # noqa: E731
        return {
            "method": self.method,
            "method_label": self.method_label,
            "level": self.level,
            "model_level": self.model_level,
            "weights": r(self.weights),
            "class_weights": r(self.class_weights),
            "model_class_weights": r(self.model_class_weights),
            "risk_contributions": r(self.risk_contributions),
            "class_risk_contributions": r(self.class_risk_contributions),
            "expected_return": round(self.expected_return, 6),
            "volatility": round(self.volatility, 6),
            "sharpe": round(self.sharpe, 4),
            "cvar_95": round(self.cvar_95, 6),
            "risk_free_rate": round(self.risk_free_rate, 6),
            "universe": self.universe,
            "excluded": self.excluded,
            "params": self.params,
            "gate": self.gate.to_dict(),
            "bl": self.bl,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


class OptimizationService:
    """Builds explainable target allocations for a risk level."""

    def __init__(self, market: MarketDataService, policy: InvestmentPolicy | None = None) -> None:
        self._market = market
        self._policy = policy or get_policy()

    # -- universe & data ---------------------------------------------------------

    def universe(
        self, level: int, model_weights: dict[str, float], exclude: list[str], esg_only: bool
    ) -> tuple[list[str], list[str]]:
        """Allowed instruments for the level (and the excluded list)."""
        max_risk = allowed_instrument_risk(level, self._policy)
        chosen: list[str] = []
        excluded: list[str] = []
        for spec in UNIVERSE:
            if model_weights.get(spec.asset_class, 0.0) <= 0:
                continue
            if spec.risk_score > max_risk:
                excluded.append(spec.symbol)
                continue
            if spec.symbol in exclude:
                excluded.append(spec.symbol)
                continue
            if esg_only and spec.asset_class == "bist_hisse" and not spec.esg_member:
                excluded.append(spec.symbol)
                continue
            chosen.append(spec.symbol)
        return chosen, excluded

    async def estimation_window(self, symbols: list[str]) -> pd.DataFrame:
        """Daily returns over the last ≤5 years (≥ ``min_history_years``)."""
        years = int(self._policy.optimization.get("min_history_years", 3))
        rets = await self._market.returns(symbols)
        rets = rets.tail(TRADING_DAYS * max(years, 5))
        min_obs = int(TRADING_DAYS * years * 0.9)
        keep = [c for c in rets.columns if rets[c].notna().sum() >= min(min_obs, len(rets))]
        return rets[keep].dropna()

    # -- constraints -------------------------------------------------------------------

    def constraints(
        self, symbols: list[str], model_weights: dict[str, float], tilt: float = 0.0
    ) -> st.Constraints:
        band = float(self._policy.optimization.get("class_band", 0.10))
        max_single = float(self._policy.optimization.get("max_single_instrument", 0.30))
        classes = {BY_SYMBOL[s].asset_class for s in symbols}
        targets = {c: model_weights.get(c, 0.0) for c in classes}
        if tilt:
            targets = apply_tilt(targets, tilt)
        total = sum(targets.values()) or 1.0
        targets = {c: w / total for c, w in targets.items()}
        groups = {
            c: [i for i, s in enumerate(symbols) if BY_SYMBOL[s].asset_class == c] for c in classes
        }
        upper = np.array(
            [max_single if BY_SYMBOL[s].risk_score >= RISKY_THRESHOLD else 1.0 for s in symbols]
        )
        # Tavan, sınıfın alt sınırını imkânsız kılmasın.
        for c, idx in groups.items():
            lo_c = max(0.0, targets[c] - band)
            cap_sum = float(np.sum(upper[idx]))
            if cap_sum < lo_c:
                upper[idx] = np.maximum(upper[idx], lo_c / len(idx) + 1e-6)
        return st.Constraints(
            lower=np.zeros(len(symbols)),
            upper=upper,
            groups=groups,
            group_lower={c: max(0.0, targets[c] - band) for c in classes},
            group_upper={c: min(1.0, targets[c] + band) for c in classes},
        )

    @staticmethod
    def strategic_weights(symbols: list[str], model_weights: dict[str, float]) -> np.ndarray:
        """Model class weights spread equally over each class's instruments."""
        counts: dict[str, int] = {}
        for s in symbols:
            counts[BY_SYMBOL[s].asset_class] = counts.get(BY_SYMBOL[s].asset_class, 0) + 1
        w = np.array(
            [
                model_weights.get(BY_SYMBOL[s].asset_class, 0.0) / counts[BY_SYMBOL[s].asset_class]
                for s in symbols
            ]
        )
        return w / w.sum() if w.sum() > 0 else np.full(len(symbols), 1.0 / len(symbols))

    # -- main entry point ------------------------------------------------------------------

    def _prepare(
        self, req: OptimizationRequest
    ) -> tuple[str, int, int, dict[str, float], list[str], list[str]]:
        """Method, levels, model weights, allowed universe and exclusions."""
        policy = self._policy
        method = (req.method or policy.optimization.get("default_method", "hrp")).lower()
        if method not in METHODS:
            raise st.OptimizationError(f"Bilinmeyen optimizasyon yöntemi: {method}")
        level = max(1, min(int(req.level), 10))
        model_level = max(1, min(int(req.model_level or level), 10))
        model_weights = policy.model_weights(model_level)
        # Model seviyesi müşteriden yüksekse enstrüman kapısı model seviyesine göre açılır
        # (yalnızca açık onayla ilerleyebilir — kapı aşağıda kontrol edilir).
        gate_level = max(level, model_level) if req.override_ack else level
        symbols, excluded = self.universe(gate_level, model_weights, req.exclude, req.esg_only)
        if not symbols:
            raise st.OptimizationError("Uygun enstrüman bulunamadı.")
        return method, level, model_level, model_weights, symbols, excluded

    async def optimize(self, req: OptimizationRequest) -> OptimizationResult:
        """Optimise on the latest estimation window of the shared market data."""
        _, _, _, _, symbols, _ = self._prepare(req)
        rets = await self.estimation_window(symbols)
        market_series = None
        if self._policy.optimization.get("return_shrinkage") == "capm":
            bench = await self._market.returns([BENCHMARK_SYMBOL])
            market_series = bench[BENCHMARK_SYMBOL]
        return self.optimize_on_returns(
            req, rets, rf=self._market.risk_free_rate(), market_series=market_series
        )

    def optimize_on_returns(
        self,
        req: OptimizationRequest,
        returns: pd.DataFrame,
        *,
        rf: float,
        market_series: pd.Series | None = None,
    ) -> OptimizationResult:
        """Optimise on a given return window (no data access; used by walk-forward).

        Only the rows of ``returns`` are used, so a caller that passes data up
        to a date *t* gets weights that depend on nothing after *t*.
        """
        started = time.perf_counter()
        policy = self._policy
        method, level, model_level, model_weights, symbols, excluded = self._prepare(req)
        symbols = [s for s in symbols if s in returns.columns]
        rets = returns[symbols].dropna()
        if rets.shape[0] < 60 or not symbols:
            raise st.OptimizationError("Optimizasyon için yeterli geçmiş veri yok.")
        cov, shrink = ledoit_wolf(rets)
        mu = expected_returns(
            rets,
            method=str(policy.optimization.get("return_shrinkage", "james_stein")),
            rf=rf,
            market=market_series,
            cov=cov,
        )
        cons = self.constraints(symbols, model_weights, req.regime_tilt)
        alpha = float(policy.optimization.get("cvar_alpha", 0.95))
        scenarios = rets.tail(TRADING_DAYS * 3).to_numpy(dtype=float)

        bl_payload: dict[str, Any] | None = None
        mu_used = mu
        if method == "hrp":
            w = st.hrp(cov, cons)
        elif method == "risk_parity":
            w = st.risk_parity(cov, cons)
        elif method == "min_cvar":
            w = st.min_cvar(scenarios, cons, alpha)
        elif method == "mean_variance":
            w = st.mean_variance(mu, cov, rf, cons)
        else:
            w, mu_used, bl_payload = self._black_litterman(
                symbols, cov, model_weights, req.views, rf, cons
            )

        w = st._clean(w)
        rc = st.risk_contributions(w, cov)
        vol = st.portfolio_volatility(w, cov)
        exp_ret = float(w @ mu_used)
        weights = {s: float(x) for s, x in zip(symbols, w, strict=True) if x > 1e-6}
        class_w: dict[str, float] = {}
        class_rc: dict[str, float] = {}
        for s, x, r in zip(symbols, w, rc, strict=True):
            c = BY_SYMBOL[s].asset_class
            class_w[c] = class_w.get(c, 0.0) + float(x)
            class_rc[c] = class_rc.get(c, 0.0) + float(r)
        gate = check_allocation(
            level, weights, model_level=model_level, override_ack=req.override_ack, policy=policy
        )
        elapsed = (time.perf_counter() - started) * 1000.0
        OPTIMIZER_SECONDS.labels(method=method).observe(elapsed / 1000.0)
        logger.info(
            "optimization_done", method=method, level=level, n=len(symbols), ms=round(elapsed, 1)
        )
        return OptimizationResult(
            method=method,
            method_label=METHODS[method],
            level=level,
            model_level=model_level,
            weights=weights,
            class_weights=class_w,
            model_class_weights=dict(model_weights),
            risk_contributions={
                s: float(r) for s, r in zip(symbols, rc, strict=True) if w[symbols.index(s)] > 1e-6
            },
            class_risk_contributions=class_rc,
            expected_return=exp_ret,
            volatility=vol,
            sharpe=(exp_ret - rf) / vol if vol > 1e-9 else 0.0,
            cvar_95=st.historical_cvar(scenarios, w, alpha) if scenarios.size else 0.0,
            risk_free_rate=rf,
            universe=symbols,
            excluded=excluded,
            params={
                "covariance": "ledoit_wolf",
                "shrinkage_intensity": round(shrink, 4),
                "return_estimator": policy.optimization.get("return_shrinkage", "james_stein"),
                "observations": int(rets.shape[0]),
                "history_years": round(rets.shape[0] / TRADING_DAYS, 2),
                "window_end": str(rets.index[-1].date()) if len(rets.index) else None,
                "class_band": policy.optimization.get("class_band"),
                "class_bounds": {
                    c: [
                        round(cons.group_lower.get(c, 0.0), 6),
                        round(cons.group_upper.get(c, 1.0), 6),
                    ]
                    for c in cons.groups
                },
                "binding_constraints": binding_constraints(cons, w, symbols),
                "max_single_instrument": policy.optimization.get("max_single_instrument"),
                "cvar_alpha": alpha,
                "regime_tilt": req.regime_tilt,
                "cvar_horizon": "günlük",
            },
            gate=gate,
            bl=bl_payload,
            elapsed_ms=elapsed,
        )

    def _black_litterman(
        self,
        symbols: list[str],
        cov: np.ndarray,
        model_weights: dict[str, float],
        views: list[View],
        rf: float,
        cons: st.Constraints,
    ) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
        w_mkt = self.strategic_weights(symbols, model_weights)
        usable = [v for v in views if v.symbol in symbols]
        p = np.zeros((len(usable), len(symbols)))
        for k, v in enumerate(usable):
            p[k, symbols.index(v.symbol)] = 1.0
        q = np.array([v.expected_return for v in usable])
        conf = np.array([v.confidence for v in usable])
        delta = float(self._policy.optimization.get("bl_risk_aversion", 2.5))
        tau = float(self._policy.optimization.get("bl_tau", 0.05))
        omega = (
            st.idzorek_omega(cov, w_mkt, p, q, conf, tau=tau, risk_aversion=delta, rf=rf)
            if usable
            else np.zeros((0, 0))
        )
        res = st.black_litterman_posterior(
            cov,
            w_mkt,
            p,
            q,
            conf,
            risk_aversion=delta,
            tau=tau,
            rf=rf,
            omega=omega if usable else None,
        )
        w = st.mean_variance(res.posterior, res.posterior_cov, rf, cons)
        w_prior = st.mean_variance(res.prior, cov, rf, cons)
        payload = {
            "views": [
                {
                    "symbol": v.symbol,
                    "expected_return": v.expected_return,
                    "confidence": v.confidence,
                    "rationale": v.rationale,
                }
                for v in usable
            ],
            "ignored_views": [v.symbol for v in views if v.symbol not in symbols],
            "omega_method": "idzorek_2005_numeric",
            "omega": [round(float(x), 8) for x in np.diag(res.omega)],
            "prior_returns": {
                s: round(float(x), 6) for s, x in zip(symbols, res.prior, strict=True)
            },
            "posterior_returns": {
                s: round(float(x), 6) for s, x in zip(symbols, res.posterior, strict=True)
            },
            "prior_weights": {
                s: round(float(x), 6) for s, x in zip(symbols, w_prior, strict=True) if x > 1e-6
            },
        }
        return w, res.posterior, payload

    async def model_portfolio_stats(self, level: int) -> dict[str, float]:
        """Expected return/volatility of a model portfolio (class representatives)."""
        from services.market_data.universe import CLASS_REPRESENTATIVE

        weights = self._policy.model_weights(level)
        symbols = [CLASS_REPRESENTATIVE[c] for c in weights]
        rets = await self.estimation_window(symbols)
        cols = [s for s in symbols if s in rets.columns]
        if not cols:
            return {"expected_return": 0.0, "volatility": 0.0}
        cov, _ = ledoit_wolf(rets[cols])
        mu = expected_returns(rets[cols], cov=cov)
        w = np.array([weights[BY_SYMBOL[s].asset_class] for s in cols])
        w = w / w.sum()
        return {"expected_return": float(w @ mu), "volatility": st.portfolio_volatility(w, cov)}


def binding_constraints(
    cons: st.Constraints, w: np.ndarray, symbols: list[str], tol: float = 1e-4
) -> list[dict[str, Any]]:
    """Class bounds and instrument caps that are active at the optimum.

    Methods that minimise risk (HRP, min-CVaR, min-variance) push the lowest-risk
    class to its upper bound; reporting it makes clear which part of the
    allocation comes from the policy bands and which from the method.
    """
    out: list[dict[str, Any]] = []
    for name, idx in cons.groups.items():
        total = float(np.sum(w[list(idx)]))
        if name in cons.group_upper and total >= cons.group_upper[name] - tol:
            out.append({"type": "class_upper", "name": name, "bound": cons.group_upper[name]})
        elif name in cons.group_lower and total <= cons.group_lower[name] + tol:
            if cons.group_lower[name] > 0:
                out.append({"type": "class_lower", "name": name, "bound": cons.group_lower[name]})
    for i, s in enumerate(symbols):
        if cons.upper[i] < 1.0 and w[i] >= cons.upper[i] - tol:
            out.append({"type": "instrument_cap", "name": s, "bound": float(cons.upper[i])})
    return out


def apply_tilt(class_weights: dict[str, float], tilt: float) -> dict[str, float]:
    """Shift ``tilt`` of weight from defensive to risky classes (or back).

    Positive ``tilt`` (boğa) moves weight from money market/bonds into BIST
    classes; negative (stres) does the opposite. Weights stay ≥ 0 and the
    total is preserved.
    """
    risky = [c for c in ("bist_endeks", "bist_hisse") if class_weights.get(c, 0) > 0]
    safe = [c for c in ("para_piyasasi", "tl_tahvil") if class_weights.get(c, 0) > 0]
    if not risky or not safe or tilt == 0:
        return dict(class_weights)
    out = dict(class_weights)
    src, dst = (safe, risky) if tilt > 0 else (risky, safe)
    amount = min(abs(tilt), sum(out[c] for c in src))
    src_total = sum(out[c] for c in src)
    dst_total = sum(out[c] for c in dst)
    for c in src:
        out[c] -= amount * out[c] / src_total
    for c in dst:
        out[c] += amount * (out[c] / dst_total if dst_total > 0 else 1.0 / len(dst))
    return out


__all__ = [
    "METHODS",
    "OptimizationRequest",
    "OptimizationResult",
    "OptimizationService",
    "View",
    "apply_tilt",
    "binding_constraints",
]

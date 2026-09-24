"""Goal planning service: history for a risk level, simulations, what-ifs.

A goal is simulated with the model portfolio of its own risk level (Betterment
style bucket) or of the customer's effective level. The portfolio's monthly
TL return history comes from the asset-class representatives (so FX, gold and
eurobond shocks are included) and inflation from the TÜFE series.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from core.policy import InvestmentPolicy, get_policy
from llm.gateway import LLMGateway
from llm.prompts import DISCLAIMER
from services.market_data.service import MarketDataService
from services.market_data.universe import CLASS_REPRESENTATIVE
from services.planning.monte_carlo import SimulationSpec, simulate

GOAL_TYPES = {
    "emeklilik": "Emeklilik",
    "ev": "Ev alımı",
    "egitim": "Eğitim",
    "acil_durum": "Acil durum fonu",
    "diger": "Diğer",
}


class GoalPlanningService:
    """Monte Carlo goal planning."""

    def __init__(
        self,
        market: MarketDataService,
        gateway: LLMGateway | None = None,
        policy: InvestmentPolicy | None = None,
    ) -> None:
        self._market = market
        self._gateway = gateway
        self._p = policy or get_policy()
        self._cache: dict[int, np.ndarray] = {}

    async def history_for_level(self, level: int) -> np.ndarray:
        """``(T, 2)`` monthly ``[nominal portfolio return, inflation]``."""
        level = max(1, min(int(level), 10))
        if level in self._cache:
            return self._cache[level]
        weights = self._p.model_weights(level)
        symbols = [CLASS_REPRESENTATIVE[c] for c in weights]
        monthly = await self._market.returns(symbols, freq="M")
        w = pd.Series({CLASS_REPRESENTATIVE[c]: v for c, v in weights.items()})
        cols = [s for s in w.index if s in monthly.columns]
        port = (monthly[cols].fillna(0.0) * (w[cols] / w[cols].sum())).sum(axis=1)
        cpi = self._market.macro().get("TUFE")
        if cpi is not None:
            infl = cpi.dropna().sort_index().pct_change().reindex(port.index)
        else:
            infl = pd.Series(np.nan, index=port.index)
        default_m = (1.0 + float(self._p.planning.get("default_inflation", 0.25))) ** (1 / 12) - 1.0
        infl = infl.fillna(default_m)
        history = np.column_stack([port.to_numpy(), infl.to_numpy()])
        history = history[np.isfinite(history).all(axis=1)]
        self._cache[level] = history
        return history

    def spec(
        self,
        *,
        initial: float,
        monthly: float,
        target: float,
        years: float,
    ) -> SimulationSpec:
        return SimulationSpec(
            initial=float(initial),
            monthly_contribution=float(monthly),
            target_real=float(target),
            months=max(1, round(float(years) * 12)),
            n_paths=int(self._p.planning.get("n_paths", 10_000)),
            block_months=int(self._p.planning.get("block_months", 6)),
            seed=int(self._p.planning.get("seed", 42)),
            success_threshold=float(self._p.planning.get("success_threshold", 0.8)),
        )

    async def run(self, spec: SimulationSpec, level: int) -> dict[str, Any]:
        history = await self.history_for_level(level)
        result = simulate(history, spec).to_dict()
        result["risk_level"] = level
        return result

    async def simulate_goal(
        self,
        *,
        initial: float,
        monthly: float,
        target: float,
        years: float,
        level: int,
        what_ifs: bool = True,
    ) -> dict[str, Any]:
        """Base simulation plus the standard what-if scenarios."""
        base_spec = self.spec(initial=initial, monthly=monthly, target=target, years=years)
        base = await self.run(base_spec, level)
        if what_ifs:
            scenarios = [
                (
                    "vade_2_yil_uzat",
                    "Vadeyi 2 yıl uzatırsanız",
                    replace(base_spec, months=base_spec.months + 24),
                    level,
                ),
                (
                    "katki_yuzde_20_artir",
                    "Aylık katkıyı %20 artırırsanız",
                    replace(base_spec, monthly_contribution=base_spec.monthly_contribution * 1.2),
                    level,
                ),
            ]
            if level < 10:
                scenarios.append(
                    ("bir_seviye_riskli", "Bir seviye daha riskli portföyle", base_spec, level + 1)
                )
            if level > 1:
                scenarios.append(
                    (
                        "bir_seviye_temkinli",
                        "Bir seviye daha temkinli portföyle",
                        base_spec,
                        level - 1,
                    )
                )
            out = []
            for code, label, spec, lv in scenarios:
                r = await self.run(spec, lv)
                out.append(
                    {
                        "kod": code,
                        "aciklama": label,
                        "basari_olasiligi": r["success_probability"],
                        "p50_real": r["p50_real"],
                        "gerekli_aylik_katki": r["required_monthly_contribution"],
                    }
                )
            base["what_if"] = out
        return base

    async def what_if(
        self,
        *,
        initial: float,
        monthly: float,
        target: float,
        years: float,
        level: int,
        horizon_delta_years: float = 0.0,
        contribution_multiplier: float = 1.0,
        contribution_delta: float = 0.0,
        initial_delta: float = 0.0,
        level_delta: int = 0,
    ) -> dict[str, Any]:
        """Custom scenario vs base (slider driven)."""
        base = await self.run(
            self.spec(initial=initial, monthly=monthly, target=target, years=years), level
        )
        lv = max(1, min(10, level + int(level_delta)))
        scen = await self.run(
            self.spec(
                initial=max(0.0, initial + initial_delta),
                monthly=max(0.0, monthly * contribution_multiplier + contribution_delta),
                target=target,
                years=max(1 / 12, years + horizon_delta_years),
            ),
            lv,
        )
        return {
            "base": base,
            "scenario": scen,
            "delta_success_probability": round(
                scen["success_probability"] - base["success_probability"], 4
            ),
        }

    async def report(self, goal: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        """Grounded plain-language report (LLM or deterministic demo)."""
        if self._gateway is None:
            return {}
        gen: Any = await self._gateway.generate(
            "goal_report",
            {
                "hedef": {
                    "ad": goal.get("name"),
                    "tur": GOAL_TYPES.get(str(goal.get("goal_type")), goal.get("goal_type")),
                    "hedef_tutar": goal.get("target_amount_real"),
                    "vade_yil": goal.get("horizon_years"),
                },
                "basari_olasiligi": result["success_probability"],
                "p10": result["p10_real"],
                "p50": result["p50_real"],
                "p90": result["p90_real"],
                "gerekli_aylik_katki": result["required_monthly_contribution"],
                "aylik_katki": goal.get("monthly_contribution"),
                "what_if": result.get("what_if", []),
            },
        )
        return {
            "ozet": gen.data.ozet,
            "oneriler": gen.data.oneriler,
            "llm_mode": gen.mode,
            "disclaimer": DISCLAIMER,
        }


__all__ = ["GOAL_TYPES", "GoalPlanningService"]

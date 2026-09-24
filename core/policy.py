"""Investment policy loader (``config/policy.toml``).

Every business threshold — drift bands, costs, withholding tax table, model
portfolio weights, suitability gates, planning parameters — lives in the
policy file, not in code. :func:`get_policy` parses it once (cached) into a
typed, read-only :class:`InvestmentPolicy`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.config import BASE_DIR

DEFAULT_POLICY_FILE = BASE_DIR / "config" / "policy.toml"


class PolicyError(ValueError):
    """Raised when the policy file is inconsistent."""


@dataclass(frozen=True)
class InvestmentPolicy:
    """Typed view of the policy file (raw sections kept in ``raw``)."""

    version: str
    asset_classes: dict[str, str]
    risk_labels: list[str]
    profile_validity_days: int
    renewal_warning_days: int
    max_instrument_risk: dict[int, int]
    level_upper_bounds: list[float]
    model_portfolios: dict[int, dict[str, float]]
    optimization: dict[str, Any]
    rebalance: dict[str, Any]
    bands: dict[str, float]
    costs: dict[str, Any]
    tax: dict[str, Any]
    planning: dict[str, Any]
    autopilot: dict[str, Any]
    nudges: dict[str, Any]
    regime: dict[str, Any]
    stress: dict[str, Any]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    # -- helpers -------------------------------------------------------------

    def risk_label(self, level: int) -> str:
        """Human readable label of a 1–10 risk level."""
        idx = max(1, min(int(level), len(self.risk_labels))) - 1
        return self.risk_labels[idx]

    def band_for(self, asset_class: str) -> float:
        """Drift band of an asset class (falls back to the default band)."""
        return float(self.bands.get(asset_class, self.rebalance.get("default_band", 0.03)))

    def commission_bps(self, asset_class: str) -> float:
        return float(self.costs.get("commission_bps", {}).get(asset_class, 0.0))

    def spread_bps(self, asset_class: str) -> float:
        return float(self.costs.get("spread_bps", {}).get(asset_class, 0.0))

    def withholding_rate(self, asset_class: str, holding_days: int) -> float:
        """Withholding tax rate for a realised gain (informational)."""
        table = self.tax.get("withholding", {}).get(asset_class, {})
        long_after = int(self.tax.get("long_after_days", 365))
        key = "long" if holding_days >= long_after else "short"
        return float(table.get(key, 0.0))

    def model_weights(self, level: int) -> dict[str, float]:
        """Asset-class weights of the model portfolio for a risk level."""
        level = max(1, min(int(level), 10))
        return dict(self.model_portfolios[level])


def _parse(data: dict[str, Any]) -> InvestmentPolicy:
    models = {
        int(k): {c: float(w) for c, w in v.items()} for k, v in data["model_portfolios"].items()
    }
    for level, weights in models.items():
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-6:
            raise PolicyError(f"Model portföy {level} ağırlık toplamı 1 değil ({total:.4f}).")
        unknown = set(weights) - set(data["asset_classes"])
        if unknown:
            raise PolicyError(f"Model portföy {level} bilinmeyen sınıf içeriyor: {sorted(unknown)}")
    if sorted(models) != list(range(1, 11)):
        raise PolicyError("Model portföy kütüphanesi 1–10 seviyelerini eksiksiz içermeli.")
    risk = data["risk_levels"]
    suit = data["suitability"]
    return InvestmentPolicy(
        version=str(data.get("version", "")),
        asset_classes=dict(data["asset_classes"]),
        risk_labels=list(risk["labels"]),
        profile_validity_days=int(risk["profile_validity_days"]),
        renewal_warning_days=int(risk["renewal_warning_days"]),
        max_instrument_risk={int(k): int(v) for k, v in suit["max_instrument_risk"].items()},
        level_upper_bounds=[float(x) for x in suit["level_thresholds"]["upper_bounds"]],
        model_portfolios=models,
        optimization=dict(data["optimization"]),
        rebalance={k: v for k, v in data["rebalance"].items() if k != "bands"},
        bands={k: float(v) for k, v in data["rebalance"].get("bands", {}).items()},
        costs=dict(data["costs"]),
        tax=dict(data["tax"]),
        planning=dict(data["planning"]),
        autopilot=dict(data["autopilot"]),
        nudges=dict(data["nudges"]),
        regime=dict(data["regime"]),
        stress=dict(data["stress"]),
        raw=data,
    )


def load_policy(path: Path | str | None = None) -> InvestmentPolicy:
    """Parse a policy file (``POLICY_FILE`` env var or the default)."""
    target = Path(path or os.getenv("POLICY_FILE") or DEFAULT_POLICY_FILE)
    with target.open("rb") as fh:
        return _parse(tomllib.load(fh))


@lru_cache(maxsize=1)
def get_policy() -> InvestmentPolicy:
    """Process-wide cached policy."""
    return load_policy()


__all__ = ["InvestmentPolicy", "PolicyError", "get_policy", "load_policy"]

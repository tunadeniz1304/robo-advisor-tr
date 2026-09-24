"""Portfolio optimisation strategies (strategy pattern, NumPy/SciPy only).

All strategies share one constraint model (:class:`Constraints`): long-only
bounds per instrument and linear lower/upper bounds on asset-class sums
(model portfolio ± band). Every strategy is deterministic.

* :func:`hrp` — Hierarchical Risk Parity (López de Prado, 2016): correlation
  distance, single-linkage clustering, quasi-diagonalisation and recursive
  bisection; the result is projected onto the constraint set.
* :func:`risk_parity` — equal risk contribution (SLSQP).
* :func:`min_cvar` — Rockafellar–Uryasev CVaR minimisation as a linear
  programme (``scipy.optimize.linprog``/HiGHS).
* :func:`mean_variance` — maximum Sharpe ratio against a real risk-free rate.
* :func:`black_litterman_posterior` — BL posterior with Idzorek confidences.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.optimize import linprog, minimize
from scipy.spatial.distance import squareform

_EPS = 1e-12


class OptimizationError(ValueError):
    """Raised when no feasible allocation exists."""


@dataclass
class Constraints:
    """Linear constraint model shared by every strategy.

    Attributes:
        lower: Per-asset lower bounds.
        upper: Per-asset upper bounds.
        groups: ``{group_name: [asset indices]}`` (asset classes).
        group_lower: Lower bound of each group's total weight.
        group_upper: Upper bound of each group's total weight.
    """

    lower: np.ndarray
    upper: np.ndarray
    groups: dict[str, list[int]] = field(default_factory=dict)
    group_lower: dict[str, float] = field(default_factory=dict)
    group_upper: dict[str, float] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.lower)

    def bounds(self) -> list[tuple[float, float]]:
        return list(zip(self.lower.tolist(), self.upper.tolist(), strict=True))

    def scipy_constraints(self) -> list[dict[str, object]]:
        cons: list[dict[str, object]] = [{"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}]
        for name, idx in self.groups.items():
            ids = list(idx)
            lo = self.group_lower.get(name)
            hi = self.group_upper.get(name)
            if lo is not None:
                cons.append(
                    {"type": "ineq", "fun": lambda w, ids=ids, lo=lo: float(np.sum(w[ids]) - lo)}
                )
            if hi is not None:
                cons.append(
                    {"type": "ineq", "fun": lambda w, ids=ids, hi=hi: float(hi - np.sum(w[ids]))}
                )
        return cons

    def linear_rows(self) -> tuple[np.ndarray, np.ndarray]:
        """``A_ub w ≤ b_ub`` rows of the group constraints."""
        rows: list[np.ndarray] = []
        rhs: list[float] = []
        for name, idx in self.groups.items():
            mask = np.zeros(self.n)
            mask[list(idx)] = 1.0
            if name in self.group_upper:
                rows.append(mask.copy())
                rhs.append(self.group_upper[name])
            if name in self.group_lower:
                rows.append(-mask)
                rhs.append(-self.group_lower[name])
        if not rows:
            return np.zeros((0, self.n)), np.zeros(0)
        return np.vstack(rows), np.array(rhs)

    def feasible_start(self) -> np.ndarray:
        """A feasible starting point (LP with a flat objective)."""
        a_ub, b_ub = self.linear_rows()
        res = linprog(
            c=np.zeros(self.n),
            A_ub=a_ub if a_ub.size else None,
            b_ub=b_ub if b_ub.size else None,
            A_eq=np.ones((1, self.n)),
            b_eq=np.array([1.0]),
            bounds=self.bounds(),
            method="highs",
        )
        if not res.success:
            raise OptimizationError("Kısıtlar birlikte sağlanamıyor (uygun çözüm yok).")
        return np.asarray(res.x)

    def violation(self, w: np.ndarray) -> float:
        """Maximum constraint violation of ``w``."""
        worst = abs(float(np.sum(w)) - 1.0)
        worst = max(
            worst,
            float(np.max(self.lower - w, initial=0.0)),
            float(np.max(w - self.upper, initial=0.0)),
        )
        for name, idx in self.groups.items():
            s = float(np.sum(w[list(idx)]))
            if name in self.group_lower:
                worst = max(worst, self.group_lower[name] - s)
            if name in self.group_upper:
                worst = max(worst, s - self.group_upper[name])
        return worst


def unconstrained(n: int) -> Constraints:
    return Constraints(lower=np.zeros(n), upper=np.ones(n))


def _clean(w: np.ndarray) -> np.ndarray:
    w = np.where(np.abs(w) < 1e-7, 0.0, w)
    w = np.maximum(w, 0.0)
    s = float(np.sum(w))
    return w / s if s > _EPS else w


def _solve(objective, x0: np.ndarray, cons: Constraints, jac=None) -> np.ndarray:  # noqa: ANN001
    res = minimize(
        objective,
        x0,
        jac=jac,
        bounds=cons.bounds(),
        constraints=cons.scipy_constraints(),
        method="SLSQP",
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    w = _clean(np.asarray(res.x))
    if cons.violation(w) > 1e-5:
        # SLSQP başarısızsa uygun başlangıç noktasına dön (asla kısıt ihlali yok).
        w = _clean(x0)
    return w


def project(target: np.ndarray, cons: Constraints) -> np.ndarray:
    """Euclidean projection of ``target`` onto the constraint set."""
    if cons.violation(target) <= 1e-9:
        return target
    x0 = cons.feasible_start()
    return _solve(
        lambda w: float(np.sum((w - target) ** 2)),
        x0,
        cons,
        jac=lambda w: 2.0 * (w - target),
    )


# ------------------------------------------------------------------ risk helpers


def portfolio_volatility(w: np.ndarray, cov: np.ndarray) -> float:
    return float(np.sqrt(max(float(w @ cov @ w), 0.0)))


def risk_contributions(w: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Fractional risk contributions ``w_i (Σw)_i / (wᵀΣw)`` (sum to 1)."""
    var = float(w @ cov @ w)
    if var <= _EPS:
        return np.zeros_like(w)
    return w * (cov @ w) / var


# ------------------------------------------------------------------ strategies


def min_variance(cov: np.ndarray, cons: Constraints) -> np.ndarray:
    x0 = cons.feasible_start()
    return _solve(lambda w: float(w @ cov @ w), x0, cons, jac=lambda w: 2.0 * cov @ w)


def mean_variance(mu: np.ndarray, cov: np.ndarray, rf: float, cons: Constraints) -> np.ndarray:
    """Constrained maximum-Sharpe portfolio (min-variance if all μ ≤ rf)."""
    if np.all(mu <= rf):
        return min_variance(cov, cons)
    excess = mu - rf
    x0 = cons.feasible_start()

    def neg_sharpe(w: np.ndarray) -> float:
        return -float(w @ excess) / max(portfolio_volatility(w, cov), 1e-9)

    return _solve(neg_sharpe, x0, cons)


def risk_parity(
    cov: np.ndarray, cons: Constraints, budgets: np.ndarray | None = None
) -> np.ndarray:
    """Equal (or budgeted) risk contribution under the constraints."""
    n = cov.shape[0]
    b = budgets if budgets is not None else np.full(n, 1.0 / n)
    x0 = cons.feasible_start()

    def objective(w: np.ndarray) -> float:
        rc = risk_contributions(np.maximum(w, 1e-12), cov)
        return float(np.sum((rc - b) ** 2)) * 1e3

    return _solve(objective, x0, cons)


def _quasi_diag(corr: np.ndarray) -> list[int]:
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, 1.0))
    np.fill_diagonal(dist, 0.0)
    link = linkage(squareform(dist, checks=False), method="single")
    return [int(i) for i in leaves_list(link)]


def _ivp(cov: np.ndarray) -> np.ndarray:
    iv = 1.0 / np.maximum(np.diag(cov), _EPS)
    return iv / iv.sum()


def _cluster_var(cov: np.ndarray, items: list[int]) -> float:
    sub = cov[np.ix_(items, items)]
    w = _ivp(sub)
    return float(w @ sub @ w)


def hrp_weights(cov: np.ndarray) -> np.ndarray:
    """Unconstrained Hierarchical Risk Parity weights."""
    n = cov.shape[0]
    if n == 1:
        return np.array([1.0])
    std = np.sqrt(np.maximum(np.diag(cov), _EPS))
    corr = np.clip(cov / np.outer(std, std), -1.0, 1.0)
    order = _quasi_diag(corr)
    w = np.ones(n)
    clusters = [order]
    while clusters:
        nxt: list[list[int]] = []
        for c in clusters:
            if len(c) <= 1:
                continue
            half = len(c) // 2
            left, right = c[:half], c[half:]
            v_l, v_r = _cluster_var(cov, left), _cluster_var(cov, right)
            alpha = 1.0 - v_l / (v_l + v_r) if (v_l + v_r) > 0 else 0.5
            w[left] *= alpha
            w[right] *= 1.0 - alpha
            nxt.extend([left, right])
        clusters = nxt
    return w / w.sum()


def hrp(cov: np.ndarray, cons: Constraints) -> np.ndarray:
    """HRP weights projected onto the constraint set."""
    return project(hrp_weights(cov), cons)


def min_cvar(scenarios: np.ndarray, cons: Constraints, alpha: float = 0.95) -> np.ndarray:
    """Minimise CVaR_α of portfolio losses over historical scenarios (LP).

    Variables ``x = [w (n), z (T), v]``:
    ``min v + 1/((1−α)T) Σ z_t`` s.t. ``z_t ≥ −r_tᵀw − v``, ``z_t ≥ 0``.
    """
    t, n = scenarios.shape
    c = np.concatenate([np.zeros(n), np.full(t, 1.0 / ((1.0 - alpha) * t)), [1.0]])
    # −r_t w − v − z_t ≤ 0
    a_loss = np.hstack([-scenarios, -np.eye(t), -np.ones((t, 1))])
    b_loss = np.zeros(t)
    g_rows, g_rhs = cons.linear_rows()
    if g_rows.size:
        g_rows = np.hstack([g_rows, np.zeros((g_rows.shape[0], t + 1))])
        a_ub = np.vstack([a_loss, g_rows])
        b_ub = np.concatenate([b_loss, g_rhs])
    else:
        a_ub, b_ub = a_loss, b_loss
    a_eq = np.concatenate([np.ones(n), np.zeros(t + 1)]).reshape(1, -1)
    bounds = cons.bounds() + [(0.0, None)] * t + [(None, None)]
    res = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=[1.0], bounds=bounds, method="highs")
    if not res.success:
        raise OptimizationError("CVaR programı çözülemedi.")
    return _clean(np.asarray(res.x[:n]))


def historical_cvar(scenarios: np.ndarray, w: np.ndarray, alpha: float = 0.95) -> float:
    """Historical CVaR (expected loss beyond VaR) of a weight vector."""
    losses = -(scenarios @ w)
    var = float(np.quantile(losses, alpha))
    tail = losses[losses >= var]
    return float(tail.mean()) if tail.size else var


@dataclass
class BLResult:
    """Black-Litterman posterior."""

    prior: np.ndarray
    posterior: np.ndarray
    posterior_cov: np.ndarray
    omega: np.ndarray


def black_litterman_posterior(
    cov: np.ndarray,
    market_weights: np.ndarray,
    p: np.ndarray,
    q: np.ndarray,
    confidences: np.ndarray,
    *,
    risk_aversion: float = 2.5,
    tau: float = 0.05,
    rf: float = 0.0,
) -> BLResult:
    """Black-Litterman posterior returns with Idzorek-style confidences.

    ``π = rf + δ Σ w_mkt``; ``Ω_kk = (1−c_k)/c_k · τ p_k Σ p_kᵀ`` (Idzorek /
    Walters closed form: 100 % confidence → the view is fully expressed,
    0 % → ignored). Posterior:
    ``μ = [(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹ [(τΣ)⁻¹π + PᵀΩ⁻¹Q]``.

    Args:
        cov: Annualised covariance (n×n).
        market_weights: Equilibrium (strategic) weights (n).
        p: View pick matrix (k×n).
        q: View returns (k), annual, TL based.
        confidences: View confidences in (0, 1) (k).
        risk_aversion: δ.
        tau: Uncertainty scaling of the prior.
        rf: Risk-free rate added to the equilibrium excess returns.
    """
    prior = rf + risk_aversion * cov @ market_weights
    if p.size == 0:
        return BLResult(prior, prior.copy(), cov, np.zeros((0, 0)))
    c = np.clip(confidences, 1e-4, 1.0 - 1e-6)
    view_var = np.array([float(pk @ (tau * cov) @ pk) for pk in p])
    omega = np.diag((1.0 - c) / c * view_var)
    tau_cov_inv = np.linalg.inv(tau * cov)
    omega_inv = np.linalg.inv(omega)
    m = np.linalg.inv(tau_cov_inv + p.T @ omega_inv @ p)
    posterior = m @ (tau_cov_inv @ prior + p.T @ omega_inv @ q)
    return BLResult(prior=prior, posterior=posterior, posterior_cov=cov + m, omega=omega)


__all__ = [
    "BLResult",
    "Constraints",
    "OptimizationError",
    "black_litterman_posterior",
    "historical_cvar",
    "hrp",
    "hrp_weights",
    "mean_variance",
    "min_cvar",
    "min_variance",
    "portfolio_volatility",
    "project",
    "risk_contributions",
    "risk_parity",
    "unconstrained",
]

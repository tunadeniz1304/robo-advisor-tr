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
* :func:`idzorek_omega` — Idzorek (2005) view uncertainties from confidences,
  solved numerically per view.
* :func:`black_litterman_posterior` — BL posterior (He–Litterman form).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.optimize import brentq, linprog, minimize
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


def _single_view_posterior(
    cov: np.ndarray, prior: np.ndarray, pk: np.ndarray, qk: float, omega_k: float, tau: float
) -> np.ndarray:
    """Posterior mean with one view (Woodbury form, stable for ω → 0)."""
    tau_cov_p = tau * cov @ pk
    denom = float(pk @ tau_cov_p) + omega_k
    return np.asarray(prior + tau_cov_p * (qk - float(pk @ prior)) / denom)


def idzorek_omega(
    cov: np.ndarray,
    market_weights: np.ndarray,
    p: np.ndarray,
    q: np.ndarray,
    confidences: np.ndarray,
    *,
    tau: float = 0.05,
    risk_aversion: float = 2.5,
    rf: float = 0.0,
) -> np.ndarray:
    """Idzorek (2005) Ω: one 1-D root search per view.

    For view *k* alone, the 100 %-confidence posterior (ω_k = 0) gives the
    implied weights ``w_100 = (δΣ)⁻¹(μ_100 − r_f)``. Idzorek chooses ω_k so
    that the tilt of the implied weights equals the stated confidence share
    of that full tilt::

        (w(ω_k) − w_mkt) · (w_100 − w_mkt) / ‖w_100 − w_mkt‖² = c_k

    The ratio is strictly decreasing in ω_k, so ``brentq`` finds the unique
    root. With unconstrained implied weights the root coincides with the
    closed form ``τ(1−c_k)/c_k · p_kΣp_kᵀ`` that PyPortfolioOpt uses for
    ``omega="idzorek"`` (derivation in Walters, 2014); the tests check both.
    c_k = 0 means "ignore the view" (ω_k = 1e6 · p_kΣp_kᵀ).

    Args:
        cov: Annualised covariance (n×n).
        market_weights: Equilibrium weights (n).
        p: View pick matrix (k×n).
        q: View returns (k), annual total returns.
        confidences: Confidence per view in [0, 1] (k).
        tau: Prior uncertainty scale.
        risk_aversion: δ.
        rf: Risk-free rate (views and prior are total returns).

    Returns:
        Diagonal Ω (k×k).
    """
    prior = rf + risk_aversion * cov @ market_weights
    omegas = [
        _idzorek_view_omega(
            cov, market_weights, prior, pk, float(qk), float(ck), tau, risk_aversion, rf
        )
        for pk, qk, ck in zip(
            np.atleast_2d(p), np.atleast_1d(q), np.atleast_1d(confidences), strict=True
        )
    ]
    return np.diag(omegas)


def _idzorek_view_omega(
    cov: np.ndarray,
    market_weights: np.ndarray,
    prior: np.ndarray,
    pk: np.ndarray,
    qk: float,
    confidence: float,
    tau: float,
    risk_aversion: float,
    rf: float,
) -> float:
    """ω_k of one view (see :func:`idzorek_omega`)."""
    view_var = float(pk @ cov @ pk)
    c = float(np.clip(confidence, 0.0, 1.0))
    if c <= 1e-9:
        return 1e6 * view_var
    c = min(c, 1.0 - 1e-9)
    delta_cov = risk_aversion * cov
    w_100 = np.linalg.solve(delta_cov, _single_view_posterior(cov, prior, pk, qk, 0.0, tau) - rf)
    full_tilt = w_100 - market_weights
    norm = float(full_tilt @ full_tilt)
    if norm <= _EPS:  # görüş dengeyle aynı: güven ağırlıkları değiştirmez
        return tau * view_var * (1.0 - c) / c

    def gap(omega_k: float) -> float:
        mu = _single_view_posterior(cov, prior, pk, qk, omega_k, tau)
        tilt = np.linalg.solve(delta_cov, mu - rf) - market_weights
        return float(tilt @ full_tilt) / norm - c

    hi = tau * view_var
    while gap(hi) > 0:
        hi *= 10.0
    return float(brentq(gap, 0.0, hi, xtol=1e-14, rtol=1e-12, maxiter=500))


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
    omega: np.ndarray | None = None,
) -> BLResult:
    """Black-Litterman posterior returns; Ω defaults to Idzorek's method.

    ``π = rf + δ Σ w_mkt``. Ω comes from :func:`idzorek_omega` (numerical,
    per view) unless given explicitly. Posterior:
    ``μ = [(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹ [(τΣ)⁻¹π + PᵀΩ⁻¹Q]``,
    posterior covariance ``Σ + [(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹``.

    Args:
        cov: Annualised covariance (n×n).
        market_weights: Equilibrium (strategic) weights (n).
        p: View pick matrix (k×n).
        q: View returns (k), annual, TL based.
        confidences: View confidences in [0, 1] (k).
        risk_aversion: δ.
        tau: Uncertainty scaling of the prior.
        rf: Risk-free rate added to the equilibrium excess returns.
        omega: Optional explicit Ω (k×k).
    """
    prior = rf + risk_aversion * cov @ market_weights
    if p.size == 0:
        return BLResult(prior, prior.copy(), cov, np.zeros((0, 0)))
    if omega is None:
        conf = np.clip(confidences, 0.0, 1.0 - 1e-6)
        omega = idzorek_omega(
            cov, market_weights, p, q, conf, tau=tau, risk_aversion=risk_aversion, rf=rf
        )
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
    "idzorek_omega",
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

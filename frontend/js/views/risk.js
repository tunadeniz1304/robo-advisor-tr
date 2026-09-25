// "Risk nereden geliyor?": sınıf ağırlığı vs risk payı ve faktör betaları.

import { api } from "../api.js";
import { palette, renderChart } from "../charts.js";
import { $, esc, table } from "../dom.js";
import { classLabel, num, pct } from "../format.js";
import { state } from "../state.js";

function renderClasses(classes) {
  const entries = Object.entries(classes || {}).sort((a, b) => b[1].risk_share - a[1].risk_share);
  const labels = entries.map(([key]) => classLabel(key));
  renderChart(
    "chart-risk-classes",
    {
      type: "bar",
      tableLabel: "Varlık sınıfı",
      data: {
        labels,
        datasets: [
          {
            label: "Ağırlık",
            data: entries.map(([, c]) => c.weight),
            backgroundColor: palette[0],
          },
          {
            label: "Risk payı",
            data: entries.map(([, c]) => c.risk_share),
            backgroundColor: palette[2],
          },
        ],
      },
      options: { scales: { y: { ticks: { format: { style: "percent" } } } } },
    },
    {
      format: (v) => pct(v),
      summary:
        "Varlık sınıfı başına portföy ağırlığı ve risk payı çubuk grafiği. " +
        `En büyük risk kaynağı: ${labels[0] || "–"}.`,
    },
  );
  $("#risk-classes").innerHTML = table(
    entries,
    [
      ["Varlık sınıfı", ([key]) => esc(classLabel(key))],
      ["Ağırlık", ([, c]) => pct(c.weight)],
      ["Risk payı", ([, c]) => pct(c.risk_share)],
      ["Risk / ağırlık", ([, c]) => num(c.risk_to_weight)],
    ],
    "Sınıf bazında ağırlık ve risk katkısı",
  );
}

function renderFactors(factors) {
  const box = $("#risk-factors");
  if (!factors || factors.error) {
    box.innerHTML = `<p class="muted">Faktör analizi yapılamadı: ${esc(factors?.error || "–")}</p>`;
    return;
  }
  const rows = Object.entries(factors.betas || {});
  const summary =
    `R² ${pct(factors.r_squared)} · faktör dışı (özgün) risk payı ` +
    `${pct(factors.idiosyncratic_share)} · yıllık alfa ${pct(factors.alpha_annual)}`;
  box.innerHTML =
    table(
      rows,
      [
        ["Faktör", ([key, f]) => esc(f.label || key)],
        ["Beta", ([, f]) => num(f.beta)],
        ["t istatistiği", ([, f]) => num(f.t_stat, 1)],
        ["Varyans payı", ([, f]) => pct(f.variance_share)],
      ],
      "Faktör betaları (haftalık regresyon)",
    ) + `<p class="muted">${esc(summary)}</p>`;
}

export async function loadRisk() {
  if (!state.portfolioId) {
    return;
  }
  const info = $("#risk-info");
  let r;
  try {
    r = await api(`/portfolios/${state.portfolioId}/risk-sources`);
  } catch (err) {
    if (err.status !== 409) {
      throw err;
    }
    info.textContent = "Portföyde riskli pozisyon yok; risk ayrıştırması yapılamıyor.";
    $("#risk-classes").innerHTML = "";
    $("#risk-factors").innerHTML = "";
    return;
  }
  const w = r.window || {};
  info.textContent =
    `Yıllık oynaklık ${pct(r.volatility)} · nakit ${pct(r.cash_weight)} · ` +
    `pencere ${w.start || "–"} → ${w.end || "–"}${r.note ? `\n${r.note}` : ""}`;
  renderClasses(r.classes);
  renderFactors(r.factors);
}

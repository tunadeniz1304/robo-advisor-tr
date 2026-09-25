// Analitik: performans raporu (TWR/MWR + tear sheet) ve backtest.

import { api, post } from "../api.js";
import { lineDataset, renderChart } from "../charts.js";
import { $, announce, esc, setBusy, table } from "../dom.js";
import { annualPct, num, pct, tl } from "../format.js";
import { state } from "../state.js";

const POLICY_NAMES = { none: "Rebalance yok", calendar: "Takvim", band: "Bant" };
const BENCHMARK_NAMES = { XU100: "BIST 100", "TUFE+3": "TÜFE + 3", "60/40": "60/40 karma" };

function metricRows(r) {
  const t = r.tear_sheet || {};
  const per = r.period || {};
  const years = per.years == null ? "" : ` (${num(per.years, 1)} yıl, ${per.days} gün)`;
  return [
    ["Dönem", `${per.start || "–"} → ${per.end || "–"}${years}`],
    ["TWR (kümülatif)", pct(r.twr_cumulative)],
    ["TWR (yıllık)", annualPct(r.twr_annualized)],
    ["MWR (yıllık, XIRR)", annualPct(r.mwr_annualized)],
    ["CAGR", annualPct(t.cagr)],
    ["Risksiz faiz (TL, yıllık)", pct(t.risk_free_rate)],
    ["Volatilite (yıllık)", pct(t.volatility)],
    ["Sharpe", num(t.sharpe)],
    ["Sortino", num(t.sortino)],
    ["Calmar", t.calmar == null ? "— (<1 yıl)" : num(t.calmar)],
    ["Maks. düşüş", pct(t.max_drawdown)],
    ["VaR %95 (günlük, tarihsel)", pct(t.var_95_hist)],
    ["CVaR %95 (günlük, tarihsel)", pct(t.cvar_95_hist)],
    [`${r.benchmark_symbol || "BIST"} CAGR`, annualPct(t.benchmark?.cagr)],
    ["Yıllık TÜFE", pct(r.inflation_yoy)],
  ];
}

export async function loadAnalytics() {
  if (!state.portfolioId) {
    return;
  }
  const r = await api(`/portfolios/${state.portfolioId}/report`);
  $("#tearsheet").innerHTML = table(
    metricRows(r),
    [
      ["Metrik", (x) => esc(x[0])],
      ["Değer", (x) => esc(x[1])],
    ],
    "Performans metrikleri",
  );
  const curve = (r.value_curve || []).filter((v) => v.value > 0);
  renderChart(
    "chart-value",
    {
      type: "line",
      tableLabel: "Tarih",
      data: {
        labels: curve.map((v) => v.date),
        datasets: [lineDataset("Portföy değeri", curve.map((v) => v.value), 0)],
      },
    },
    {
      format: tl,
      summary: `Portföy değeri, ${curve[0]?.date || "–"} ile ${curve.at(-1)?.date || "–"} arası.`,
    },
  );
}

function statCells(stats) {
  const s = stats || {};
  return [pct(s.cagr), pct(s.volatility), num(s.sharpe), pct(s.max_drawdown)];
}

// Tüm stratejiler + kıyaslar için satırlar: [ad, tür, stats, final, rebalance, maliyet]
function backtestRows(r) {
  const rows = [];
  if (r.mode === "walk_forward") {
    const name = `Walk-forward (${state.methods[r.method] || r.method})`;
    rows.push([name, "Strateji", r.stats, r.final_value, r.rebalances, r.total_cost]);
  } else {
    for (const [key, v] of Object.entries(r.results || {})) {
      const final = v.equity_curve?.at(-1)?.value;
      rows.push([POLICY_NAMES[key] || key, "Politika", v.stats, final, v.rebalances, v.total_cost]);
    }
  }
  for (const [key, b] of Object.entries(r.benchmarks || {})) {
    rows.push([BENCHMARK_NAMES[key] || key, "Kıyas", b.stats, b.final_value, null, null]);
  }
  return rows;
}

function backtestCurves(r) {
  const curves = [];
  if (r.mode === "walk_forward") {
    curves.push(["Walk-forward", r.equity_curve]);
  } else {
    for (const [key, v] of Object.entries(r.results || {})) {
      curves.push([POLICY_NAMES[key] || key, v.equity_curve]);
    }
  }
  for (const [key, b] of Object.entries(r.benchmarks || {})) {
    curves.push([BENCHMARK_NAMES[key] || key, b.equity_curve]);
  }
  return curves.filter(([, c]) => Array.isArray(c) && c.length);
}

function renderBacktestChart(r) {
  const curves = backtestCurves(r);
  if (!curves.length) {
    return;
  }
  const labels = curves[0][1].map((e) => e.date);
  const datasets = curves.map(([name, curve], i) => {
    const byDate = new Map(curve.map((e) => [e.date, e.value]));
    const extra = i >= curves.length - Object.keys(r.benchmarks || {}).length
      ? { borderDash: [5, 3] }
      : {};
    return lineDataset(name, labels.map((d) => byDate.get(d) ?? null), i, extra);
  });
  renderChart(
    "chart-backtest",
    { type: "line", tableLabel: "Tarih", data: { labels, datasets } },
    {
      format: tl,
      summary: `Backtest değer eğrileri (${curves.map((c) => c[0]).join(", ")}).`,
    },
  );
}

function backtestInfo(r) {
  if (r.mode !== "walk_forward") {
    return `Sabit hedef ağırlıklar, risk seviyesi ${r.level}, ${r.years} yıl.`;
  }
  const excluded = (r.excluded_short_history || []).join(", ");
  return (
    `Test dönemi ${r.test_start} → ${r.test_end} · tahmin penceresi ${r.window_days} gün ` +
    `(${r.window === "expanding" ? "genişleyen" : "kayan"}) · ` +
    `${r.rebalance_days} günde bir yeniden optimizasyon · ileriye bakış yok.` +
    (excluded ? ` Kısa geçmiş nedeniyle dışarıda: ${excluded}.` : "")
  );
}

async function runBacktest(button) {
  const walkForward = $("#bt-mode").value === "walk_forward";
  const body = { customer_id: state.customerId, years: Number($("#bt-years").value) || 5 };
  if (walkForward) {
    body.use_optimizer = true;
    body.method = $("#bt-method").value || null;
  }
  setBusy(button, true);
  announce("Backtest çalışıyor…");
  try {
    const r = await post("/backtest", body);
    $("#backtest-info").textContent = backtestInfo(r);
    $("#backtest").innerHTML = table(
      backtestRows(r),
      [
        ["Strateji", (x) => esc(x[0])],
        ["Tür", (x) => esc(x[1])],
        ["CAGR", (x) => statCells(x[2])[0]],
        ["Volatilite", (x) => statCells(x[2])[1]],
        ["Sharpe", (x) => statCells(x[2])[2]],
        ["Maks. düşüş", (x) => statCells(x[2])[3]],
        ["Son değer", (x) => tl(x[3])],
        ["Rebalance", (x) => esc(x[4] ?? "–")],
        ["Maliyet", (x) => (x[5] == null ? "–" : tl(x[5]))],
      ],
      "Backtest sonuçları ve kıyaslar",
    );
    renderBacktestChart(r);
    announce("Backtest tamamlandı.");
  } catch (err) {
    announce(`Backtest başarısız: ${err.message}`, "error");
  } finally {
    setBusy(button, false);
  }
}

function syncBacktestControls() {
  const walkForward = $("#bt-mode").value === "walk_forward";
  $("#bt-method-wrap").classList.toggle("hidden", !walkForward);
}

export function wireAnalytics() {
  $("#btn-backtest").addEventListener("click", (e) => runBacktest(e.currentTarget));
  $("#bt-mode").addEventListener("change", syncBacktestControls);
  syncBacktestControls();
}

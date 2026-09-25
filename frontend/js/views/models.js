// Model portföyler: 10 risk seviyesinin risk/getiri karşılaştırması (walk-forward).

import { api } from "../api.js";
import { lineDataset, palette, renderChart } from "../charts.js";
import { $, esc } from "../dom.js";
import { num, pct, tl } from "../format.js";
import { isStaff, state } from "../state.js";

const BENCHMARK_NAMES = { XU100: "BIST 100", "TUFE+3": "TÜFE + 3", "60/40": "60/40 karma" };

const PERCENT_TICKS = { format: { style: "percent" } };

let cache = null;
let ownLevel = null;

async function customerLevel() {
  if (!state.customerId) {
    return null;
  }
  try {
    const profile = await api(`/customers/${state.customerId}/risk-profile`);
    return profile.effective?.level ?? null;
  } catch {
    return null;
  }
}

function point(x, y, label) {
  return { x, y, label };
}

function ownTag() {
  return isStaff() ? "müşterinin seviyesi" : "sizin seviyeniz";
}

function levelName(key, level) {
  const own = Number(key) === ownLevel ? ` (${ownTag()})` : "";
  return `Seviye ${key} · ${level.label}${own}`;
}

function scatterDatasets(r) {
  const levels = Object.entries(r.levels);
  const wf = levels.map(([k, l]) => {
    return point(l.walk_forward.volatility, l.walk_forward.cagr, levelName(k, l));
  });
  const stat = levels.map(([k, l]) => {
    return point(l.static_model.volatility, l.static_model.cagr, `${levelName(k, l)} statik`);
  });
  const bench = Object.entries(r.benchmarks || {}).map(([k, b]) => {
    return point(b.volatility, b.cagr, BENCHMARK_NAMES[k] || k);
  });
  const sets = [
    { label: "Walk-forward", data: wf, backgroundColor: palette[0], pointRadius: 5 },
    { label: "Statik model", data: stat, backgroundColor: palette[5], pointRadius: 4 },
    { label: "Kıyaslar", data: bench, backgroundColor: palette[7], pointStyle: "rect" },
  ];
  const own = r.levels[String(ownLevel)];
  if (own) {
    const p = point(own.walk_forward.volatility, own.walk_forward.cagr, levelName(ownLevel, own));
    sets.push({
      label: ownTag(),
      data: [p],
      backgroundColor: palette[6],
      pointRadius: 10,
      pointStyle: "star",
      borderColor: palette[6],
      borderWidth: 2,
    });
  }
  return sets;
}

function tableRows(r) {
  const rows = Object.entries(r.levels).map(([k, l]) => [
    levelName(k, l),
    pct(l.walk_forward.cagr),
    pct(l.walk_forward.volatility),
    num(l.walk_forward.sharpe),
    pct(l.walk_forward.max_drawdown),
    pct(l.static_model.cagr),
    pct(l.static_model.volatility),
  ]);
  for (const [k, b] of Object.entries(r.benchmarks || {})) {
    const name = BENCHMARK_NAMES[k] || k;
    const drawdown = pct(b.max_drawdown);
    rows.push([name, pct(b.cagr), pct(b.volatility), num(b.sharpe), drawdown, "–", "–"]);
  }
  return rows;
}

function renderScatter(r) {
  const tooltip = { callbacks: { label: (ctx) => ctx.raw.label } };
  renderChart(
    "chart-models",
    {
      type: "scatter",
      data: { datasets: scatterDatasets(r) },
      options: {
        aspectRatio: 2,
        plugins: { tooltip },
        scales: {
          x: { title: { display: true, text: "Yıllık oynaklık" }, ticks: PERCENT_TICKS },
          y: { title: { display: true, text: "Yıllık getiri (CAGR)" }, ticks: PERCENT_TICKS },
        },
      },
    },
    {
      summary:
        "Risk seviyelerinin oynaklık–getiri dağılım grafiği (walk-forward ve statik model)" +
        (ownLevel ? `; ${ownTag()} (${ownLevel}) vurgulandı.` : "."),
      table: {
        head: [
          "Seviye / kıyas",
          "WF CAGR",
          "WF oynaklık",
          "WF Sharpe",
          "WF maks. düşüş",
          "Statik CAGR",
          "Statik oynaklık",
        ],
        rows: tableRows(r),
      },
    },
  );
}

function renderCurve(r) {
  const key = $("#model-level").value;
  const level = r.levels[key];
  if (!level) {
    return;
  }
  const labels = level.equity_curve.map((e) => e.date);
  const sets = [lineDataset(levelName(key, level), level.equity_curve.map((e) => e.value), 0)];
  Object.entries(r.benchmark_curves || {}).forEach(([k, curve], i) => {
    const byDate = new Map(curve.map((e) => [e.date, e.value]));
    const data = labels.map((d) => byDate.get(d) ?? null);
    sets.push(lineDataset(BENCHMARK_NAMES[k] || k, data, i + 1, { borderDash: [5, 3] }));
  });
  renderChart(
    "chart-model-curve",
    { type: "line", tableLabel: "Tarih", data: { labels, datasets: sets } },
    {
      format: tl,
      summary: `Seviye ${key} walk-forward değer eğrisi ve kıyaslar (${labels.length} gözlem).`,
    },
  );
}

export async function loadModels() {
  if (!cache) {
    cache = await api("/model-portfolios/performance");
  }
  ownLevel = await customerLevel();
  const r = cache;
  const first = Object.values(r.levels)[0] || {};
  $("#models-info").textContent =
    `Yöntem ${r.method} · test ${first.test_start || "–"} → ${first.test_end || "–"} · ` +
    `risksiz faiz ${pct(r.risk_free_rate)} · veri ${r.source || "–"} (${r.snapshot_end || "–"})`;
  const select = $("#model-level");
  const previous = select.value || String(ownLevel || 5);
  select.innerHTML = Object.entries(r.levels)
    .map(([k, l]) => `<option value="${esc(k)}">${esc(levelName(k, l))}</option>`)
    .join("");
  select.value = r.levels[previous] ? previous : Object.keys(r.levels)[0];
  renderScatter(r);
  renderCurve(r);
}

export function wireModels() {
  $("#model-level").addEventListener("change", () => {
    if (cache) {
      renderCurve(cache);
    }
  });
}

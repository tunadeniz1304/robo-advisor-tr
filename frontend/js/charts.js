// Chart.js yardımcıları + her grafiğin erişilebilir tablo karşılığı.
//
// Her <canvas> role="img", aria-label ve data-table="<tablo id>" taşır;
// renderChart grafiği çizer ve aynı veriyi o tabloya yazar.

import { $, esc } from "./dom.js";
import { state } from "./state.js";

export const palette = [
  "#1f5eff",
  "#12b76a",
  "#f79009",
  "#7a5af8",
  "#ee46bc",
  "#06aed4",
  "#d92d20",
  "#667085",
  "#84cc16",
  "#0e9384",
];

function isDark() {
  return document.documentElement.dataset.theme === "dark";
}

function identity(value) {
  return value == null ? "–" : String(value);
}

// Grafik verisini (etiketler × veri setleri) tabloya yazar.
export function fillDataTable(tableEl, config, format = identity) {
  if (!tableEl) {
    return;
  }
  const labels = config.data.labels || [];
  const sets = config.data.datasets || [];
  const firstHead = config.tableLabel || "Etiket";
  const heads = [firstHead, ...sets.map((d, i) => d.label || `Seri ${i + 1}`)];
  const headHtml = heads.map((h) => `<th scope="col">${esc(h)}</th>`).join("");
  const rows = labels.map((label, i) => {
    const cells = sets.map((d) => `<td>${esc(format(d.data[i]))}</td>`).join("");
    return `<tr><th scope="row">${esc(label)}</th>${cells}</tr>`;
  });
  tableEl.innerHTML = `<thead><tr>${headHtml}</tr></thead><tbody>${rows.join("")}</tbody>`;
}

// Özel tablo (ör. scatter verisi): head = başlıklar, rows = hücre metinleri dizisi.
export function fillCustomTable(tableEl, head, rows) {
  if (!tableEl) {
    return;
  }
  const headHtml = head.map((h) => `<th scope="col">${esc(h)}</th>`).join("");
  const body = rows.map((r) => {
    const [first, ...rest] = r;
    const cells = rest.map((c) => `<td>${esc(c)}</td>`).join("");
    return `<tr><th scope="row">${esc(first)}</th>${cells}</tr>`;
  });
  tableEl.innerHTML = `<thead><tr>${headHtml}</tr></thead><tbody>${body.join("")}</tbody>`;
}

/**
 * Draw a chart on canvas #id and mirror its data into the canvas' data-table.
 * @param {string} id canvas id
 * @param {object} config Chart.js config (+ optional tableLabel)
 * @param {object} [opts] { format, summary, table: {head, rows} for custom tables }
 */
export function renderChart(id, config, opts = {}) {
  const canvas = $(`#${id}`);
  if (!canvas) {
    return;
  }
  if (opts.summary) {
    canvas.setAttribute("aria-label", opts.summary);
  }
  const tableId = canvas.dataset.table;
  const tableEl = tableId ? document.getElementById(tableId) : null;
  if (opts.table) {
    fillCustomTable(tableEl, opts.table.head, opts.table.rows);
  } else {
    fillDataTable(tableEl, config, opts.format);
  }
  if (!window.Chart) {
    return;
  }
  state.charts[id]?.destroy();
  window.Chart.defaults.color = isDark() ? "#b4bccb" : "#4b5565";
  const { tableLabel, ...chartConfig } = config;
  if (chartConfig.type === "line") {
    chartConfig.options = { aspectRatio: 2.6, ...chartConfig.options };
  }
  state.charts[id] = new window.Chart(canvas, chartConfig);
}

export function lineDataset(label, data, colorIndex, extra = {}) {
  return {
    label,
    data,
    borderColor: palette[colorIndex % palette.length],
    backgroundColor: palette[colorIndex % palette.length],
    fill: false,
    pointRadius: 0,
    borderWidth: 2,
    ...extra,
  };
}

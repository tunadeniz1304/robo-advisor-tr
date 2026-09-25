// Stres laboratuvarı: hazır ve özel senaryolar.

import { api, post } from "../api.js";
import { palette, renderChart } from "../charts.js";
import { $, announce, esc, setBusy } from "../dom.js";
import { pct, tl } from "../format.js";
import { state } from "../state.js";

export async function loadStress() {
  if ($("#stress-scenario").options.length) {
    return;
  }
  const c = await api("/stress/scenarios");
  const all = [...Object.entries(c.factor || {}), ...Object.entries(c.historical || {})];
  $("#stress-scenario").innerHTML = all
    .map(([key, v]) => `<option value="${esc(key)}">${esc(v.label)}</option>`)
    .join("");
}

function renderResult(r) {
  const goals = (r.hedef_etkisi || [])
    .map((h) => `<p>Hedef "${esc(h.ad)}": ${pct(h.once)} → ${pct(h.sonra)}</p>`)
    .join("");
  $("#stress-result").innerHTML = `<p><b>${esc(r.senaryo_adi)}</b>: ${tl(r.pnl)}
    (${pct(r.pnl_orani)}) · maks. düşüş ${pct(r.maks_dusus)}</p>${goals}`;
  const contributions = Object.entries(r.katkilar || {});
  renderChart(
    "chart-stress",
    {
      type: "bar",
      tableLabel: "Varlık",
      data: {
        labels: contributions.map((x) => x[0]),
        datasets: [
          {
            label: "Kâr/zarar (TL)",
            data: contributions.map((x) => x[1]),
            backgroundColor: contributions.map((x) => (x[1] < 0 ? palette[6] : palette[1])),
          },
        ],
      },
      options: { indexAxis: "y" },
    },
    {
      format: tl,
      summary: `${r.senaryo_adi}: varlık bazında kâr/zarar katkıları. Toplam ${tl(r.pnl)}.`,
    },
  );
}

async function runStress(body, button) {
  if (!state.portfolioId) {
    return;
  }
  setBusy(button, true);
  try {
    const r = await post(`/portfolios/${state.portfolioId}/stress`, body);
    renderResult(r);
    announce(`Senaryo uygulandı: ${r.senaryo_adi}.`);
  } catch (err) {
    announce(`Senaryo uygulanamadı: ${err.message}`, "error");
  } finally {
    setBusy(button, false);
  }
}

export function wireStress() {
  $("#btn-stress").addEventListener("click", (e) => {
    runStress({ scenario: $("#stress-scenario").value }, e.currentTarget);
  });
  $("#btn-stress-custom").addEventListener("click", (e) => {
    const custom = {
      bist: Number($("#sc-bist").value),
      usdtry: Number($("#sc-usd").value),
      gold: Number($("#sc-gold").value),
      rate: Number($("#sc-rate").value),
    };
    runStress({ custom }, e.currentTarget);
  });
}

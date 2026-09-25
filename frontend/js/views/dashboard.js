// Panel: KPI'lar, varlık dağılımı, hedef halkaları, bildirimler, piyasa yorumu.

import { api, post } from "../api.js";
import { palette, renderChart } from "../charts.js";
import { $, $$, announce, esc } from "../dom.js";
import { pct, tl } from "../format.js";
import { state } from "../state.js";

function renderAllocation(valuation) {
  const items = valuation.items.filter((i) => i.market_value > 0);
  const labels = [...items.map((i) => i.ticker), "Nakit"];
  const values = [...items.map((i) => i.market_value), valuation.cash];
  const top = items
    .slice()
    .sort((a, b) => b.market_value - a.market_value)
    .slice(0, 3)
    .map((i) => i.ticker)
    .join(", ");
  renderChart(
    "chart-allocation",
    {
      type: "doughnut",
      tableLabel: "Varlık",
      data: {
        labels,
        datasets: [{ label: "Piyasa değeri", data: values, backgroundColor: palette }],
      },
      options: { plugins: { legend: { position: "right" } } },
    },
    {
      format: tl,
      summary: `Varlık dağılımı halka grafiği, ${labels.length} kalem. En büyükler: ${top}.`,
    },
  );
}

function renderGoalRings(goals) {
  const box = $("#goal-rings");
  if (!goals.length) {
    box.innerHTML = '<p class="muted">Henüz hedef yok.</p>';
    return;
  }
  box.innerHTML = goals
    .map((g) => {
      const p = g.last_simulation?.success_probability;
      const label = `${g.name}: başarı olasılığı ${pct(p, 0)}`;
      return `<div class="ring">
        <div class="ring-graphic" role="img" aria-label="${esc(label)}"></div>
        <div>${esc(g.name)}<br><b>${pct(p, 0)}</b></div>
      </div>`;
    })
    .join("");
  $$(".ring-graphic", box).forEach((el, k) => {
    const p = goals[k].last_simulation?.success_probability ?? 0;
    el.style.setProperty("--p", `${Math.round(p * 100)}%`);
  });
}

function renderNudges(nudges) {
  $("#nudges").innerHTML =
    nudges
      .map(
        (n) => `<li class="sev-${esc(n.severity)}"><b>${esc(n.title)}</b><br>${esc(n.message)}
          <button class="btn ghost" data-dismiss="${n.id}" type="button"
            aria-label="Bildirimi kapat: ${esc(n.title)}">Kapat</button></li>`,
      )
      .join("") || '<li class="muted">Bildirim yok.</li>';
}

function loadCommentary() {
  post("/commentary/market")
    .then((c) => {
      const bullets = (c.maddeler || []).map((m) => `• ${m}`).join("\n");
      $("#commentary").textContent = `${c.baslik}\n${c.ozet}\n${bullets}\n\n${c.disclaimer}`;
    })
    .catch(() => {
      $("#commentary").textContent = "Piyasa yorumu şu an alınamadı.";
    });
}

export async function loadDashboard() {
  if (!state.portfolioId) {
    $("#kpi-value").textContent = "–";
    announce("Bu müşterinin portföyü yok.");
    return;
  }
  const cid = state.customerId;
  const [val, prof, goals, nudges] = await Promise.all([
    api(`/portfolios/${state.portfolioId}/valuation`),
    api(`/customers/${cid}/risk-profile`),
    api(`/customers/${cid}/goals`),
    api(`/customers/${cid}/nudges`),
  ]);
  $("#kpi-value").textContent = tl(val.total_value);
  $("#kpi-cash").textContent = tl(val.cash);
  $("#kpi-level").textContent = `${prof.effective.level}/10 · ${prof.effective.label}`;
  api(`/portfolios/${state.portfolioId}/report`)
    .then((r) => {
      $("#kpi-twr").textContent = pct(r.twr_cumulative);
      const period = r.period || {};
      $("#kpi-twr-period").textContent = `${period.start || "–"} → ${period.end || "–"}`;
    })
    .catch(() => {
      $("#kpi-twr").textContent = "–";
    });
  renderAllocation(val);
  renderGoalRings(goals);
  renderNudges(nudges);
  loadCommentary();
}

export function wireDashboard() {
  $("#nudges").addEventListener("click", async (e) => {
    const button = e.target.closest("button[data-dismiss]");
    if (!button) {
      return;
    }
    await post(`/nudges/${button.dataset.dismiss}/dismiss`);
    announce("Bildirim kapatıldı.");
    await loadDashboard();
  });
}

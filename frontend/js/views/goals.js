// Hedefler: liste, ekleme, Monte Carlo simülasyonu ve "ya olursa" kaydırıcıları.

import { api, post } from "../api.js";
import { lineDataset, renderChart } from "../charts.js";
import { $, announce, esc, setBusy } from "../dom.js";
import { pct, tl } from "../format.js";
import { state } from "../state.js";

const NUMERIC_FIELDS = [
  "target_amount_real",
  "horizon_years",
  "initial_amount",
  "monthly_contribution",
];

export async function loadGoals() {
  const goals = await api(`/customers/${state.customerId}/goals`);
  $("#goal-list").innerHTML =
    goals
      .map(
        (g) => `<li><b>${esc(g.name)}</b> (${esc(g.goal_type_label)}) ·
          hedef ${tl(g.target_amount_real)} · ${g.horizon_years} yıl
          <button class="btn" data-sim="${g.id}" type="button"
            aria-label="Simüle et: ${esc(g.name)}">Simüle et</button></li>`,
      )
      .join("") || '<li class="muted">Hedef ekleyin.</li>';
  const selected = goals.find((g) => g.id === state.goal) || goals[0];
  if (selected) {
    await simulate(selected.id);
  }
}

function fanChart(result, goal) {
  const bands = result.bands;
  const labels = bands.map((b) => `${Math.round(b.month / 12)}. yıl`);
  renderChart(
    "chart-fan",
    {
      type: "line",
      tableLabel: "Zaman",
      data: {
        labels,
        datasets: [
          lineDataset("P90", bands.map((b) => b.p90), 1),
          lineDataset("P50 (reel)", bands.map((b) => b.p50), 0),
          lineDataset("P10", bands.map((b) => b.p10), 6),
          lineDataset("Hedef", bands.map(() => goal.target_amount_real), 7, {
            borderDash: [6, 4],
          }),
        ],
      },
    },
    {
      format: tl,
      summary:
        `${goal.name} için servet yelpazesi (P10–P90). ` +
        `Başarı olasılığı ${pct(result.success_probability)}.`,
    },
  );
}

export async function simulate(id) {
  state.goal = Number(id);
  const r = await post(`/goals/${id}/simulate?with_report=true`);
  const s = r.result;
  $("#goal-title").textContent = `Simülasyon — ${r.goal.name}`;
  const whatIf = (s.what_if || [])
    .map((w) => `• ${w.aciklama}: ${pct(w.basari_olasiligi)}`)
    .join("\n");
  $("#goal-summary").textContent =
    `Başarı olasılığı ${pct(s.success_probability)} · Medyan (reel) ${tl(s.p50_real)} · ` +
    `Gerekli aylık katkı ${tl(s.required_monthly_contribution)}\n` +
    `${r.report?.ozet ?? ""}\n${whatIf}`;
  fanChart(s, r.goal);
}

let whatIfTimer = null;

function whatIf() {
  for (const key of ["horizon", "contrib", "level"]) {
    $(`#wi-${key}-o`).textContent = $(`#wi-${key}`).value;
  }
  clearTimeout(whatIfTimer);
  whatIfTimer = setTimeout(runWhatIf, 350);
}

async function runWhatIf() {
  if (!state.goal) {
    return;
  }
  try {
    const r = await post(`/goals/${state.goal}/what-if`, {
      horizon_delta_years: Number($("#wi-horizon").value),
      contribution_multiplier: Number($("#wi-contrib").value),
      level_delta: Number($("#wi-level").value),
    });
    $("#whatif-result").textContent =
      `Senaryo başarı olasılığı ${pct(r.scenario.success_probability)} ` +
      `(fark ${pct(r.delta_success_probability)})`;
  } catch (err) {
    announce(err.message, "error");
  }
}

async function onGoalSubmit(e) {
  e.preventDefault();
  const form = e.target;
  const data = Object.fromEntries(new FormData(form));
  for (const key of NUMERIC_FIELDS) {
    data[key] = Number(data[key] || 0);
  }
  const button = form.querySelector("button[type=submit]");
  setBusy(button, true);
  try {
    const goal = await post(`/customers/${state.customerId}/goals`, data);
    form.reset();
    state.goal = goal.id;
    announce(`Hedef eklendi: ${goal.name}.`);
    await loadGoals();
  } catch (err) {
    announce(`Hedef eklenemedi: ${err.message}`, "error");
  } finally {
    setBusy(button, false);
  }
}

export function wireGoals() {
  $("#goal-form").addEventListener("submit", onGoalSubmit);
  $("#goal-list").addEventListener("click", (e) => {
    const button = e.target.closest("button[data-sim]");
    if (button) {
      simulate(button.dataset.sim).catch((err) => announce(err.message, "error"));
    }
  });
  for (const key of ["horizon", "contrib", "level"]) {
    $(`#wi-${key}`).addEventListener("input", whatIf);
  }
}

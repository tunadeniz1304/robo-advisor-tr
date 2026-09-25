// SPK uygunluk testi (risk profili anketi).

import { api, post } from "../api.js";
import { $, announce, esc } from "../dom.js";
import { state } from "../state.js";

export async function loadQuestionnaire() {
  if (!state.q.list.length) {
    state.q.list = (await api("/suitability/questionnaire")).questions;
  }
  renderQuestion();
}

function optionHtml(question, option) {
  const checked = state.q.answers[question.id] === option.value ? "checked" : "";
  return `<label class="option">
    <input type="radio" name="opt" value="${esc(option.value)}" ${checked}>
    ${esc(option.label)}
  </label>`;
}

function renderQuestion() {
  const { list, i } = state.q;
  const q = list[i];
  if (!q) {
    return;
  }
  const bar = $("#q-progress");
  bar.style.width = `${((i + 1) / list.length) * 100}%`;
  bar.parentElement.setAttribute("aria-valuenow", String(i + 1));
  bar.parentElement.setAttribute("aria-valuemax", String(list.length));
  const help = q.help ? `<p class="muted">${esc(q.help)}</p>` : "";
  $("#q-body").innerHTML = `<fieldset class="question">
    <legend><span class="muted">${esc(q.section)} · ${i + 1}/${list.length}</span>
      <span class="q-text">${esc(q.text)}</span></legend>
    ${q.options.map((o) => optionHtml(q, o)).join("")}
    ${help}
  </fieldset>`;
  $("#q-next").textContent = i === list.length - 1 ? "Gönder" : "İleri";
  $("#q-prev").disabled = i === 0;
}

async function submitProfile(confirm = false) {
  const out = $("#q-result");
  try {
    const r = await post(`/customers/${state.customerId}/risk-profile`, {
      answers: state.q.answers,
      confirm_inconsistencies: confirm,
    });
    const p = r.profile;
    out.textContent =
      `Risk seviyeniz: ${p.risk_level}/10 (${p.risk_label})\n` + r.result.explanation.join("\n");
    announce(`Risk profili kaydedildi: ${p.risk_level}/10.`);
  } catch (e) {
    if (e.status !== 409) {
      out.textContent = e.message;
      announce(e.message, "error");
      return;
    }
    const d = e.body.detail;
    const warnings = d.result.warnings.map((w) => esc(w.message)).join("<br>");
    out.innerHTML = `<p class="error">${esc(d.message)}</p><p>${warnings}</p>
      <button id="q-confirm" class="btn" type="button">Cevaplarımı onaylıyorum</button>`;
    $("#q-confirm").addEventListener("click", () => submitProfile(true));
    const idx = state.q.list.findIndex((q) => d.reask.includes(q.id));
    state.q.i = Math.max(0, idx);
    renderQuestion();
    announce(d.message, "error");
  }
}

function next() {
  const q = state.q.list[state.q.i];
  if (!q) {
    return;
  }
  if (!state.q.answers[q.id]) {
    announce("Devam etmek için bir seçenek işaretleyin.", "error");
    return;
  }
  if (state.q.i < state.q.list.length - 1) {
    state.q.i += 1;
    renderQuestion();
    $("#q-body input")?.focus();
  } else {
    submitProfile();
  }
}

export function wireOnboarding() {
  $("#q-body").addEventListener("change", (e) => {
    if (e.target.name === "opt") {
      state.q.answers[state.q.list[state.q.i].id] = e.target.value;
    }
  });
  $("#q-prev").addEventListener("click", () => {
    state.q.i = Math.max(0, state.q.i - 1);
    renderQuestion();
  });
  $("#q-next").addEventListener("click", next);
}

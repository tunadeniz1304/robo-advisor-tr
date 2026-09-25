// Yeniden dengeleme: öneri oluşturma, önizleme, onay / ret.

import { api, post } from "../api.js";
import { $, announce, esc, setBusy, table } from "../dom.js";
import { dateTime, pct, proposalStatusLabel, tl } from "../format.js";
import { state } from "../state.js";

// Eski (yavaş) bir yüklemenin yeni öneriyi ezmesini önleyen sıra numarası.
let renderSeq = 0;

export async function loadRebalance() {
  const seq = ++renderSeq;
  if (!state.portfolioId) {
    $("#proposal").innerHTML = '<p class="muted">Portföy yok.</p>';
    return;
  }
  const pending = await api(
    `/proposals?portfolio_id=${state.portfolioId}&status=ONAY_BEKLIYOR`,
  );
  if (seq !== renderSeq) {
    return;
  }
  if (pending[0]) {
    renderProposal(pending[0]);
  } else {
    $("#proposal").innerHTML = '<p class="muted">Bekleyen öneri yok.</p>';
  }
}

function ordersTable(orders) {
  return table(
    orders || [],
    [
      ["Sembol", (o) => esc(o.symbol)],
      ["Yön", (o) => (o.side === "BUY" ? "Alış" : "Satış")],
      ["Tutar", (o) => tl(o.amount)],
      ["Maliyet", (o) => tl(o.cost?.total)],
    ],
    "Emirler",
  );
}

function weightsTable(p) {
  const before = p.before_weights || {};
  const after = p.after_weights || {};
  const symbols = Object.keys({ ...before, ...after });
  return table(
    symbols.map((s) => [s, before[s] || 0, after[s] || 0]),
    [
      ["Varlık", (r) => esc(r[0])],
      ["Önce", (r) => pct(r[1])],
      ["Sonra", (r) => pct(r[2])],
    ],
    "Ağırlıklar (önce / sonra)",
  );
}

function executionSummary(p) {
  const report = p.execution_report;
  if (!report || !p.executed_at) {
    return "";
  }
  const fills = report.fills?.length ?? 0;
  return `<p>Yürütme: ${fills} emir gerçekleşti · ${esc(dateTime(p.executed_at))}</p>`;
}

function actionButtons(p) {
  if (p.status !== "ONAY_BEKLIYOR") {
    return "";
  }
  return `<div class="actions">
    <button class="btn ok" data-approve="${p.id}" type="button">Onayla ve yürüt</button>
    <button class="btn" data-reject="${p.id}" type="button">Reddet</button>
  </div>`;
}

export function renderProposal(p) {
  const cards = (p.explanation || [])
    .map((c) => `<div class="why"><b>${esc(c.baslik)}</b>${esc(c.metin)}</div>`)
    .join("");
  const risk = `${pct(p.risk_before?.volatility)} → ${pct(p.risk_after?.volatility)}`;
  $("#proposal").innerHTML = `
    <p><b>Öneri #${p.id}</b> ·
      <span class="status-pill" data-status="${esc(p.status)}">
        ${esc(proposalStatusLabel(p.status))}</span> ·
      ${esc(p.method_label || p.source)} · son geçerlilik ${esc(dateTime(p.expires_at))}</p>
    ${executionSummary(p)}
    <div class="cards">${cards}</div>
    <div class="table-wrap">${ordersTable(p.orders)}</div>
    <p>Tahmini maliyet ${tl(p.estimated_cost)} · tahmini stopaj ${tl(p.estimated_tax)} ·
      devir ${pct(p.turnover)} · oynaklık ${risk}</p>
    <div class="table-wrap">${weightsTable(p)}</div>
    <p class="prose">${esc(p.rationale)}</p>
    <p class="muted">${esc(p.disclaimer)}</p>
    ${actionButtons(p)}`;
}

export async function approveProposal(id) {
  renderSeq += 1;
  const proposal = await api(`/proposals/${id}/approve`, {
    method: "POST",
    headers: { "Idempotency-Key": `ui-${id}` },
  });
  announce(`Öneri #${id}: ${proposalStatusLabel(proposal.status)}.`);
  return proposal;
}

async function createProposal(button) {
  const method = $("#opt-method").value;
  const query = `customer_id=${state.customerId}${method ? `&method=${method}` : ""}`;
  renderSeq += 1;
  setBusy(button, true);
  $("#proposal").setAttribute("aria-busy", "true");
  try {
    const r = await post(`/advisor/rebalance/${state.portfolioId}?${query}`);
    if (!r.needs_rebalance) {
      $("#proposal").innerHTML = `<p>${esc(r.message)}</p>`;
      announce(r.message);
    } else {
      renderProposal(r.proposal);
      announce(`Öneri #${r.proposal.id} oluşturuldu, onay bekliyor.`);
    }
  } catch (err) {
    announce(`Öneri oluşturulamadı: ${err.message}`, "error");
  } finally {
    setBusy(button, false);
    $("#proposal").removeAttribute("aria-busy");
  }
}

async function onProposalClick(e) {
  const button = e.target.closest("button");
  if (!button) {
    return;
  }
  try {
    if (button.dataset.approve) {
      setBusy(button, true);
      renderProposal(await approveProposal(button.dataset.approve));
    } else if (button.dataset.reject) {
      const id = button.dataset.reject;
      const p = await post(`/proposals/${id}/reject`, { reason: "Kullanıcı reddetti" });
      renderProposal(p);
      announce(`Öneri #${id} reddedildi.`);
    }
  } catch (err) {
    setBusy(button, false);
    announce(err.message, "error");
  }
}

export function wireRebalance() {
  $("#btn-propose").addEventListener("click", (e) => createProposal(e.currentTarget));
  $("#proposal").addEventListener("click", onProposalClick);
}

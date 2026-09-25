// Danışman paneli: onay kuyruğu, Black-Litterman görüşleri, denetim izi.

import { api, post } from "../api.js";
import { $, announce, esc } from "../dom.js";
import { pct } from "../format.js";
import { show } from "../router.js";
import { approveProposal, renderProposal } from "./rebalance.js";

function queueItem(p) {
  return `<li>#${p.id} · müşteri ${p.customer_id} · ${p.orders.length} emir · ${esc(p.source)}
    <button class="btn ok" data-approve="${p.id}" type="button">Onayla</button>
    <button class="btn" data-show="${p.id}" type="button">İncele</button></li>`;
}

function viewItem(v) {
  const approve =
    v.status === "onerildi"
      ? `<button class="btn" data-view-approve="${v.id}" type="button">Onayla</button>`
      : "";
  return `<li>${esc(v.symbol)} · ${pct(v.expected_return)} · güven ${pct(v.confidence, 0)} ·
    ${esc(v.source)} · <b>${esc(v.status)}</b> ${approve}</li>`;
}

function auditItem(a) {
  return `<li>${esc(a.created_at.slice(0, 19))} · ${esc(a.actor)} · <b>${esc(a.action)}</b> ·
    ${esc(a.entity_type)} ${esc(a.entity_id)} · <code>${esc(a.hash.slice(0, 10))}…</code></li>`;
}

export async function loadAdvisor() {
  const [queue, views, audit] = await Promise.all([
    api("/proposals?status=ONAY_BEKLIYOR"),
    api("/bl-views"),
    api("/audit?limit=30"),
  ]);
  $("#queue").innerHTML = queue.map(queueItem).join("") || '<li class="muted">Kuyruk boş.</li>';
  $("#views").innerHTML = views.map(viewItem).join("");
  $("#audit").innerHTML = audit.map(auditItem).join("");
}

async function onPanelClick(e) {
  const button = e.target.closest("button");
  if (!button) {
    return;
  }
  const { approve, show: showId, viewApprove } = button.dataset;
  try {
    if (approve) {
      button.disabled = true;
      await approveProposal(approve);
      await loadAdvisor();
    } else if (showId) {
      await show("rebalance");
      renderProposal(await api(`/proposals/${showId}`));
    } else if (viewApprove) {
      await post(`/bl-views/${viewApprove}/approve`);
      announce("Görüş onaylandı.");
      await loadAdvisor();
    }
  } catch (err) {
    button.disabled = false;
    announce(err.message, "error");
  }
}

export function wireAdvisor() {
  $("#view-advisor").addEventListener("click", onPanelClick);
  $("#view-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      await post("/bl-views", {
        symbol: f.symbol,
        expected_return: Number(f.expected_return),
        confidence: Number(f.confidence),
      });
      announce("Görüş kaydedildi.");
      await loadAdvisor();
    } catch (err) {
      announce(`Görüş kaydedilemedi: ${err.message}`, "error");
    }
  });
  $("#btn-suggest").addEventListener("click", async () => {
    try {
      await post("/bl-views/suggest");
      await loadAdvisor();
    } catch (err) {
      announce(err.message, "error");
    }
  });
  $("#btn-verify").addEventListener("click", async () => {
    const r = await api("/audit/verify");
    $("#verify-result").textContent = r.valid
      ? ` Zincir sağlam (${r.count} kayıt)`
      : ` Bozulma: kayıt #${r.broken_at}`;
  });
}

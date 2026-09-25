// Başlık rozetleri (GET /system/status) ve "Veri kalitesi" kartı (GET /data/quality).

import { api } from "../api.js";
import { $, esc, table } from "../dom.js";
import { qualityLabel, shortDate, dateTime } from "../format.js";

const DATA_MODE = {
  gercek: { text: "gerçek", cls: "live" },
  snapshot: { text: "snapshot", cls: "info" },
  yaklasik: { text: "yaklaşık", cls: "demo" },
};

const QUALITY_CLASS = { ok: "live", warning: "demo", error: "bad" };

const ISSUE_KIND = {
  gap: "boşluk",
  jump: "sıçrama",
  split: "bölünme",
  stale: "bayatlık",
};

function setBadge(el, text, cls, title) {
  el.textContent = text;
  el.className = `badge ${cls}`;
  el.title = title;
  el.setAttribute("aria-label", `${text}. ${title}`.trim());
  el.classList.remove("hidden");
}

export function describeDataMode(data) {
  const lines = [`Kaynak: ${data.source || "–"}`];
  if (data.snapshot_end) {
    lines.push(`Snapshot sonu: ${data.snapshot_end}`);
  }
  for (const [sym, until] of Object.entries(data.spliced_series || {})) {
    lines.push(`${sym}: ${until}'e kadar vekil`);
  }
  for (const sym of data.proxy_series || []) {
    lines.push(`${sym}: tamamı vekil seri`);
  }
  for (const key of data.approximate_macro || []) {
    lines.push(`${key}: yaklaşık (elle girilmiş) makro seri`);
  }
  for (const [key, source] of Object.entries(data.macro_sources || {})) {
    lines.push(`${key} kaynağı: ${source}`);
  }
  return lines.join("\n");
}

function renderProxyNote(data) {
  const note = $("#data-proxy-note");
  if (!note) {
    return;
  }
  const items = describeDataMode(data)
    .split("\n")
    .map((line) => `<li>${esc(line)}</li>`)
    .join("");
  note.innerHTML = `<h3>Veri kaynağı ve vekil dönemler</h3><ul class="list small">${items}</ul>`;
}

export async function loadBadges() {
  const status = await api("/system/status");
  const mode = DATA_MODE[status.data.mode] || { text: status.data.mode, cls: "info" };
  setBadge($("#badge-data"), `Veri: ${mode.text}`, mode.cls, describeDataMode(status.data));
  const q = status.quality || {};
  const counts = q.counts || {};
  const qTitle = `iyi ${counts.ok ?? 0} · uyarı ${counts.warning ?? 0} · hata ${counts.error ?? 0}`;
  const qCls = QUALITY_CLASS[q.status] || "info";
  setBadge($("#badge-quality"), `Veri kalitesi: ${qualityLabel(q.status)}`, qCls, qTitle);
  const live = status.ai?.mode === "live";
  const aiTitle = live ? "Yanıtlar canlı LLM ile üretiliyor" : "Deterministik demo yanıtları";
  setBadge($("#badge-ai"), live ? "AI: Canlı" : "AI: Demo", live ? "live" : "demo", aiTitle);
  renderProxyNote(status.data);
  return status;
}

function issuesCell(series) {
  const notes = (series.issues || []).map((i) => {
    const kind = ISSUE_KIND[i.kind] || i.kind;
    return `<li><b>${esc(kind)}</b> ${esc(i.date || "")} ${esc(i.detail || "")}</li>`;
  });
  if (series.proxy_until) {
    notes.unshift(`<li>${esc(series.proxy_until)} tarihine kadar vekil seri</li>`);
  } else if (series.is_proxy) {
    notes.unshift("<li>Tamamı vekil seri</li>");
  }
  return notes.length ? `<ul class="issues">${notes.join("")}</ul>` : "–";
}

function statusCell(series) {
  const cls = QUALITY_CLASS[series.status] || "info";
  return `<span class="badge ${cls}">${esc(qualityLabel(series.status))}</span>`;
}

export async function loadDataQuality() {
  const report = await api("/data/quality");
  const onlyIssues = $("#dq-only-issues")?.checked;
  const series = (report.series || []).filter((s) => !onlyIssues || s.status !== "ok");
  const c = report.counts || {};
  $("#dq-summary").textContent =
    `Genel durum: ${qualityLabel(report.status)} · iyi ${c.ok ?? 0}, uyarı ${c.warning ?? 0}, ` +
    `hata ${c.error ?? 0} · veri tarihi ${shortDate(report.as_of)} · ` +
    `kontrol ${dateTime(report.checked_at)}`;
  $("#dq-table").innerHTML = table(
    series,
    [
      ["Sembol", (s) => esc(s.symbol)],
      ["Kaynak", (s) => esc(s.source || "–")],
      ["İlk tarih", (s) => esc(shortDate(s.first_date))],
      ["Son tarih", (s) => esc(shortDate(s.last_date))],
      ["Satır", (s) => esc(s.rows ?? "–")],
      ["Durum", statusCell],
      ["Sorunlar", issuesCell],
    ],
    "Fiyat serilerinin kalite kontrolü",
  );
}

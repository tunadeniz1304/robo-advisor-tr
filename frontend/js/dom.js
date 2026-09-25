// DOM yardımcıları, HTML kaçışı ve ekran okuyucu duyuruları.

export function $(selector, root = document) {
  return root.querySelector(selector);
}

export function $$(selector, root = document) {
  return [...root.querySelectorAll(selector)];
}

const ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

// Basit HTML tablo üretici: cols = [[başlık, (satır) => güvenli HTML], ...]
export function table(rows, cols, caption = "") {
  const head = cols.map((c) => `<th scope="col">${esc(c[0])}</th>`).join("");
  const body = rows
    .map((r) => `<tr>${cols.map((c) => `<td>${c[1](r)}</td>`).join("")}</tr>`)
    .join("");
  const cap = caption ? `<caption>${esc(caption)}</caption>` : "";
  return `<table>${cap}<thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

let clearTimer = null;

// aria-live="polite" bölgesine durum ya da hata mesajı yazar.
export function announce(message, kind = "info") {
  const region = $("#app-status");
  if (!region) {
    return;
  }
  region.textContent = message;
  region.dataset.kind = kind;
  clearTimeout(clearTimer);
  if (message && kind !== "error") {
    clearTimer = setTimeout(() => announce(""), 6000);
  }
}

export function setBusy(button, busy) {
  if (!button) {
    return;
  }
  button.disabled = busy;
  button.setAttribute("aria-busy", busy ? "true" : "false");
}

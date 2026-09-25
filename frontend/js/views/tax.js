// Vergi (bilgi amaçlı): açık lotlar, gerçekleşen kâr, satış simülasyonu, zarar hasadı.

import { api, post } from "../api.js";
import { $, announce, esc, setBusy, table } from "../dom.js";
import { classLabel, num, shortDate, tl } from "../format.js";
import { state } from "../state.js";

function base() {
  return `/portfolios/${state.portfolioId}/tax`;
}

function signed(value) {
  const cls = value < 0 ? "neg" : "pos";
  return value == null ? "–" : `<span class="${cls}">${tl(value)}</span>`;
}

async function loadLots() {
  const method = $("#tax-method").value;
  const r = await api(`${base()}/lots?method=${method}`);
  $("#tax-lots").innerHTML = table(
    r.lots,
    [
      ["Sembol", (l) => esc(l.symbol)],
      ["Miktar", (l) => num(l.quantity, 4)],
      ["Birim maliyet", (l) => num(l.unit_cost)],
      ["Alış", (l) => esc(shortDate(l.acquired))],
      ["Gün", (l) => esc(l.holding_days)],
      ["Fiyat", (l) => num(l.price)],
      ["Gerçekleşmemiş K/Z", (l) => signed(l.unrealized)],
    ],
    `Açık lotlar (${r.method} sırasıyla)`,
  );
  const symbols = [...new Set(r.lots.map((l) => l.symbol))];
  const select = $("#tax-symbol");
  const current = select.value;
  select.innerHTML = symbols
    .map((s) => `<option value="${esc(s)}">${esc(s)}</option>`)
    .join("");
  if (symbols.includes(current)) {
    select.value = current;
  }
  $("#tax-note").textContent = r.note || "";
}

async function loadRealized() {
  const method = $("#tax-method").value;
  const year = Number($("#tax-year").value) || new Date().getFullYear();
  const r = await api(`${base()}/realized?year=${year}&method=${method}`);
  const classes = Object.entries(r.by_class || {});
  const detail = classes.length
    ? table(
        classes,
        [
          ["Sınıf", ([key]) => esc(classLabel(key))],
          ["Kâr/zarar", ([, c]) => signed(c.gain)],
          ["Tahmini stopaj", ([, c]) => tl(c.tax)],
        ],
        "Sınıf bazında gerçekleşen",
      )
    : `<p class="muted">${year} yılında gerçekleşen satış yok.</p>`;
  $("#tax-realized").innerHTML =
    `<p>${year}: toplam gerçekleşen ${signed(r.total_gain)} · ` +
    `tahmini stopaj ${tl(r.estimated_tax)}</p>${detail}`;
}

async function loadHarvest() {
  const method = $("#tax-method").value;
  const r = await api(`${base()}/harvest?method=${method}`);
  const classes = Object.entries(r.by_class || {});
  const byClass = table(
    classes,
    [
      ["Sınıf", ([key]) => esc(classLabel(key))],
      ["Gerçekleşmemiş zarar", ([, c]) => signed(c.unrealized_loss)],
      ["Yıl içi gerçekleşen kâr", ([, c]) => tl(c.realized_gain_ytd)],
      ["Mahsup edilebilir", ([, c]) => tl(c.offsettable)],
      ["Tahmini tasarruf", ([, c]) => tl(c.estimated_saving)],
    ],
    "Sınıf bazında zarar hasadı",
  );
  const candidates = table(
    (r.candidates || []).slice(0, 10),
    [
      ["Sembol", (c) => esc(c.symbol)],
      ["Lot", (c) => esc(c.lot_id)],
      ["Miktar", (c) => num(c.quantity, 4)],
      ["Birim maliyet", (c) => num(c.unit_cost)],
      ["Fiyat", (c) => num(c.price)],
      ["Zarar", (c) => signed(c.unrealized_loss)],
    ],
    "Zararda olan lotlar (ilk 10)",
  );
  $("#tax-harvest").innerHTML =
    `<p>Toplam tahmini tasarruf: <b>${tl(r.total_estimated_saving)}</b></p>` +
    `${byClass}${candidates}<p class="muted">${esc(r.note || "")}</p>`;
}

export async function loadTax() {
  if (!state.portfolioId) {
    return;
  }
  if (!$("#tax-year").value) {
    $("#tax-year").value = String(new Date().getFullYear());
  }
  await Promise.all([loadLots(), loadRealized(), loadHarvest()]);
}

async function simulateSale(e) {
  e.preventDefault();
  const button = e.target.querySelector("button[type=submit]");
  const symbol = $("#tax-symbol").value;
  const quantity = Number($("#tax-qty").value);
  setBusy(button, true);
  try {
    const r = await post(`${base()}/simulate-sale`, { symbol, quantity });
    $("#tax-sim").innerHTML =
      `<p>${esc(r.symbol)} · fiyat ${num(r.price)}</p>` +
      table(
        Object.entries(r.methods || {}),
        [
          ["Yöntem", ([m]) => esc(m)],
          ["Kâr/zarar", ([, v]) => signed(v.gain)],
          ["Tahmini stopaj", ([, v]) => tl(v.tax)],
          ["Lot dışı miktar", ([, v]) => num(v.untracked_quantity, 4)],
          ["Lot sayısı", ([, v]) => esc(v.slices?.length ?? 0)],
        ],
        "FIFO / HIFO karşılaştırması",
      );
    announce("Satış simülasyonu hazır.");
  } catch (err) {
    announce(`Simülasyon başarısız: ${err.message}`, "error");
  } finally {
    setBusy(button, false);
  }
}

export function wireTax() {
  $("#tax-method").addEventListener("change", () => {
    loadTax().catch((err) => announce(err.message, "error"));
  });
  $("#tax-year").addEventListener("change", () => {
    loadRealized().catch((err) => announce(err.message, "error"));
  });
  $("#tax-sale-form").addEventListener("submit", simulateSale);
}

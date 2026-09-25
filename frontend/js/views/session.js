// Giriş, oturum kurulumu ve (danışman için) müşteri seçimi.

import { api, loadStoredTokens, logout, post, saveTokens } from "../api.js";
import { $, $$, announce, esc } from "../dom.js";
import { currentView, show } from "../router.js";
import { isStaff, resetCustomerContext, state } from "../state.js";
import { loadBadges } from "./status.js";

export async function selectCustomer(id) {
  state.customerId = Number(id) || null;
  resetCustomerContext();
  if (!state.customerId) {
    return;
  }
  const portfolios = await api(`/portfolios?customer_id=${state.customerId}`);
  state.portfolioId = portfolios[0]?.id ?? null;
}

async function loadMethods() {
  try {
    state.methods = await api("/optimization/methods");
  } catch {
    state.methods = {};
  }
  const options = Object.entries(state.methods)
    .map(([key, label]) => `<option value="${esc(key)}">${esc(label)}</option>`)
    .join("");
  $$("select[data-methods]").forEach((select) => {
    const placeholder = select.dataset.methods;
    const first = placeholder ? `<option value="">${esc(placeholder)}</option>` : "";
    select.innerHTML = first + options;
  });
}

async function loadCustomers() {
  const customers = await api("/customers");
  $("#customer-select").innerHTML = customers
    .map((c) => `<option value="${c.id}">${esc(c.full_name)}</option>`)
    .join("");
  return customers[0]?.id ?? null;
}

export async function afterLogin() {
  state.me = await api("/auth/me");
  $("#view-login").classList.add("hidden");
  $("#nav").classList.remove("hidden");
  $("#btn-logout").classList.remove("hidden");
  $("#user-name").textContent = state.me.display_name || state.me.username;
  const staff = isStaff();
  $$(".staff-only").forEach((e) => e.classList.toggle("hidden", !staff));
  loadBadges().catch((err) => announce(`Durum rozetleri alınamadı: ${err.message}`, "error"));
  await loadMethods();
  const customerId = staff ? await loadCustomers() : state.me.customer_id;
  await selectCustomer(customerId);
  await show("dashboard", { focus: true });
}

async function onLoginSubmit(e) {
  e.preventDefault();
  const form = new FormData(e.target);
  const error = $("#login-error");
  error.textContent = "";
  try {
    const tokens = await post("/auth/login", {
      username: form.get("username"),
      password: form.get("password"),
    });
    saveTokens(tokens);
  } catch {
    error.textContent = "Giriş başarısız: kullanıcı adı veya şifre hatalı.";
    return;
  }
  try {
    await afterLogin();
  } catch (err) {
    error.textContent = `Oturum açıldı ama veriler yüklenemedi: ${err.message}`;
  }
}

export function wireSession() {
  $("#login-form").addEventListener("submit", onLoginSubmit);
  $("#btn-logout").addEventListener("click", logout);
  $("#customer-select").addEventListener("change", async (e) => {
    await selectCustomer(e.target.value);
    await show(currentView());
  });
}

export async function restoreSession() {
  const tokens = loadStoredTokens();
  if (!tokens) {
    $("#login-form input[name=username]")?.focus();
    return;
  }
  saveTokens(tokens);
  try {
    await afterLogin();
  } catch {
    state.token = null;
    announce("Oturum süresi doldu, lütfen yeniden giriş yapın.");
  }
}

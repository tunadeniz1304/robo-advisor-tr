// Otonom Finansal Danışman — bağımlılıksız SPA giriş noktası (ES modules + Chart.js).
// CSP uyumlu: satır içi script/olay yok; tüm olaylar addEventListener ile bağlanır.

import { $, $$, announce } from "./dom.js";
import { registerView, show, wireNavKeyboard } from "./router.js";
import { loadAdvisor, wireAdvisor } from "./views/advisor.js";
import { loadAnalytics, wireAnalytics } from "./views/analytics.js";
import { wireCopilot } from "./views/copilot.js";
import { loadDashboard, wireDashboard } from "./views/dashboard.js";
import { loadGoals, wireGoals } from "./views/goals.js";
import { loadQuestionnaire, wireOnboarding } from "./views/onboarding.js";
import { loadModels, wireModels } from "./views/models.js";
import { loadRebalance, wireRebalance } from "./views/rebalance.js";
import { loadRisk } from "./views/risk.js";
import { restoreSession, wireSession } from "./views/session.js";
import { loadBadges, loadDataQuality } from "./views/status.js";
import { loadStress, wireStress } from "./views/stress.js";
import { loadTax, wireTax } from "./views/tax.js";

const THEME_KEY = "of.theme";

function applyStoredTheme() {
  const root = document.documentElement;
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored) {
      root.dataset.theme = stored;
    } else if (matchMedia("(prefers-color-scheme: dark)").matches) {
      root.dataset.theme = "dark";
    }
  } catch {
    // localStorage kullanılamıyor: varsayılan tema
  }
  syncThemeButton();
}

function syncThemeButton() {
  const dark = document.documentElement.dataset.theme === "dark";
  $("#btn-theme").setAttribute("aria-pressed", dark ? "true" : "false");
}

function toggleTheme() {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  syncThemeButton();
  try {
    localStorage.setItem(THEME_KEY, root.dataset.theme);
  } catch {
    // yok say
  }
}

async function loadDataView() {
  await Promise.all([loadBadges(), loadDataQuality()]);
}

function registerViews() {
  registerView("dashboard", loadDashboard);
  registerView("onboarding", loadQuestionnaire);
  registerView("goals", loadGoals);
  registerView("rebalance", loadRebalance);
  registerView("analytics", loadAnalytics);
  registerView("risk", loadRisk);
  registerView("stress", loadStress);
  registerView("tax", loadTax);
  registerView("models", loadModels);
  registerView("data", loadDataView);
  registerView("advisor", loadAdvisor);
}

function wire() {
  $("#btn-theme").addEventListener("click", toggleTheme);
  const nav = $("#nav");
  $$("button", nav).forEach((b) => {
    b.addEventListener("click", () => show(b.dataset.view, { focus: true }));
  });
  wireNavKeyboard(nav);
  $$("[data-goto]").forEach((el) => {
    el.addEventListener("click", () => show(el.dataset.goto, { focus: true }));
  });
  $("#dq-only-issues").addEventListener("change", () => {
    loadDataQuality().catch((err) => announce(err.message, "error"));
  });
  wireSession();
  wireDashboard();
  wireOnboarding();
  wireGoals();
  wireRebalance();
  wireAnalytics();
  wireStress();
  wireTax();
  wireModels();
  wireCopilot();
  wireAdvisor();
}

window.addEventListener("unhandledrejection", (e) => {
  const message = e.reason?.message || String(e.reason);
  announce(`Beklenmeyen hata: ${message}`, "error");
});

applyStoredTheme();
registerViews();
wire();
restoreSession();

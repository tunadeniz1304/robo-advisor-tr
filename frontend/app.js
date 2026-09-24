// Otonom Finansal Danışman — bağımlılıksız SPA (vanilla ES modules + Chart.js).
// CSP uyumlu: inline script/olay yok; tüm olaylar addEventListener ile bağlanır.
const API = "/api/v1";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const nf = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });
const tl = (v) => (v == null ? "–" : `${nf.format(v)} TL`);
const pct = (v, d = 1) => (v == null ? "–" : `%${(v * 100).toLocaleString("tr-TR", { minimumFractionDigits: d, maximumFractionDigits: d })}`);
const state = { token: null, refresh: null, me: null, customerId: null, portfolioId: null, charts: {}, goal: null, q: { list: [], i: 0, answers: {} } };

// ------------------------------------------------------------------ API
async function api(path, opts = {}, retry = true) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(API + path, { ...opts, headers });
  if (res.status === 401 && retry && state.refresh) {
    const r = await fetch(`${API}/auth/refresh`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: state.refresh }) });
    if (r.ok) { saveTokens(await r.json()); return api(path, opts, false); }
    logout();
  }
  const ct = res.headers.get("content-type") || "";
  const body = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) { const err = new Error(typeof body === "object" ? JSON.stringify(body.detail) : body); err.status = res.status; err.body = body; throw err; }
  return body;
}
const post = (p, b) => api(p, { method: "POST", body: JSON.stringify(b ?? {}) });

function saveTokens(t) { state.token = t.access_token; state.refresh = t.refresh_token; try { sessionStorage.setItem("of.tokens", JSON.stringify(t)); } catch { /* yok say */ } }
function logout() { state.token = null; try { sessionStorage.removeItem("of.tokens"); } catch { /* yok say */ } location.reload(); }

// ------------------------------------------------------------------ UI helpers
function chart(id, config) {
  if (!window.Chart) return;
  state.charts[id]?.destroy();
  const dark = document.documentElement.dataset.theme === "dark";
  window.Chart.defaults.color = dark ? "#98a2b3" : "#667085";
  state.charts[id] = new window.Chart($(`#${id}`), config);
}
const palette = ["#1f5eff", "#12b76a", "#f79009", "#7a5af8", "#ee46bc", "#06aed4", "#d92d20", "#667085", "#84cc16", "#0e9384"];
function show(view) {
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  ({ dashboard: loadDashboard, onboarding: loadQuestionnaire, goals: loadGoals, rebalance: loadRebalance, analytics: loadAnalytics, stress: loadStress, advisor: loadAdvisor })[view]?.();
}
function table(rows, cols) {
  return `<table><thead><tr>${cols.map((c) => `<th>${esc(c[0])}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td>${c[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
async function badges() {
  try {
    const [llm, data] = await Promise.all([fetch(`${API}/llm/status`).then((r) => r.json()), fetch(`${API}/market/status`).then((r) => r.json())]);
    const ai = $("#badge-ai"); ai.textContent = llm.mode === "live" ? "AI: Canlı" : "AI: Demo"; ai.className = `badge ${llm.mode === "live" ? "live" : "demo"}`;
    const d = $("#badge-data"); const live = data.source === "live" || data.source === "mixed";
    d.textContent = live ? "Veri: Canlı" : "Veri: Önbellek"; d.className = `badge ${live ? "live" : "demo"}`;
  } catch { /* rozetler opsiyonel */ }
}

// ------------------------------------------------------------------ session
async function afterLogin() {
  state.me = await api("/auth/me");
  $("#view-login").classList.add("hidden"); $("#nav").classList.remove("hidden"); $("#btn-logout").classList.remove("hidden");
  const staff = state.me.role !== "musteri";
  $$(".staff-only").forEach((e) => e.classList.toggle("hidden", !staff));
  if (staff) {
    const customers = await api("/customers");
    $("#customer-select").innerHTML = customers.map((c) => `<option value="${c.id}">${esc(c.full_name)}</option>`).join("");
    state.customerId = customers[0]?.id ?? null;
  } else state.customerId = state.me.customer_id;
  await selectCustomer(state.customerId);
  show("dashboard");
}
async function selectCustomer(id) {
  state.customerId = Number(id) || null; state.portfolioId = null;
  if (!state.customerId) return;
  const ps = await api(`/portfolios?customer_id=${state.customerId}`);
  state.portfolioId = ps[0]?.id ?? null;
}

// ------------------------------------------------------------------ dashboard
async function loadDashboard() {
  if (!state.portfolioId) return;
  const [val, prof, goals, nudges] = await Promise.all([
    api(`/portfolios/${state.portfolioId}/valuation`), api(`/customers/${state.customerId}/risk-profile`),
    api(`/customers/${state.customerId}/goals`), api(`/customers/${state.customerId}/nudges`),
  ]);
  $("#kpi-value").textContent = tl(val.total_value); $("#kpi-cash").textContent = tl(val.cash);
  $("#kpi-level").textContent = `${prof.effective.level}/10 · ${prof.effective.label}`;
  api(`/portfolios/${state.portfolioId}/report`).then((r) => { $("#kpi-twr").textContent = pct(r.twr_cumulative); }).catch(() => {});
  const items = val.items.filter((i) => i.market_value > 0);
  chart("chart-allocation", { type: "doughnut", data: { labels: [...items.map((i) => i.ticker), "Nakit"], datasets: [{ data: [...items.map((i) => i.market_value), val.cash], backgroundColor: palette }] }, options: { plugins: { legend: { position: "right" } } } });
  $("#goal-rings").innerHTML = goals.map((g, k) => `<div class="ring"><canvas id="ring-${k}"></canvas><div>${esc(g.name)}<br><b>${pct(g.last_simulation?.success_probability, 0)}</b></div></div>`).join("") || '<p class="muted">Henüz hedef yok.</p>';
  goals.forEach((g, k) => { const p = g.last_simulation?.success_probability ?? 0; chart(`ring-${k}`, { type: "doughnut", data: { datasets: [{ data: [p, 1 - p], backgroundColor: [palette[1], "#e4e7ec"] }] }, options: { cutout: "70%", plugins: { tooltip: { enabled: false } } } }); });
  $("#nudges").innerHTML = nudges.map((n) => `<li class="sev-${n.severity}"><b>${esc(n.title)}</b><br>${esc(n.message)} <button class="btn ghost" data-dismiss="${n.id}" type="button">Kapat</button></li>`).join("") || '<li class="muted">Bildirim yok.</li>';
  post("/commentary/market").then((c) => { $("#commentary").textContent = `${c.baslik}\n${c.ozet}\n• ${c.maddeler.join("\n• ")}\n\n${c.disclaimer}`; }).catch(() => {});
}

// ------------------------------------------------------------------ onboarding
async function loadQuestionnaire() {
  if (!state.q.list.length) state.q.list = (await api("/suitability/questionnaire")).questions;
  renderQuestion();
}
function renderQuestion() {
  const { list, i, answers } = state.q; const q = list[i];
  $("#q-progress").style.width = `${((i + 1) / list.length) * 100}%`;
  $("#q-body").innerHTML = `<p class="muted">${esc(q.section)} · ${i + 1}/${list.length}</p><h3>${esc(q.text)}</h3>${q.options.map((o) => `<label class="option"><input type="radio" name="opt" value="${esc(o.value)}" ${answers[q.id] === o.value ? "checked" : ""}> ${esc(o.label)}</label>`).join("")}${q.help ? `<p class="muted">${esc(q.help)}</p>` : ""}`;
  $("#q-next").textContent = i === list.length - 1 ? "Gönder" : "İleri";
}
async function submitProfile(confirm = false) {
  try {
    const r = await post(`/customers/${state.customerId}/risk-profile`, { answers: state.q.answers, confirm_inconsistencies: confirm });
    $("#q-result").textContent = `Risk seviyeniz: ${r.profile.risk_level}/10 (${r.profile.risk_label})\n${r.result.explanation.join("\n")}`;
  } catch (e) {
    if (e.status === 409) {
      const d = e.body.detail; $("#q-result").innerHTML = `<p class="error">${esc(d.message)}</p><p>${d.result.warnings.map((w) => esc(w.message)).join("<br>")}</p><button id="q-confirm" class="btn" type="button">Cevaplarımı onaylıyorum</button>`;
      $("#q-confirm").addEventListener("click", () => submitProfile(true));
      state.q.i = state.q.list.findIndex((q) => d.reask.includes(q.id)); renderQuestion();
    } else $("#q-result").textContent = e.message;
  }
}

// ------------------------------------------------------------------ goals
async function loadGoals() {
  const goals = await api(`/customers/${state.customerId}/goals`);
  $("#goal-list").innerHTML = goals.map((g) => `<li><b>${esc(g.name)}</b> (${esc(g.goal_type_label)}) · hedef ${tl(g.target_amount_real)} · ${g.horizon_years} yıl <button class="btn" data-sim="${g.id}" type="button">Simüle et</button></li>`).join("") || '<li class="muted">Hedef ekleyin.</li>';
  if (goals[0]) simulate(goals[0].id);
}
async function simulate(id) {
  state.goal = id;
  const r = await post(`/goals/${id}/simulate?with_report=true`);
  const s = r.result; $("#goal-title").textContent = `Simülasyon — ${r.goal.name}`;
  $("#goal-summary").textContent = `Başarı olasılığı ${pct(s.success_probability)} · Medyan (reel) ${tl(s.p50_real)} · Gerekli aylık katkı ${tl(s.required_monthly_contribution)}\n${r.report?.ozet ?? ""}\n${(s.what_if || []).map((w) => `• ${w.aciklama}: ${pct(w.basari_olasiligi)}`).join("\n")}`;
  const labels = s.bands.map((b) => `${Math.round(b.month / 12)}. yıl`);
  chart("chart-fan", { type: "line", data: { labels, datasets: [
    { label: "P90", data: s.bands.map((b) => b.p90), borderColor: palette[1], fill: false, pointRadius: 0 },
    { label: "P50 (reel)", data: s.bands.map((b) => b.p50), borderColor: palette[0], fill: false, pointRadius: 0 },
    { label: "P10", data: s.bands.map((b) => b.p10), borderColor: palette[6], fill: false, pointRadius: 0 },
    { label: "Hedef", data: s.bands.map(() => r.goal.target_amount_real), borderColor: palette[7], borderDash: [6, 4], pointRadius: 0 }] } });
}
let wiTimer;
function whatIf() {
  ["horizon", "contrib", "level"].forEach((k) => { $(`#wi-${k}-o`).textContent = $(`#wi-${k}`).value; });
  clearTimeout(wiTimer);
  wiTimer = setTimeout(async () => {
    if (!state.goal) return;
    const r = await post(`/goals/${state.goal}/what-if`, { horizon_delta_years: Number($("#wi-horizon").value), contribution_multiplier: Number($("#wi-contrib").value), level_delta: Number($("#wi-level").value) });
    $("#whatif-result").textContent = `Senaryo başarı olasılığı ${pct(r.scenario.success_probability)} (fark ${pct(r.delta_success_probability)})`;
  }, 350);
}

// ------------------------------------------------------------------ rebalance
async function loadRebalance() {
  const pending = await api(`/proposals?portfolio_id=${state.portfolioId}&status=ONAY_BEKLIYOR`);
  if (pending[0]) renderProposal(pending[0]); else $("#proposal").innerHTML = '<p class="muted">Bekleyen öneri yok.</p>';
}
function renderProposal(p) {
  const cards = (p.explanation || []).map((c) => `<div class="why"><b>${esc(c.baslik)}</b>${esc(c.metin)}</div>`).join("");
  const w = Object.keys({ ...p.before_weights, ...p.after_weights });
  $("#proposal").innerHTML = `<p><b>Öneri #${p.id}</b> · ${esc(p.status)} · ${esc(p.method_label || p.source)} · son geçerlilik ${esc(p.expires_at?.slice(0, 16))}</p>
    <div class="cards">${cards}</div>
    <div class="table-wrap">${table(p.orders, [["Sembol", (o) => esc(o.symbol)], ["Yön", (o) => (o.side === "BUY" ? "Alış" : "Satış")], ["Tutar", (o) => tl(o.amount)], ["Maliyet", (o) => tl(o.cost?.total)]])}</div>
    <p>Tahmini maliyet ${tl(p.estimated_cost)} · tahmini stopaj ${tl(p.estimated_tax)} · devir ${pct(p.turnover)} · oynaklık ${pct(p.risk_before?.volatility)} → ${pct(p.risk_after?.volatility)}</p>
    <div class="table-wrap">${table(w.map((s) => [s, p.before_weights[s] || 0, p.after_weights[s] || 0]), [["Varlık", (r) => esc(r[0])], ["Önce", (r) => pct(r[1])], ["Sonra", (r) => pct(r[2])]])}</div>
    <p class="prose">${esc(p.rationale)}</p><p class="muted">${esc(p.disclaimer)}</p>
    ${p.status === "ONAY_BEKLIYOR" ? `<div class="actions"><button class="btn ok" data-approve="${p.id}" type="button">Onayla ve yürüt</button><button class="btn" data-reject="${p.id}" type="button">Reddet</button></div>` : ""}`;
}

// ------------------------------------------------------------------ analytics & stress
async function loadAnalytics() {
  const r = await api(`/portfolios/${state.portfolioId}/report`); const t = r.tear_sheet || {};
  const per = r.period || {}; const rows = [["Dönem", `${per.start || "—"} → ${per.end || "—"}`], ["TWR (kümülatif)", pct(r.twr_cumulative)], ["TWR (yıllık)", r.twr_annualized == null ? "— (<1 yıl)" : pct(r.twr_annualized)], ["MWR (yıllık, XIRR)", r.mwr_annualized == null ? "— (<1 yıl)" : pct(r.mwr_annualized)], ["Risksiz faiz (TL)", pct(t.risk_free_rate)], ["Volatilite", pct(t.volatility)], ["Sharpe", t.sharpe?.toFixed(2)], ["Sortino", t.sortino?.toFixed(2)], ["Maks. düşüş", pct(t.max_drawdown)], ["VaR %95 (günlük)", pct(t.var_95_hist)], ["CVaR %95", pct(t.cvar_95_hist)], ["BIST CAGR", pct(t.benchmark?.cagr)], ["Yıllık TÜFE", pct(r.inflation_yoy)]];
  $("#tearsheet").innerHTML = table(rows, [["Metrik", (x) => esc(x[0])], ["Değer", (x) => esc(x[1])]]);
  chart("chart-value", { type: "line", data: { labels: r.value_curve.map((v) => v.date), datasets: [{ label: "Portföy değeri", data: r.value_curve.map((v) => v.value), borderColor: palette[0], pointRadius: 0 }] } });
}
async function runBacktest() {
  const r = await post("/backtest", { customer_id: state.customerId, years: 5 });
  const names = { none: "Rebalance yok", calendar: "Takvim", band: "Bant" };
  $("#backtest").innerHTML = table(Object.entries(r.results), [["Politika", ([k]) => names[k]], ["CAGR", ([, v]) => pct(v.stats.cagr)], ["Volatilite", ([, v]) => pct(v.stats.volatility)], ["Maks. düşüş", ([, v]) => pct(v.stats.max_drawdown)], ["Rebalance", ([, v]) => v.rebalances], ["Maliyet", ([, v]) => tl(v.total_cost)]]);
  const first = Object.values(r.results)[0];
  chart("chart-backtest", { type: "line", data: { labels: first.equity_curve.map((e) => e.date), datasets: Object.entries(r.results).map(([k, v], i) => ({ label: names[k], data: v.equity_curve.map((e) => e.value), borderColor: palette[i], pointRadius: 0 })) } });
}
async function loadStress() {
  const c = await api("/stress/scenarios");
  $("#stress-scenario").innerHTML = [...Object.entries(c.factor), ...Object.entries(c.historical)].map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join("");
}
async function runStress(body) {
  const r = await post(`/portfolios/${state.portfolioId}/stress`, body);
  $("#stress-result").innerHTML = `<p><b>${esc(r.senaryo_adi)}</b>: ${tl(r.pnl)} (${pct(r.pnl_orani)}) · maks. düşüş ${pct(r.maks_dusus)}</p>${(r.hedef_etkisi || []).map((h) => `<p>Hedef "${esc(h.ad)}": ${pct(h.once)} → ${pct(h.sonra)}</p>`).join("")}`;
  const k = Object.entries(r.katkilar);
  chart("chart-stress", { type: "bar", data: { labels: k.map((x) => x[0]), datasets: [{ label: "P&L (TL)", data: k.map((x) => x[1]), backgroundColor: k.map((x) => (x[1] < 0 ? palette[6] : palette[1])) }] }, options: { indexAxis: "y" } });
}

// ------------------------------------------------------------------ copilot (SSE)
function msg(cls, text) { const d = document.createElement("div"); d.className = `msg ${cls}`; d.textContent = text; $("#chat").append(d); $("#chat").scrollTop = 1e9; return d; }
async function ask(question) {
  msg("user", question); const bot = msg("bot", "");
  const res = await fetch(`${API}/copilot/chat`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token}` }, body: JSON.stringify({ message: question, customer_id: state.customerId, portfolio_id: state.portfolioId }) });
  if (!res.ok) { bot.textContent = "Copilot yanıt veremedi."; return; }
  const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
  for (;;) {
    const { value, done } = await reader.read(); if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
      const ev = /event: (\w+)/.exec(chunk)?.[1]; const data = JSON.parse(chunk.split("data: ")[1] || "null");
      if (ev === "step") $("#chat").insertBefore(Object.assign(document.createElement("div"), { className: "msg step", textContent: `🔧 ${data.tool}` }), bot);
      else if (ev === "token") bot.textContent += data;
      else if (ev === "done") bot.title = `LLM: ${data.llm_mode}`;
    }
  }
}

// ------------------------------------------------------------------ advisor
async function loadAdvisor() {
  const [queue, views, audit] = await Promise.all([api("/proposals?status=ONAY_BEKLIYOR"), api("/bl-views"), api("/audit?limit=30")]);
  $("#queue").innerHTML = queue.map((p) => `<li>#${p.id} · müşteri ${p.customer_id} · ${p.orders.length} emir · ${esc(p.source)} <button class="btn ok" data-approve="${p.id}" type="button">Onayla</button> <button class="btn" data-show="${p.id}" type="button">İncele</button></li>`).join("") || '<li class="muted">Kuyruk boş.</li>';
  $("#views").innerHTML = views.map((v) => `<li>${esc(v.symbol)} · ${pct(v.expected_return)} · güven ${pct(v.confidence, 0)} · ${esc(v.source)} · <b>${esc(v.status)}</b> ${v.status === "onerildi" ? `<button class="btn" data-view-approve="${v.id}" type="button">Onayla</button>` : ""}</li>`).join("");
  $("#audit").innerHTML = audit.map((a) => `<li>${esc(a.created_at.slice(0, 19))} · ${esc(a.actor)} · <b>${esc(a.action)}</b> · ${esc(a.entity_type)} ${esc(a.entity_id)} · <code>${esc(a.hash.slice(0, 10))}…</code></li>`).join("");
}

// ------------------------------------------------------------------ wiring
function wire() {
  $("#btn-theme").addEventListener("click", () => { const d = document.documentElement; d.dataset.theme = d.dataset.theme === "dark" ? "light" : "dark"; try { localStorage.setItem("of.theme", d.dataset.theme); } catch { /* yok say */ } });
  $("#btn-logout").addEventListener("click", logout);
  $$("#nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
  $("#customer-select").addEventListener("change", async (e) => { await selectCustomer(e.target.value); loadDashboard(); });
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault(); const f = new FormData(e.target);
    try { saveTokens(await post("/auth/login", { username: f.get("username"), password: f.get("password") })); await afterLogin(); } catch (err) { $("#login-error").textContent = "Giriş başarısız: kullanıcı adı veya şifre hatalı."; }
  });
  $("#q-body").addEventListener("change", (e) => { if (e.target.name === "opt") state.q.answers[state.q.list[state.q.i].id] = e.target.value; });
  $("#q-prev").addEventListener("click", () => { state.q.i = Math.max(0, state.q.i - 1); renderQuestion(); });
  $("#q-next").addEventListener("click", () => { const q = state.q.list[state.q.i]; if (!state.q.answers[q.id]) return; if (state.q.i < state.q.list.length - 1) { state.q.i += 1; renderQuestion(); } else submitProfile(); });
  $("#goal-form").addEventListener("submit", async (e) => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); ["target_amount_real", "horizon_years", "initial_amount", "monthly_contribution"].forEach((k) => { f[k] = Number(f[k] || 0); }); await post(`/customers/${state.customerId}/goals`, f); e.target.reset(); loadGoals(); });
  ["horizon", "contrib", "level"].forEach((k) => $(`#wi-${k}`).addEventListener("input", whatIf));
  $("#btn-propose").addEventListener("click", async () => {
    const m = $("#opt-method").value; const r = await post(`/advisor/rebalance/${state.portfolioId}?customer_id=${state.customerId}${m ? `&method=${m}` : ""}`);
    if (!r.needs_rebalance) $("#proposal").innerHTML = `<p>${esc(r.message)}</p>`; else renderProposal(r.proposal);
  });
  document.body.addEventListener("click", async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.approve) { t.disabled = true; const p = await api(`/proposals/${t.dataset.approve}/approve`, { method: "POST", headers: { "Idempotency-Key": `ui-${t.dataset.approve}` } }); renderProposal(p); if (!$("#view-advisor").classList.contains("hidden")) loadAdvisor(); }
    if (t.dataset.reject) renderProposal(await post(`/proposals/${t.dataset.reject}/reject`, { reason: "Kullanıcı reddetti" }));
    if (t.dataset.show) { show("rebalance"); renderProposal(await api(`/proposals/${t.dataset.show}`)); }
    if (t.dataset.sim) simulate(t.dataset.sim);
    if (t.dataset.dismiss) { await post(`/nudges/${t.dataset.dismiss}/dismiss`); loadDashboard(); }
    if (t.dataset.viewApprove) { await post(`/bl-views/${t.dataset.viewApprove}/approve`); loadAdvisor(); }
    if (t.classList.contains("chip")) ask(t.textContent);
  });
  $("#btn-backtest").addEventListener("click", runBacktest);
  $("#btn-stress").addEventListener("click", () => runStress({ scenario: $("#stress-scenario").value }));
  $("#btn-stress-custom").addEventListener("click", () => runStress({ custom: { bist: +$("#sc-bist").value, usdtry: +$("#sc-usd").value, gold: +$("#sc-gold").value, rate: +$("#sc-rate").value } }));
  $("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#chat-input").value.trim(); if (q) { $("#chat-input").value = ""; ask(q); } });
  $("#view-form").addEventListener("submit", async (e) => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); await post("/bl-views", { symbol: f.symbol, expected_return: Number(f.expected_return), confidence: Number(f.confidence) }); loadAdvisor(); });
  $("#btn-suggest").addEventListener("click", async () => { await post("/bl-views/suggest"); loadAdvisor(); });
  $("#btn-verify").addEventListener("click", async () => { const r = await api("/audit/verify"); $("#verify-result").textContent = r.valid ? ` ✔ Zincir sağlam (${r.count} kayıt)` : ` ✖ Bozulma: kayıt #${r.broken_at}`; });
}

(async function init() {
  try { const th = localStorage.getItem("of.theme"); if (th) document.documentElement.dataset.theme = th; else if (matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.dataset.theme = "dark"; } catch { /* yok say */ }
  wire(); badges();
  try { const t = JSON.parse(sessionStorage.getItem("of.tokens") || "null"); if (t) { saveTokens(t); await afterLogin(); } } catch { state.token = null; }
})();

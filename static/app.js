// Otonom Finansal Danışman — yönetim paneli istemcisi (vanilla JS)
const API = '/api/v1';
const state = { customers: [], selected: null };

const qs = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function api(path, opts = {}) {
  const res = await fetch(API + path, opts);
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
  return data;
}

function setStatus(html, cls) {
  const el = qs('#api-status');
  el.textContent = html;
  el.className = 'badge ' + cls;
}

async function loadCustomers() {
  try {
    state.customers = await api('/customers?limit=500');
    setStatus('API çevrimiçi', 'badge--ok');
  } catch (e) {
    setStatus('API erişilemiyor', 'badge--err');
  }
  renderCustomers();
}

function renderCustomers() {
  const list = qs('#customer-list');
  list.innerHTML = state.customers.map((c) =>
    '<li class="' + (state.selected && state.selected.id === c.id ? 'active' : '') + '" data-id="' + c.id + '">'
    + '<div><div class="customer-name">' + esc(c.full_name) + '</div>'
    + '<div class="customer-count">' + esc(c.email) + '</div></div>'
    + '<span class="muted small">#' + c.id + '</span></li>'
  ).join('');
  list.querySelectorAll('li').forEach((li) =>
    li.addEventListener('click', () => selectCustomer(Number(li.dataset.id)))
  );
}

async function selectCustomer(id) {
  state.selected = state.customers.find((c) => c.id === id) || null;
  renderCustomers();
  if (!state.selected) return;
  qs('#empty-state').classList.add('hidden');
  qs('#customer-detail').classList.remove('hidden');
  qs('#customer-name').textContent = state.selected.full_name;
  qs('#customer-email').textContent = state.selected.email;
  await Promise.all([loadRisk(id), loadPortfolios(id), loadRuns()]);
}

async function loadRisk(customerId) {
  const box = qs('#risk-box');
  try {
    const r = await api('/advisor/risk/' + customerId);
    box.classList.remove('hidden');
    qs('#risk-score').textContent = r.score.toFixed(2);
    qs('#risk-category').textContent = r.category;
    qs('#risk-ceiling').textContent = Math.round(r.max_equity_weight * 100) + '%';
    qs('#risk-rationale').textContent = r.rationale;
    qs('#risk-chip').textContent = 'Risk: ' + r.category;
  } catch {
    box.classList.add('hidden');
    qs('#risk-chip').textContent = 'Risk: -';
  }
}

async function loadPortfolios(customerId) {
  const list = qs('#portfolio-list');
  list.innerHTML = '<p class="muted small">Yükleniyor…</p>';
  try {
    const portfolios = await api('/portfolios?customer_id=' + customerId + '&limit=200');
    list.innerHTML = '';
    for (const p of portfolios) {
      const div = document.createElement('div');
      div.className = 'portfolio-card';
      div.innerHTML = portfolioCardHTML(p);
      list.appendChild(div);
      div.querySelector('.btn-rebalance').addEventListener('click', () => rebalance(p));
      div.querySelector('.btn-valuation').addEventListener('click', () => showValuation(p));
    }
  } catch (e) {
    list.innerHTML = '<p class="muted small">' + esc(e.message) + '</p>';
  }
}

function portfolioCardHTML(p) {
  const keys = Object.keys(p.holdings || {});
  const holdings = keys.length ? keys.map((k) => esc(k) + ' ' + esc(p.holdings[k])).join(', ') : 'Boş';
  return '<div class="portfolio-card__head"><div><h4>' + esc(p.name)
    + ' <span class="muted">#' + p.id + '</span></h4>'
    + '<span class="muted">' + esc(p.currency) + ' · Nakit ' + esc(p.cash) + '</span></div>'
    + '<div class="detail__meta"><button class="btn btn--secondary btn-valuation" type="button">Değerleme</button>'
    + '<button class="btn btn--primary btn-rebalance" type="button">Yeniden Dengele</button></div></div>'
    + '<p class="muted small">Varlıklar: ' + esc(holdings) + '</p>';
}

async function rebalance(p) {
  if (!state.selected) return;
  const btn = p._btn || null;
  try {
    const res = await api('/advisor/rebalance/' + p.id + '?customer_id=' + state.selected.id, { method: 'POST' });
    openModal(htmlModal(esc(res.report || ''), res.orders || [], res.weights || {}));
    await Promise.all([loadPortfolios(state.selected.id), loadRuns(), loadCustomers()]);
  } catch (e) {
    openModal('<h3>Hata</h3><p class="muted">' + esc(e.message) + '</p>');
  }
}

async function showValuation(p) {
  try {
    const v = await api('/portfolios/' + p.id + '/valuation');
    const rows = (v.items || []).map((i) =>
      '<tr><td>' + esc(i.ticker) + '</td><td class="num">' + (i.market_value || 0).toFixed(2) + '</td>'
      + '<td class="num ' + ((i.unrealized_pnl || 0) >= 0 ? 'pos' : 'neg') + '">' + (i.unrealized_pnl || 0).toFixed(2) + '</td></tr>'
    ).join('');
    openModal('<h3>' + esc(p.name) + ' — Değerleme</h3>'
      + '<table class="table"><thead><tr><th>Varlık</th><th class="num">Piyasa Değeri</th><th class="num">Kar/Zarar</th></tr></thead>'
      + '<tbody>' + rows + '</tbody></table>'
      + '<p class="mt small">Toplam: <strong>' + v.total_value.toFixed(2) + '</strong> · Nakit: ' + v.cash.toFixed(2)
      + ' · Toplam Kar/Zarar: ' + v.total_unrealized_pnl.toFixed(2) + '</p>');
  } catch (e) {
    openModal('<h3>Hata</h3><p class="muted">' + esc(e.message) + '</p>');
  }
}

async function loadRuns() {
  const list = qs('#runs-list');
  if (!state.selected) { list.innerHTML = '<p class="muted small">Kayıt yok.</p>'; return; }
  try {
    const runs = await api('/runs?customer_id=' + state.selected.id + '&limit=20');
    if (!runs.length) { list.innerHTML = '<p class="muted small">Kayıt yok.</p>'; return; }
    list.innerHTML = runs.map((r) =>
      '<div class="run-item"><div class="head"><span><strong>#' + r.id
      + '</strong> · Portföy #' + r.portfolio_id
      + ' · <span class="muted">' + (r.status || '') + '</span></span>'
      + '<span class="muted small">' + esc((r.created_at || '').slice(0, 16).replace('T', ' ')) + '</span></div>'
      + (r.error ? '<p class="neg small">' + esc(r.error) + '</p>' : '')
      + '<div class="run-report">' + esc(r.report || '') + '</div></div>'
    ).join('');
  } catch (e) {
    list.innerHTML = '<p class="muted small">' + esc(e.message) + '</p>';
  }
}

function htmlModal(report, orders, weights) {
  const orderRows = orders.map((o) =>
    '<tr><td>' + esc(o.ticker) + '</td><td>' + (o.side || '') + '</td>'
    + '<td class="num">' + o.quantity + '</td><td class="num">' + (o.price || 0).toFixed(2) + '</td></tr>'
  ).join('');
  const weightRows = Object.keys(weights || {}).map((t) =>
    '<tr><td>' + esc(t) + '</td><td class="num">' + ((weights[t] || 0) * 100).toFixed(2) + '%</td></tr>'
  ).join('');
  return '<h3>Danışman Raporu</h3>'
    + '<p class="muted small run-report">' + report + '</p>'
    + '<h4 class="mt">Hedef Ağırlıklar</h4>'
    + '<table class="table"><tbody>' + weightRows + '</tbody></table>'
    + '<h4 class="mt">Emirler</h4>'
    + '<table class="table"><thead><tr><th>Varlık</th><th>Yön</th><th class="num">Miktar</th><th class="num">Fiyat</th></tr></thead><tbody>'
    + orderRows + '</tbody></table>';
}

function openModal(html) {
  qs('#modal-body').innerHTML = html;
  qs('#modal-backdrop').classList.remove('hidden');
}
function closeModal() { qs('#modal-backdrop').classList.add('hidden'); }

async function newCustomer() {
  qs('#modal-body').innerHTML = '<h3>Yeni Müşteri</h3>'
    + '<div class="field"><label>Ad Soyad</label><input id="f-name" /></div>'
    + '<div class="field"><label>E-posta</label><input id="f-email" type="email" /></div>'
    + '<div class="field"><label>Yatırım Ufku (yıl)</label><input id="f-horizon" type="number" value="5" /></div>'
    + '<div class="field"><label>Aylık Gelir</label><input id="f-income" type="number" value="0" /></div>'
    + '<div class="field"><label>Risk Toleransı (1-5)</label><input id="f-tolerance" type="number" min="1" max="5" value="3" /></div>'
    + '<div class="modal-actions"><button id="save-customer" class="btn btn--primary" type="button">Kaydet</button></div>';
  qs('#modal-backdrop').classList.remove('hidden');
  qs('#save-customer').addEventListener('click', async () => {
    try {
      await postJSON('/customers', {
        full_name: qs('#f-name').value,
        email: qs('#f-email').value,
        investment_horizon_years: Number(qs('#f-horizon').value),
        monthly_income: Number(qs('#f-income').value),
        declared_risk_tolerance: Number(qs('#f-tolerance').value),
      });
      closeModal();
      await loadCustomers();
    } catch (e) {
      alert(e.message);
    }
  });
}
function postJSON(path, body) {
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}


async function newPortfolio() {
  if (!state.selected) return;
  qs('#modal-body').innerHTML = '<h3>Yeni Portföy</h3>'
    + '<div class="field"><label>Ad</label><input id="p-name" value="Ana Portföy" /></div>'
    + '<div class="field"><label>Para Birimi</label><input id="p-currency" value="TRY" /></div>'
    + '<div class="field"><label>Nakit</label><input id="p-cash" type="number" value="100000" /></div>'
    + '<div class="modal-actions"><button id="save-portfolio" class="btn btn--primary" type="button">Kaydet</button></div>';
  qs('#modal-backdrop').classList.remove('hidden');
  qs('#save-portfolio').addEventListener('click', async () => {
    try {
      await postJSON('/portfolios', {
        customer_id: state.selected.id,
        name: qs('#p-name').value,
        currency: qs('#p-currency').value,
        cash: Number(qs('#p-cash').value),
      });
      closeModal();
      await loadPortfolios(state.selected.id);
    } catch (e) {
      alert(e.message);
    }
  });
}

function init() {
  qs('#btn-refresh').addEventListener('click', loadCustomers);
  qs('#btn-new-customer').addEventListener('click', newCustomer);
  qs('#btn-new-portfolio').addEventListener('click', () => { if (state.selected) newPortfolio(); });
  qs('#modal-close').addEventListener('click', closeModal);
  qs('#modal-backdrop').addEventListener('click', (e) => { if (e.target.id === 'modal-backdrop') closeModal(); });
  loadCustomers();
}
document.addEventListener('DOMContentLoaded', init);

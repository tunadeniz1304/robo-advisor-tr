// Uygulama durumu: tek, paylaşılan ve değiştirilebilir nesne.

export const state = {
  token: null,
  refresh: null,
  me: null,
  customerId: null,
  portfolioId: null,
  charts: {},
  methods: {},
  goal: null,
  q: { list: [], i: 0, answers: {} },
};

export function isStaff() {
  return Boolean(state.me) && state.me.role !== "musteri";
}

export function resetCustomerContext() {
  state.portfolioId = null;
  state.goal = null;
}

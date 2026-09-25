// fetch sarmalayıcı: Bearer token, 401'de tek seferlik yenileme, anlaşılır hatalar.

import { state } from "./state.js";

export const API = "/api/v1";
const SESSION_STORE_KEY = "of.tokens";

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

// API "detail" alanını (metin, doğrulama listesi veya nesne) okunur metne çevirir.
export function describeDetail(detail) {
  if (detail == null) {
    return "";
  }
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg || JSON.stringify(d)).join(" · ");
  }
  if (typeof detail === "object" && detail.message) {
    return String(detail.message);
  }
  return JSON.stringify(detail);
}

function errorMessage(status, body) {
  const detail = typeof body === "object" && body ? describeDetail(body.detail) : body;
  if (status === 429) {
    return "Çok fazla istek gönderildi. Lütfen biraz sonra tekrar deneyin.";
  }
  if (status >= 500) {
    return `Sunucu hatası (${status}). ${detail || ""}`.trim();
  }
  return detail || `İstek başarısız (${status}).`;
}

export function saveTokens(tokens) {
  state.token = tokens.access_token;
  state.refresh = tokens.refresh_token;
  try {
    sessionStorage.setItem(SESSION_STORE_KEY, JSON.stringify(tokens));
  } catch {
    // sessionStorage kullanılamıyorsa oturum yalnız bellekte tutulur.
  }
}

export function loadStoredTokens() {
  try {
    return JSON.parse(sessionStorage.getItem(SESSION_STORE_KEY) || "null");
  } catch {
    return null;
  }
}

export function logout() {
  state.token = null;
  state.refresh = null;
  try {
    sessionStorage.removeItem(SESSION_STORE_KEY);
  } catch {
    // yok say
  }
  location.reload();
}

async function refreshTokens() {
  const res = await fetch(`${API}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: state.refresh }),
  });
  if (!res.ok) {
    return false;
  }
  saveTokens(await res.json());
  return true;
}

export function authHeaders(extra = {}) {
  const headers = { "Content-Type": "application/json", ...extra };
  if (state.token) {
    headers.Authorization = `Bearer ${state.token}`;
  }
  return headers;
}

async function readBody(res) {
  const type = res.headers.get("content-type") || "";
  return type.includes("json") ? res.json() : res.text();
}

export async function api(path, opts = {}, retry = true) {
  let res;
  try {
    res = await fetch(API + path, { ...opts, headers: authHeaders(opts.headers) });
  } catch {
    throw new ApiError("Sunucuya ulaşılamadı. Bağlantınızı kontrol edin.", 0, null);
  }
  if (res.status === 401 && retry && state.refresh) {
    if (await refreshTokens()) {
      return api(path, opts, false);
    }
    logout();
  }
  const body = await readBody(res);
  if (!res.ok) {
    throw new ApiError(errorMessage(res.status, body), res.status, body);
  }
  return body;
}

export function post(path, body, headers = {}) {
  return api(path, { method: "POST", body: JSON.stringify(body ?? {}), headers });
}

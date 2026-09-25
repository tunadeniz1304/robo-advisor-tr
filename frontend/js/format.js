// Sayı, yüzde ve TL biçimlendirme (tr-TR).

const DASH = "–";
const tlFormat = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });

export function tl(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return DASH;
  }
  return `${tlFormat.format(value)} TL`;
}

export function pct(value, digits = 1) {
  if (value == null || Number.isNaN(Number(value))) {
    return DASH;
  }
  const text = (value * 100).toLocaleString("tr-TR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return `%${text}`;
}

// Yıllıklaştırılmış metrikler: dönem 1 yıldan kısaysa API null döner.
export function annualPct(value) {
  return value == null ? "— (<1 yıl)" : pct(value);
}

export function num(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) {
    return DASH;
  }
  return Number(value).toLocaleString("tr-TR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function shortDate(iso) {
  return iso ? String(iso).slice(0, 10) : DASH;
}

export function dateTime(iso) {
  return iso ? String(iso).slice(0, 16).replace("T", " ") : DASH;
}

const PROPOSAL_STATUS = {
  TASLAK: "Taslak",
  ONAY_BEKLIYOR: "Onay bekliyor",
  ONAYLANDI: "Onaylandı",
  YURUTULDU: "Yürütüldü",
  REDDEDILDI: "Reddedildi",
  SURESI_DOLDU: "Süresi doldu",
  BASARISIZ: "Başarısız",
};

export function proposalStatusLabel(status) {
  return PROPOSAL_STATUS[status] || status || DASH;
}

const QUALITY_STATUS = { ok: "iyi", warning: "uyarı", error: "hata" };

export function qualityLabel(status) {
  return QUALITY_STATUS[status] || status || DASH;
}

const ASSET_CLASS = {
  para_piyasasi: "Para piyasası",
  tl_tahvil: "TL tahvil",
  eurobond: "Eurobond",
  altin: "Altın",
  doviz: "Döviz",
  bist_endeks: "BIST endeks",
  bist_hisse: "BIST hisse",
  diger: "Diğer",
};

export function classLabel(key) {
  return ASSET_CLASS[key] || key;
}

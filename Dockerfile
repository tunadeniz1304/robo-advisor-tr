# =============================================================================
# Otonom Finansal Danışman (Robo-Advisor) — Production Docker Image
# Çok aşamalı (multi-stage) build: bağımlılık katmanının ayrılması,
# küçük runtime imajı ve sıfır tekil (non-root) çalışma prensibi.
# =============================================================================

# ---- Aşama 1: bağımlılıklar -------------------------------------------------
FROM python:3.11-slim AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/advisor

# Önce sadece bağımlılıkları kopyala: kaynak kodu değişse bile Docker katman
# önbelleğini korur, rebuild hızlanır.
COPY requirements.txt .

RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

# ---- Aşama 2: çalıştırılabilir image ---------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/opt/advisor

# Uygulama root'u için adanmış, izinsiz kullanıcı oluştur (security best-practice)
RUN useradd --create-home --shell /usr/sbin/nologin advisor

WORKDIR /opt/advisor

COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY . .

RUN chown -R advisor:advisor /opt/advisor

USER advisor

EXPOSE 8000

# Uvicorn'u doğrudan çalıştır: main.py kök uvicorn bağlama noktasıdır (Adım 5).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

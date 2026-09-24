# =============================================================================
# Otonom Finansal Danışman — üretim imajı (multi-stage, non-root)
# =============================================================================

# ---- Aşama 1: bağımlılıklar -------------------------------------------------
FROM python:3.11-slim AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/advisor
COPY requirements.txt requirements-extras.txt ./
RUN python -m pip install --prefix=/install -r requirements.txt \
    && (python -m pip install --prefix=/install -r requirements-extras.txt || echo "opsiyonel paketler atlandı")

# ---- Aşama 2: çalıştırılabilir imaj -------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/advisor \
    PYTHONIOENCODING=utf-8

RUN useradd --create-home --shell /usr/sbin/nologin advisor
WORKDIR /opt/advisor

COPY --from=deps /install /usr/local
COPY . .
RUN mkdir -p /opt/advisor/state && chown -R advisor:advisor /opt/advisor

USER advisor
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]

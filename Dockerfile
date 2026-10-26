# syntax=docker/dockerfile:1

# ---------- frontend build ----------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tools ./tools
# The console, built above, is served by FastAPI from app/static.
COPY --from=web /app/static ./app/static

# Run as a non-root user; data/ holds the SQLite fallback and generated keys.
RUN useradd --create-home --uid 10001 gateway \
    && mkdir -p /app/data \
    && chown -R gateway:gateway /app
USER gateway

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/readyz || exit 1

# 2 workers per instance: mostly I/O bound waiting on providers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--timeout-graceful-shutdown", "30"]

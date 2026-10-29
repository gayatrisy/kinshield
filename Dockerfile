# --- web app ---
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- API + worker in one container ---
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 app
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=web /web/dist frontend/dist
RUN mkdir -p /data && chown -R app /data /app
USER app
ENV PYTHONPATH=/app/backend KINSHIELD_DATA_DIR=/data PYTHONUNBUFFERED=1 KINSHIELD_EMBEDDED_WORKER=1 PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn kinshield.api:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips '*'"]

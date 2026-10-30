# Hugging Face Space for KinShield: builds the app straight from the public GitHub repo.
# To deploy a newer version: Space Settings -> Factory rebuild.

# --- fetch the code ---
FROM alpine:3.20 AS src
ADD https://codeload.github.com/gayatrisy/kinshield/tar.gz/refs/heads/main /kinshield.tar.gz
RUN mkdir /src && tar -xzf /kinshield.tar.gz -C /src --strip-components=1

# --- web app ---
FROM node:22-slim AS web
WORKDIR /web
COPY --from=src /src/frontend/ ./
RUN npm ci --no-audit --no-fund && npm run build

# --- API + reminder worker in one container ---
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
WORKDIR /app
COPY --from=src /src/backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY --from=src /src/backend/ backend/
COPY --from=web /web/dist frontend/dist
RUN mkdir -p /app/data && chown -R user /app
USER user
ENV PYTHONPATH=/app/backend KINSHIELD_DATA_DIR=/app/data PYTHONUNBUFFERED=1 KINSHIELD_EMBEDDED_WORKER=1 PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "uvicorn kinshield.api:app --host 0.0.0.0 --port 7860 --proxy-headers --forwarded-allow-ips '*'"]

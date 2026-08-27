FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    FRONTEND_DIST=/app/frontend/dist \
    SSLROOTCERT=/app/certs/cc-ca.crt

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/sentinel /app/sentinel
COPY certs/cc-ca.crt /app/certs/cc-ca.crt
COPY --from=frontend /frontend/dist /app/frontend/dist

EXPOSE 8080
CMD ["sh", "-c", "uvicorn sentinel.main:app --host 0.0.0.0 --port ${PORT:-8080} --timeout-keep-alive 120"]

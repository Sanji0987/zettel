# ---- Stage 1: build the React frontend ----
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build
# produces /frontend/dist

# ---- Stage 2: Python backend + serve the built frontend ----
FROM python:3.12-slim AS runtime
WORKDIR /app

# System deps kept minimal; slim image already has what FastAPI needs.
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Backend code
COPY backend/app ./app

# Bring in the compiled React app; FastAPI serves it from /app/static.
COPY --from=frontend /frontend/dist ./static

# SQLite lives on a mounted volume at /data (see compose).
ENV ZK_DB_PATH=/data/zettelkeistan.db
ENV ZK_STATIC_DIR=/app/static

# Run as a non-root user. /data is a mounted volume, so make it writable by that
# user (the app also creates the DB dir at startup).
RUN useradd --uid 10001 --create-home appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Zettelkeistan — Phase 1 (containerized notes app)

FastAPI + SQLite + React, single container. FastAPI serves the compiled
React build; SQLite persists to a Docker volume. No Cognee / n8n yet.

## Run (production-like, one container)

    docker compose up --build

Open http://localhost:8000

The SQLite DB lives in the `zk_data` volume (survives rebuilds).
To wipe it: `docker compose down -v`.

## Dev (hot reload, two servers — optional)

Backend:

    cd backend
    pip install -r requirements.txt
    ZK_DB_PATH=./dev.db ZK_STATIC_DIR=/nonexistent uvicorn app.main:app --reload

Frontend (Vite proxies /api -> :8000):

    cd frontend
    npm install
    npm run dev

Open http://localhost:5173

## API

    GET    /api/health
    GET    /api/notes
    POST   /api/notes
    GET    /api/notes/{id}
    PUT    /api/notes/{id}
    DELETE /api/notes/{id}

## Later phases slot in here
- Phase 2: add `postgres` + `cognee` services to compose; FastAPI gains
  `/search` + ingest calling Cognee. `pending_ingest` flag already exists.
- Phase 3: add `n8n` service for batched cognify/memify.

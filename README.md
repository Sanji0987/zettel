# Zettelkeistan

A local-first atomic notes app (Zettelkasten) with a knowledge-graph memory and a
chat "brain". FastAPI + SQLite + React in a single container; **Cognee Cloud** holds the
derived knowledge graph; an **n8n** workflow answers chat questions using a **local
Ollama** model. SQLite is the source of truth — the graph is rebuildable from it.

> The full, honest build history is in [`BUILD_LOG.md`](BUILD_LOG.md).

## What's in here

- **Notes** — CRUD over SQLite (`title`, `text`, `label`, `references`), each with a
  `pending_ingest` "dirty since last ingest" flag.
- **Vault import/export** — notes ⇄ Markdown files (`.md` with light frontmatter);
  File System Access API with a multi-file/zip fallback. The vault is a sync
  checkpoint, not a second source of truth.
- **Cognee integration** — per-note ingest + search, an `active_dataset` pointer, and
  an atomic rebuild (build under a fresh name, cognify once, flip the pointer only on
  success). See [`API_CONTRACT.md`](API_CONTRACT.md) / [`FRONTEND_API.md`](FRONTEND_API.md).
- **Chat** — a thin FastAPI relay (`POST /api/chat`) forwards to an n8n webhook (or a
  mock). The n8n workflows live in [`n8n/`](n8n/): read mode does a length-gated,
  **depth-2-bounded** Cognee decomposition; write mode drafts a note (edit gate, no save).
- **Model** — the existing local `ollama` container (e.g. `gemma4:e2b`), reached over
  the shared `n8n-net` Docker network. No cloud LLM.

## Run (single container)

    cp .env.example .env      # fill in COGNEE_API_KEY (never commit .env)
    docker compose up --build

Open http://localhost:8000 (bound to loopback; the API has no auth). SQLite lives in the
`zk_data` volume and survives rebuilds — wipe with `docker compose down -v`.

The app runs as non-root and joins the external `n8n-net` network so it can reach the
`ollama` (and `n8n`) containers by name. Make sure that network + the `ollama` container
exist first (`docker start ollama`).

## Dev (hot reload, two servers)

Backend:

    cd backend
    pip install -r requirements.txt
    ZK_DB_PATH=./dev.db ZK_STATIC_DIR=/nonexistent uvicorn app.main:app --reload

Frontend (Vite proxies `/api` → :8000):

    cd frontend && npm install && npm run dev   # http://localhost:5173

## Configuration (`.env`)

| Var | Purpose |
|-----|---------|
| `COGNEE_BASE_URL` / `COGNEE_TENANT_ID` / `COGNEE_API_KEY` | Cognee Cloud REST auth |
| `OLLAMA_BASE_URL` | local Ollama (default `http://ollama:11434`) |
| `N8N_CHAT_WEBHOOK` | n8n chat webhook; empty = built-in mock reply |
| `ZK_CORS_ORIGINS` | override the scoped dev CORS origins (optional) |
| `DECOMP_WORD_THRESHOLD` | n8n: word count above which read mode decomposes (default 40) |

## Layout

    backend/app/        FastAPI: db.py (SQLite), cognee_client.py, rebuild.py,
                        relay.py (ollama/n8n relay), main.py (routes)
    frontend/src/       React: App.jsx, vault.js (md<->note + zip), styles.css
    n8n/                byte-accurate workflow exports + restore README
    scripts/            throwaway harnesses (e.g. decomp_test.py)
    API_CONTRACT.md, FRONTEND_API.md, BUILD_LOG.md

## Notes / caveats

- The n8n chat "brain" is three workflows (main + `Answer With Optional Split` +
  `Cognee Retrieve One`); credentials are **not** exported — recreate them on import
  (see `n8n/README.md`).
- `gemma4:e2b` needs `num_ctx` pinned (its default 131072 context crashes llama-server);
  the workflows pin `8192`.
- Single-user, local-first by design — the API is unauthenticated and loopback-bound.

# Zettelkeistan

A local-first atomic notes app (Zettelkasten) with a knowledge-graph memory and a
chat "brain". FastAPI + SQLite + React in a single container; **Cognee Cloud** holds the
derived knowledge graph; an **n8n** workflow answers chat questions. On this branch
(`main`) the chat reasoning runs on **Groq** (with optional real web search); the
`ollama-version` branch runs it on a **local Ollama** model instead — see
[Two versions](#two-versions). SQLite is the source of truth — the graph is rebuildable
from it.

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
- **Model** — on `main`, chat reasoning runs on **Groq** (`openai/gpt-oss-120b`) via the
  n8n workflow, and offers **real web search** (Groq `groq/compound`) when your notes
  don't have the answer. The `ollama-version` branch uses the local `ollama` container
  (e.g. `gemma4:e2b`) instead, reached over the shared `n8n-net` Docker network. Either
  way, note *content* also goes to Cognee Cloud for the graph — see [Two versions](#two-versions).

## Two versions

This project has two branches with different privacy/performance tradeoffs. Both store
your notes in SQLite locally and both send note content to **Cognee Cloud** for the
knowledge graph (embeddings + graph building). The difference is where the AI *reasoning*
runs.

### `ollama-version` — local reasoning, more private
- Chat/reasoning runs on a **local model** (Gemma via Ollama) on your own hardware.
- Your notes' *content* still goes to **Cognee Cloud** for the graph — that's the one
  external service, and it's the same in both versions.
- Nothing else leaves your machine. No note text is sent to any LLM provider for
  reasoning — it's all local.
- Trade-off: slower, and the local model is smaller/less capable. No web search
  (stays fully local by design).
- Best if: you want reasoning kept on your own machine and are OK with Cognee Cloud as
  the one trusted external dependency.

### `main` — Groq cloud, fast and more capable
- Chat/reasoning runs on **Groq** (`openai/gpt-oss-120b`), plus Cognee Cloud for the graph.
- Faster (Groq is very fast), a stronger model (better grounding, decomposition, honesty),
  and supports **real web search** (via Groq's `groq/compound` model) when your notes
  don't have an answer — offered only after an explicit "search the web" choice, and its
  replies are clearly labelled as coming from the web, not your notes.
- Trade-off: less private — your note content goes to *both* Cognee Cloud AND Groq (for
  reasoning). Requires a Groq API key (free tier available).
- Best if: you want the best speed/quality and are comfortable with note content reaching
  Groq in addition to Cognee.

### Switching
- `git checkout ollama-version` for the local version, `git checkout main` for Groq.
- **`main`** needs a **`GROQ_API_KEY`** (free tier at console.groq.com). The key is
  entered into the n8n **"Groq account"** credential in the n8n UI — the FastAPI app
  never uses it. Everything else (`COGNEE_*`, webhooks) is the same as below.
- **`ollama-version`** needs no Groq key. Instead the **`ollama` container must be
  running with the model pulled** (`docker start ollama`, then `ollama pull gemma4:e2b`);
  it's reached at `OLLAMA_BASE_URL` (default `http://ollama:11434`). Its `.env.example`
  has no `GROQ_API_KEY`.
- Both branches share the same `COGNEE_BASE_URL` / `COGNEE_TENANT_ID` / `COGNEE_API_KEY`.

**Honest note:** neither version is fully airgapped — both rely on Cognee Cloud for the
knowledge graph, so your note content leaves your machine in both. If true
zero-external-dependency operation is a requirement, that would need self-hosted Cognee
(not currently set up).

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
| `COGNEE_BASE_URL` / `COGNEE_TENANT_ID` / `COGNEE_API_KEY` | Cognee Cloud REST auth (both branches) |
| `GROQ_API_KEY` | `main` only: Groq chat/web-search key. Entered into the n8n "Groq account" credential; the app never uses it |
| `OLLAMA_BASE_URL` | local Ollama (default `http://ollama:11434`); the reasoning model on `ollama-version`, still used on `main` for `/api/ollama/status` |
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
- On `ollama-version`, `gemma4:e2b` needs `num_ctx` pinned (its default 131072 context
  crashes llama-server); the workflows pin `8192`. `main` uses Groq, so this doesn't apply.
- Single-user, local-first by design — the API is unauthenticated and loopback-bound.

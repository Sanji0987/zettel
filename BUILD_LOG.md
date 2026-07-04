# Build Log — Zettelkeistan

A chronological record of how this project was actually built, in the order the work
happened. Each entry is one real unit of work (a request and the files it touched).

> Note on git history: the project was not under version control during development.
> The commits in this repo are a faithful reconstruction of the sequence below —
> each commit is a coherent, buildable snapshot in the true order, ending at the
> current tree. Timestamps are the real commit times (not backdated).

---

## 1. Scaffold — containerized notes app (FastAPI + SQLite + React)
**Files:** `Dockerfile`, `docker-compose.yml`, `.env.example`, `.gitignore`,
`.dockerignore`, `README.md`, `backend/requirements.txt`, `backend/app/{__init__,db,main,cognee_client}.py`,
`frontend/{index.html,package.json,vite.config.js}`, `frontend/src/{App.jsx,main.jsx,styles.css}`

Single-container app: FastAPI serves the compiled React build; SQLite persists to a
Docker volume. Notes CRUD (`/api/notes`), plus a first Cognee Cloud integration
(`cognee_client.py`) with per-note ingest/search. The SQLite schema already bakes in
`label`, `references`, `pending_ingest`, and a `settings` table for later phases.

## 2. Fix the `/add` 409 — multipart, not JSON
**Files:** `backend/app/cognee_client.py`

`cognee_client.add()` was POSTing JSON to Cognee's `/api/v1/add`, which is a **multipart
file-upload** endpoint — so every ingest returned `409 "Either datasetId or datasetName
must be provided"`. Switched to a multipart body (`data` file field + `datasetName`
form field) with auth-only headers so httpx sets the boundary. Confirmed against the
live OpenAPI schema.

## 3. Dataset list + delete (destroy/rebuild property)
**Files:** `backend/app/cognee_client.py`

Added `list_datasets()` and `delete_dataset()` while testing the "destroy and rebuild"
property. Established that a deleted dataset's deterministic id is poisoned on recreate,
which later drives the fresh-name rebuild strategy.

## 4. API contract for the frontend
**Files:** `API_CONTRACT.md`

Mapped every route the frontend uses/needs, so a separate frontend dev could work from it.

## 5. Active-dataset pointer + atomic rebuild
**Files:** `backend/app/db.py`, `backend/app/cognee_client.py`, `backend/app/rebuild.py`,
`backend/app/main.py`

SQLite becomes the source of truth; the Cognee graph is rebuildable. Added a
`settings`-backed `active_dataset` pointer (`db.get_setting/set_setting`,
`cognee_client.active_dataset()`), made add/cognify/search default to it, and added
`rebuild.py` (build under a fresh name, cognify once, flip the pointer only on success).
New routes: `GET /api/cognee/active`, `POST /api/cognee/rebuild`. Loop isolated so it can
later move to n8n.

## 6. Cleanup — drop the debug route
**Files:** `backend/app/main.py`

Removed the throwaway `/api/cognee/debug` probe and tidied comments.

## 7. Vault import/export (Markdown)
**Files:** `frontend/src/vault.js`, `frontend/src/App.jsx`, `backend/app/main.py`,
`backend/app/db.py`, `frontend/src/styles.css`

The vault folder as a sync checkpoint: import reads `.md` files into SQLite, export writes
SQLite out. `vault.js` does the markdown↔note mapping + a dependency-free zip; `App.jsx`
gets Open/Export Vault (File System Access API with a multi-file/zip fallback);
`db.import_notes()` bulk-inserts skipping exact dups; `POST /api/notes/import`,
`GET /api/notes/export`.

## 8. State persistence — dirty flag + settings API
**Files:** `backend/app/db.py`, `backend/app/main.py`

`pending_ingest` becomes a real "dirty since last ingest" flag (set on create/update,
cleared on ingest via `db.clear_pending_ingest`). Added a small `GET/POST /api/settings/{key}`
for UI state (last open note, etc.).

## 9. Frontend API reference
**Files:** `FRONTEND_API.md`

Complete route/data-model/gotcha reference written from the actual backend code.

## 10. Security + bug hardening
**Files:** `docker-compose.yml`, `Dockerfile`, `backend/app/main.py`, `backend/app/db.py`,
`frontend/src/vault.js`, `frontend/src/App.jsx`, `FRONTEND_API.md`

From a read-only review: bind the API to loopback (`127.0.0.1:8000`), run the container
as non-root `appuser`, scope CORS to the dev origins (not `*`), whitelist writable
settings keys (protect the `active_dataset` pointer), make import lenient with title/label
clamps, dedupe export filenames (`uniqueFilenames`), harden `apiSetting` to fire-and-forget,
and migrate the deprecated `@app.on_event` to `lifespan`.

## 11. Wire the app to the existing Ollama container + chat relay + chat UI
**Files:** `docker-compose.yml`, `.env.example`, `backend/app/relay.py`, `backend/app/main.py`,
`frontend/src/App.jsx`, `frontend/src/styles.css`

Multi-networked the app onto the external `n8n-net` so it can reach the pre-existing
`ollama` container by name. `relay.py` is a thin relay: `GET /api/ollama/status` lists
pulled models; `POST /api/chat` forwards to `N8N_CHAT_WEBHOOK` or returns a mock. Added a
chat panel (read/write toggle, bubbles) that disables itself if Ollama is unreachable.

## 12. n8n decompose brain — relay passthrough, decomposition harness, workflow exports
**Files:** `backend/app/relay.py`, `scripts/decomp_test.py`, `n8n/*.json`, `n8n/README.md`

Built the chat "brain" as n8n workflows (webhook → mode switch → write-draft / read with a
length-gated, depth-2-bounded Cognee decomposition). `relay.py` now passes the write-mode
`draft` through. `scripts/decomp_test.py` is a throwaway harness that evaluated
gemma4:e2b's question decomposition. `n8n/` holds byte-accurate exports of the three
workflows (main + `Answer With Optional Split` + `Cognee Retrieve One`) plus a restore README.

## 13. This build log
**Files:** `BUILD_LOG.md`

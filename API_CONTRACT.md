# Zettelkeistan — Frontend ↔ Backend API Contract

Backend: FastAPI at `http://localhost:8000`. All app routes are under `/api`.
In dev, Vite proxies `/api` → `localhost:8000` (see `frontend/vite.config.js`), so
the frontend just calls `/api/...` (base const `API = "/api"`). CORS is open (`*`).

Interactive/live docs while the container runs:
- Swagger UI: http://localhost:8000/docs
- OpenAPI JSON: http://localhost:8000/openapi.json

---

## Data shapes

### Note (response — `NoteOut`)
```json
{
  "id": 1,
  "title": "string",
  "text": "string",
  "label": "string",
  "references": "string",        // source URLs / citations, newline-separated
  "pending_ingest": false,       // true = created/edited but not yet pushed to Cognee
  "created_at": "2026-07-03T16:30:57.018408+00:00",  // ISO-8601 UTC
  "updated_at": "2026-07-03T16:31:10.022109+00:00"
}
```

### Note (request body — `NoteIn`, used by POST and PUT)
```json
{
  "title": "string",       // optional, max 500 chars, default ""
  "text": "string",        // optional, default ""
  "label": "string",       // optional, max 200 chars, default ""
  "references": "string"   // optional, default ""
}
```
All four fields are optional; omit any and it defaults to `""`.

---

## Routes

### Notes (SQLite — source of truth, no Cognee needed)

| # | Method | Path | Body | Success | Errors |
|---|--------|------|------|---------|--------|
| 1 | GET  | `/api/notes` | — | `200` → `NoteOut[]` (newest first) | — |
| 2 | POST | `/api/notes` | `NoteIn` | `201` → `NoteOut` | `422` invalid body |
| 3 | GET  | `/api/notes/{id}` | — | `200` → `NoteOut` | `404` not found |
| 4 | PUT  | `/api/notes/{id}` | `NoteIn` | `200` → `NoteOut` | `404`, `422` |
| 5 | DELETE | `/api/notes/{id}` | — | `204` (empty body) | `404` |

Notes:
- `id` is an integer (SQLite autoincrement).
- List is ordered by `updated_at` DESC.
- PUT replaces all four fields (send the full object, not a partial patch).

### Cognee (knowledge-graph integration — needs `.env` configured)

| # | Method | Path | Body | Success | Errors |
|---|--------|------|------|---------|--------|
| 6 | GET  | `/api/cognee/status` | — | `200` → `{"configured": bool}` | — |
| 7 | POST | `/api/notes/{id}/ingest` | — | `200` → `{"ingested": <id>}` | `404` note, `502` Cognee, `503` not configured |
| 8 | POST | `/api/search` | `SearchIn` | `200` → search result (below) | `502` Cognee, `503` not configured |
| 9 | POST | `/api/cognee/debug` | — | `200` → `{create, list}` raw status/text | `503` not configured |
| 10 | GET | `/api/health` | — | `200` → `{"status":"ok"}` | — |

`GET /api/cognee/status` returns `{"configured": false}` when the tenant keys are
missing — the frontend should call this on load and hide/disable Cognee features
(search, ingest) when `false`, instead of getting 503s.

#### `SearchIn` (body for POST /api/search)
```json
{
  "query": "how do notes become a graph?",
  "mode": "quick"     // "quick" (fast vector lookup) | "explore" (graph reasoning). default "quick"
}
```
- `mode` MUST be exactly `"quick"` or `"explore"` (regex-validated → `422` otherwise).
- `quick` → Cognee `CHUNKS`; `explore` → `GRAPH_COMPLETION`.

#### Search response (`200`)
```json
{
  "query": "...",
  "mode": "quick",
  "results": [
    {
      "dataset_id": "f5e9b624-...",
      "dataset_name": "zettelkeistan",
      "search_result": [ /* array of hits/answer objects from Cognee */ ]
    }
  ]
}
```
`results` is Cognee's raw payload passed straight through — an array; the useful
content is under `results[0].search_result`. Shape differs by mode (chunks vs. a
generated answer), so render defensively.

#### Ingest flow
`POST /api/notes/{id}/ingest` pushes that note's `title + text` to Cognee (add →
cognify) and clears its `pending_ingest` flag. It's synchronous and can take a
while (cognify builds the graph). Expect multi-second latency; show a spinner.

---

## Routes the CURRENT frontend already uses
`frontend/src/App.jsx` today calls only:
- `GET /api/notes` (list)
- `POST /api/notes` (create)
- `DELETE /api/notes/{id}` (delete)

## Routes still UNUSED but available (good candidates for the new frontend)
- `GET /api/notes/{id}` — load one note into an editor
- `PUT /api/notes/{id}` — edit/save a note
- `GET /api/cognee/status` — feature-gate the Cognee UI
- `POST /api/notes/{id}/ingest` — "Add to knowledge graph" button (use `pending_ingest` to show which notes still need this)
- `POST /api/search` — the search box (quick/explore toggle)
- `GET /api/health`, `POST /api/cognee/debug` — diagnostics only

---

## Non-API routes (single-container prod build only)
When a compiled React build exists at `/app/static`, the backend also serves:
- `/assets/*` — static JS/CSS
- `GET /{anything-else}` → `index.html` (SPA client-side routing fallback)

These don't exist in the two-server dev setup (Vite serves the frontend on :5173).
Because of the catch-all, **every real backend endpoint must live under `/api`** —
anything else is swallowed by the SPA fallback.

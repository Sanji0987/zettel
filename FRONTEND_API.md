# Zettelkeistan — Frontend API Reference

**This is the single source of truth for building the frontend.** It is written from the
actual backend code (`main.py`, `db.py`, `cognee_client.py`, `rebuild.py`) and verified
against the running container. Where live behavior differs from a code comment, the
**verified behavior is documented** and the discrepancy is noted.

- Base: all app routes are under **`/api`**. The frontend is served **same-origin** from
  the same FastAPI process (built React at `/app/static`), so **use relative URLs**
  (`fetch("/api/notes")`). In dev, Vite proxies `/api` → `:8000`.
- Auth to Cognee (`X-Api-Key` / `X-Tenant-Id`) is handled **entirely server-side**. The
  frontend never sends or sees any key.
- All request/response bodies are JSON (`Content-Type: application/json`) unless noted.
- Interactive docs while running: `http://localhost:8000/docs`.

> curl examples use `http://localhost:8000`; in the app use relative `/api/...`.

---

## A. DATA MODEL — the Note object

Every notes endpoint returns notes in this exact shape (`NoteOut`):

```ts
interface Note {
  id: number;              // SQLite autoincrement
  title: string;           // max 500 chars
  text: string;            // note body
  label: string;           // single short tag, max 200 chars ("" if none)
  references: string;      // source URLs/citations, newline-separated ("" if none)
  pending_ingest: boolean; // see note below
  created_at: string;      // ISO-8601 UTC, e.g. "2026-07-03T17:04:36.192322+00:00"
  updated_at: string;      // ISO-8601 UTC
}
```

Real example (from `GET /api/notes`):
```json
{
  "title": "Test",
  "text": "",
  "label": "",
  "references": "",
  "id": 5,
  "pending_ingest": true,
  "created_at": "2026-07-03T17:04:36.192322+00:00",
  "updated_at": "2026-07-03T17:04:36.192322+00:00"
}
```

**`pending_ingest` — a "dirty since last ingest" flag (verified):**
- `true` when a note is created (`POST /api/notes`), edited (`PUT`), or imported — i.e.
  its content is not yet reflected in the Cognee graph.
- `false` after a successful `POST /api/notes/{id}/ingest` (a full rebuild also brings
  every note into the graph, though it does not per-row clear this flag).
- Use it to show a "needs ingest" badge and to decide which notes to ingest.

The request body for create/update (`NoteIn`) is the first four fields only; **all are
optional** and default to `""`:
```ts
interface NoteInput {
  title?: string;      // max 500
  text?: string;
  label?: string;      // max 200
  references?: string;
}
```

---

## NOTES CRUD

### GET `/api/notes`
- **Purpose:** list all notes for the sidebar. Ordered by `updated_at` DESC (newest first).
- **Request:** no params, no body.
- **Success:** `200` → `Note[]`.
- **Errors:** none expected.
```bash
curl http://localhost:8000/api/notes
```

### GET `/api/notes/{note_id}`
- **Purpose:** load one note into the editor.
- **Request:** path param `note_id` (integer). No body.
- **Success:** `200` → `Note`.
- **Errors:** `404 {"detail":"Note not found"}`; `422` if `note_id` isn't an integer.
```bash
curl http://localhost:8000/api/notes/5
```

### POST `/api/notes`
- **Purpose:** create a new note.
- **Request:** `application/json`, body = `NoteInput` (all fields optional).
- **Success:** `201` → `Note` (the created row, with `id`, timestamps, `pending_ingest:false`).
- **Errors:** `422` if `title` > 500 or `label` > 200 chars, or body malformed.
```bash
curl -X POST http://localhost:8000/api/notes \
  -H "Content-Type: application/json" \
  -d '{"title":"Atomic Notes","text":"One idea per note.","label":"method","references":""}'
```

### PUT `/api/notes/{note_id}`
- **Purpose:** save edits to an existing note. Replaces **all four** fields (full-object
  update, not a partial patch — send the whole `NoteInput`).
- **Request:** path param `note_id` (int); `application/json` body = `NoteInput`.
- **Success:** `200` → `Note` (updated; `updated_at` refreshed).
- **Errors:** `404 {"detail":"Note not found"}`; `422` validation.
```bash
curl -X PUT http://localhost:8000/api/notes/5 \
  -H "Content-Type: application/json" \
  -d '{"title":"Atomic Notes","text":"One idea per note. Revised.","label":"method","references":""}'
```

### DELETE `/api/notes/{note_id}`
- **Purpose:** delete a note.
- **Request:** path param `note_id` (int). No body.
- **Success:** `204` with **empty body** (do not `res.json()` — check `res.status === 204`).
- **Errors:** `404 {"detail":"Note not found"}`.
```bash
curl -i -X DELETE http://localhost:8000/api/notes/5
```

---

## COGNEE

Cognee is the knowledge-graph layer. SQLite is the source of truth; the graph is derived
and rebuildable. Every Cognee route returns **`503 {"detail":"Cognee not configured (.env)"}`**
if the server has no tenant keys — call `GET /api/cognee/status` first and gate the UI.

### GET `/api/cognee/status`
- **Purpose:** check whether Cognee features (ingest, search, rebuild) are usable. Call on
  app load; hide/disable those features when `configured` is `false`.
- **Request:** none.
- **Success:** `200` → `{ "configured": boolean }`. Verified: `{"configured":true}`.
- **Errors:** none.
```bash
curl http://localhost:8000/api/cognee/status
```

### POST `/api/notes/{note_id}/ingest`
- **Purpose:** push ONE note into the active Cognee graph (add → cognify) and clear its
  `pending_ingest`. **SLOW** (runs cognify). Use for incremental single-note ingest.
- **Request:** path param `note_id` (int). No body.
- **Success:** `200` → `{ "ingested": <note_id> }` (e.g. `{"ingested":5}`).
- **Errors:**
  - `503 {"detail":"Cognee not configured (.env)"}`
  - `404 {"detail":"Note not found"}`
  - `502 {"detail":"add failed (...): ..."}` or `"cognify failed (...): ..."` — any Cognee error.
- **Sends server-side:** `f"{title}\n\n{text}"` for that note; empty ones become just the title.
```bash
curl -X POST http://localhost:8000/api/notes/5/ingest
```

### POST `/api/search`
- **Purpose:** query the active graph. Two modes map to Cognee `search_type`:
  - `mode:"quick"` → **CHUNKS** — fast vector lookup. `search_result` is an array of **chunk
    objects** (raw matched records), not a prose answer.
  - `mode:"explore"` → **GRAPH_COMPLETION** — graph reasoning. `search_result` is an array of
    **answer strings** (usually one generated answer). Use this for a Q&A box.
- **Request:** `application/json`:
  ```ts
  { query: string;              // required
    mode?: "quick" | "explore"; // optional, default "quick". MUST match /^(quick|explore)$/
  }
  ```
- **Success:** `200`:
  ```ts
  { query: string;
    mode: "quick" | "explore";
    results: Array<{
      dataset_id: string;
      dataset_name: string;
      dataset_tenant_id: string;
      search_result: string[] | object[];  // strings for explore, objects for quick
    }>;
  }
  ```
- **Real example (mode `explore`, verified this session):**
  ```json
  {
    "query": "how do notes become a graph?",
    "mode": "explore",
    "results": [
      {
        "dataset_id": "c0b1e3a2-...-v3",
        "dataset_name": "zettelkeistan_v3",
        "dataset_tenant_id": "b24860eb-...",
        "search_result": [
          "**How notes become a graph**\n1. Treat each note as a node ... "
        ]
      }
    ]
  }
  ```
- **Errors:**
  - `503` not configured.
  - `422` if `mode` is not exactly `quick`/`explore` — shape: `{"detail":[{"type":"string_pattern_mismatch","loc":["body","mode"],...}]}`.
  - `502 {"detail":"search failed (404): ...Search prerequisites not met..."}` if the active
    graph has never been ingested+cognified (search only works after a successful rebuild/ingest).
```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query":"how do notes become a graph?","mode":"explore"}'
```

### GET `/api/cognee/active`
- **Purpose:** which graph dataset the app currently targets (the cutover pointer). Cheap;
  safe to display / poll.
- **Request:** none.
- **Success:** `200` → `{ "active_dataset": string }`. Verified: `{"active_dataset":"zettelkeistan_v3"}`.
- **Errors:** none (works even if Cognee unconfigured — reads local SQLite).
```bash
curl http://localhost:8000/api/cognee/active
```

### POST `/api/cognee/rebuild`
- **Purpose:** rebuild the whole graph from **all** SQLite notes under a fresh dataset name,
  then atomically flip `active_dataset` on success. **VERY SLOW** on a real run (adds every
  note + one cognify). Use `?dry_run=true` to preview safely.
- **Request:** query param `dry_run` (boolean, default `false`). No body.
- **Success (dry run, `?dry_run=true`) — no network/cognify, safe to call freely:**
  ```json
  {"old_dataset":"zettelkeistan_v3","new_dataset":"zettelkeistan_v4","note_count":5,"dry_run":true,"cutover":false}
  ```
- **Success (real run, `dry_run=false`):**
  ```json
  {"old_dataset":"zettelkeistan_v3","new_dataset":"zettelkeistan_v4","note_count":5,"ok":true,"cutover":true}
  ```
  `cutover:true` means the pointer now points at `new_dataset`.
- **Errors:**
  - `503` not configured.
  - `502 {"detail":"cognify failed (...): ..."}` — if the build/cognify fails, the endpoint
    returns 502 and the pointer is **left unchanged** (app keeps using the old graph).
```bash
# preview only (free):
curl -X POST "http://localhost:8000/api/cognee/rebuild?dry_run=true"
# real rebuild (slow, costs tokens):
curl -X POST "http://localhost:8000/api/cognee/rebuild"
```

---

## VAULT (built)

The vault folder is a **sync checkpoint**, not a live second source: import reads files
INTO SQLite, export writes SQLite OUT to files. Markdown mapping: filename = title, body =
text; `label`/`references` are stored as simple frontmatter (`--- label: ... ---`) and
parsed back on import. (Frontmatter handling lives in the frontend `vault.js`; the backend
just takes/returns JSON.)

### POST `/api/notes/import`
- **Purpose:** bulk-insert notes parsed from vault files into SQLite.
- **Request:** `application/json`, body = **array** of note inputs:
  ```ts
  Array<{ title?: string; text?: string; label?: string; references?: string }>
  ```
  (`title` max 500, `label` max 200 → `422` otherwise.)
- **Behavior:** skips **exact duplicates** (same `title` AND `text`) — both vs. existing rows
  and within the batch. Inserted notes get `pending_ingest = true`. Import is **lenient**:
  over-long `title`/`label` are clamped (500/200 chars) rather than rejected, so one bad
  file never fails the whole batch.
- **Success:** `200` → `{ "imported": <number actually inserted> }`. Empty array → `{"imported":0}`.
- **Errors:** `422` only if the body isn't a JSON array of objects.
```bash
curl -X POST http://localhost:8000/api/notes/import \
  -H "Content-Type: application/json" \
  -d '[{"title":"Atomic Notes","text":"One idea per note.","label":"method"},
       {"title":"Linking","text":"Notes connect via links.","references":"http://a\nhttp://b"}]'
```

### GET `/api/notes/export`
- **Purpose:** get all notes as JSON so the frontend can write them out as `.md` files.
- **Request:** none.
- **Success:** `200` → `Note[]` (identical shape to `GET /api/notes`).
- **Errors:** none.
```bash
curl http://localhost:8000/api/notes/export
```

---

## SETTINGS (built)

Key/value UI-state persistence in SQLite (`settings` table). Values are **strings** —
serialize/parse yourself. **Only a whitelist of keys is accessible** (so the frontend can't
clobber internal pointers like the Cognee `active_dataset` cutover pointer):
**`last_open_note_id`** and **`active_vault`**. Any other key is rejected.

### GET `/api/settings/{key}`
- **Purpose:** read a persisted UI setting.
- **Request:** path param `key` (must be whitelisted); optional query `default` (string,
  returned if key unset).
- **Success:** `200` → `{ "key": string, "value": string }`. Verified:
  `{"key":"last_open_note_id","value":"3"}`. If unset and no `default`, `value` is `""`.
- **Errors:** `404 {"detail":"Unknown setting"}` for a non-whitelisted key.
```bash
curl "http://localhost:8000/api/settings/last_open_note_id?default="
```

### POST `/api/settings/{key}`
- **Purpose:** write a persisted UI setting (upsert).
- **Request:** path param `key` (must be whitelisted); `application/json` body
  `{ "value": string }` (defaults to `""`).
- **Success:** `200` → `{ "key": string, "value": string }`.
- **Errors:** `403 {"detail":"Setting is not writable"}` for a non-whitelisted key.
```bash
curl -X POST http://localhost:8000/api/settings/last_open_note_id \
  -H "Content-Type: application/json" -d '{"value":"5"}'
```

---

## Utility / non-API

- **GET `/api/health`** → `200 {"status":"ok"}`. Liveness check.
- **`/assets/*`** and **`GET /{anything else}` → `index.html`**: SPA static serving + client-side
  routing fallback (only in the single-container prod build). **Consequence:** any real
  backend endpoint MUST live under `/api` — anything else is swallowed by the SPA fallback,
  and a wrong-method call to a removed API path may return `405` instead of `404`.
- The old `/api/cognee/debug` endpoint has been **removed** — do not use it.

---

## B. SEARCH RESULT PARSING

The answer is nested: `response.results[0].search_result`. For **`explore`** (GRAPH_COMPLETION)
`search_result` is an **array of strings** (the generated answer). For **`quick`** (CHUNKS)
it's an array of chunk **objects**.

```js
async function search(query, mode = "explore") {
  const res = await fetch("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, mode }),
  });
  if (!res.ok) {
    const { detail } = await res.json().catch(() => ({}));
    throw new Error(typeof detail === "string" ? detail : `Search failed (${res.status})`);
  }
  const data = await res.json();
  const first = data.results?.[0];
  const hits = first?.search_result ?? [];

  if (mode === "explore") {
    // array of answer strings -> join into one markdown answer
    return { answer: hits.filter((h) => typeof h === "string").join("\n\n"), raw: data };
  }
  // quick/CHUNKS -> array of chunk objects; render however you like
  return { chunks: hits, raw: data };
}
```

Guard for the empty/never-built case: `results` may be `[]` or `search_result` empty if the
active graph has no content — show "no results yet, ingest/rebuild first."

---

## C. FRONTEND GOTCHAS

1. **Same-origin — use relative `/api` URLs.** The build is served by the same server. CORS is
   scoped to the dev origins `http://localhost:5173` / `http://127.0.0.1:5173` only (not `*`),
   so cross-origin calls from other sites are blocked. If you run the Vite dev server on a
   different host/port, set `ZK_CORS_ORIGINS` (comma-separated) on the backend.
2. **`ingest` and real `rebuild` are SLOW** (they run Cognee `cognify`, multi-second to
   minutes). The UI **must** show a loading state and not block; disable the button while in
   flight. Use a long client timeout. Never fire these in a loop.
3. **Safe to poll / cheap:** `GET /api/health`, `/api/notes`, `/api/notes/{id}`,
   `/api/notes/export`, `/api/cognee/status`, `/api/cognee/active`, `/api/settings/{key}`, and
   `POST /api/cognee/rebuild?dry_run=true`. **Expensive (never poll):** `POST /api/notes/{id}/ingest`,
   `POST /api/cognee/rebuild` (real), `POST /api/search` (moderate — user-triggered only).
4. **Search only works after content is ingested AND cognified.** A fresh/empty active graph
   returns `502 ... "Search prerequisites not met"`. Gate the search box on there being an
   active, built graph (or just handle the 502 gracefully).
5. **Cognee gating:** if `GET /api/cognee/status` → `{"configured":false}`, every Cognee call
   returns `503`. Hide ingest/search/rebuild instead of surfacing errors.
6. **DELETE returns `204` with no body** — check status, don't parse JSON.
7. **Error shape:** most errors are `{"detail": "<string>"}`; validation errors (`422`) are
   `{"detail": [ {type, loc, msg, ...} ]}` (an array). Handle both.
8. **`references` is a single newline-separated string**, not an array. Split on `\n` to render
   as a list.
9. **`pending_ingest`** is a real dirty flag: `true` after create/edit/import, `false` after a
   successful single-note ingest. Use it for a "needs ingest" badge.

---

## D. TYPICAL FLOWS

**Create and ingest a single note**
```
POST /api/notes            -> { id }          // create
POST /api/notes/{id}/ingest                    // slow: show spinner; on 200 the note is in the graph
GET  /api/notes            -> refresh sidebar  // pending_ingest now false
```

**Run a search**
```
GET  /api/cognee/status    -> ensure configured:true
POST /api/search {query, mode:"explore"}       // parse results[0].search_result (section B)
```

**Full rebuild from SQLite (after importing a vault, or switching embedder)**
```
POST /api/notes/import [ ... ]   -> { imported }        // load files into SQLite
POST /api/cognee/rebuild?dry_run=true                    // preview new_dataset + note_count (free)
POST /api/cognee/rebuild                                 // SLOW: one cognify; spinner; 200 => cutover:true
GET  /api/cognee/active   -> confirm active_dataset moved to the new name
POST /api/search {query, mode:"explore"}                 // now grounded in the rebuilt graph
```

**Restore UI state on load**
```
GET  /api/notes                                          // populate sidebar
GET  /api/settings/last_open_note_id  -> { value }       // reopen that note if it still exists
// (cursor position within a note is kept in localStorage, per-browser — not on the server)
```

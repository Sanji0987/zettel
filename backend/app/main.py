"""FastAPI backend — read/write path over SQLite, plus Cognee rebuild cutover.

In the single-container build, this also serves the compiled React app
from /app/static. In dev (two-server), CORS is open so Vite on :5173
can call the API on :8000.
"""
import asyncio
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from . import cognee_client
from . import rebuild
from . import relay


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Zettelkeistan API", version="0.1.0", lifespan=lifespan)

# The prod build serves the frontend same-origin, so no CORS is needed there. This
# only opens the API to the Vite dev server. Scoped to explicit localhost dev origins
# (NOT "*") so arbitrary websites can't read your notes from your browser. Override
# with ZK_CORS_ORIGINS (comma-separated) if you run the dev server elsewhere.
_default_dev_origins = "http://localhost:5173,http://127.0.0.1:5173"
CORS_ORIGINS = [o.strip() for o in os.environ.get("ZK_CORS_ORIGINS", _default_dev_origins).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class NoteIn(BaseModel):
    title: str = Field(default="", max_length=500)
    text: str = Field(default="")
    label: str = Field(default="", max_length=200)
    references: str = Field(default="")


class NoteOut(NoteIn):
    id: int
    pending_ingest: bool
    created_at: str
    updated_at: str


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/notes", response_model=list[NoteOut])
def list_notes() -> list[dict]:
    return db.list_notes()


@app.post("/api/notes", response_model=NoteOut, status_code=201)
def create_note(note: NoteIn) -> dict:
    return db.create_note(note.title, note.text, note.label, note.references)


class NoteImport(BaseModel):
    # Lenient by design: import shouldn't reject the whole batch over one long
    # filename/title. db.import_notes clamps title/label to the stored limits.
    title: str = ""
    text: str = ""
    label: str = ""
    references: str = ""


@app.post("/api/notes/import")
def import_notes(items: list[NoteImport]) -> dict:
    """Bulk-import notes from a vault (files -> SQLite). Skips exact dup title+text.

    SQLite stays the source of truth; the vault folder is a sync checkpoint, not a
    live second source. Imported notes are marked pending_ingest for the next rebuild.
    """
    count = db.import_notes([i.model_dump() for i in items])
    return {"imported": count}


@app.get("/api/notes/export", response_model=list[NoteOut])
def export_notes() -> list[dict]:
    """All notes as JSON (SQLite -> files). The frontend turns these into .md files."""
    return db.list_notes()


@app.get("/api/notes/{note_id}", response_model=NoteOut)
def get_note(note_id: int) -> dict:
    n = db.get_note(note_id)
    if n is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return n


@app.put("/api/notes/{note_id}", response_model=NoteOut)
def update_note(note_id: int, note: NoteIn) -> dict:
    n = db.update_note(note_id, note.title, note.text, note.label, note.references)
    if n is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return n


@app.delete("/api/notes/{note_id}", status_code=204)
def delete_note(note_id: int) -> None:
    if not db.delete_note(note_id):
        raise HTTPException(status_code=404, detail="Note not found")


# ---- Cognee integration (Phase 2) --------------------------------------

class SearchIn(BaseModel):
    query: str
    mode: str = Field(default="quick", pattern="^(quick|explore)$")


@app.get("/api/cognee/status")
def cognee_status() -> dict:
    return {"configured": cognee_client.is_configured()}


@app.post("/api/notes/{note_id}/ingest")
async def ingest_note(note_id: int) -> dict:
    """Push one note's text to Cognee, then clear its pending flag."""
    if not cognee_client.is_configured():
        raise HTTPException(status_code=503, detail="Cognee not configured (.env)")
    note = db.get_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    payload = f"{note['title']}\n\n{note['text']}".strip()
    try:
        # Chunk (only if large) and add each chunk under node_set "note_<id>", then
        # cognify ONCE. Small notes stay a single chunk.
        await cognee_client.add_note_chunks(note_id, payload)
        await cognee_client.cognify()
    except cognee_client.CogneeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    # Clear the pending flag only. (Don't call update_note here — that would bump
    # updated_at and reshuffle the sidebar as if the note had been edited.)
    db.clear_pending_ingest(note_id)
    return {"ingested": note_id}


# ---- UI state persistence (SQLite settings table) ----------------------

# Only these keys are readable/writable via the public settings API. This keeps the
# frontend from clobbering internal pointers like "active_dataset" (the Cognee cutover
# pointer), which must only ever be moved by a successful rebuild.
ALLOWED_SETTINGS = {"last_open_note_id", "active_vault"}


class SettingIn(BaseModel):
    value: str = ""


@app.get("/api/settings/{key}")
def read_setting(key: str, default: str = "") -> dict:
    if key not in ALLOWED_SETTINGS:
        raise HTTPException(status_code=404, detail="Unknown setting")
    return {"key": key, "value": db.get_setting(key, default)}


@app.post("/api/settings/{key}")
def write_setting(key: str, body: SettingIn) -> dict:
    if key not in ALLOWED_SETTINGS:
        raise HTTPException(status_code=403, detail="Setting is not writable")
    db.set_setting(key, body.value)
    return {"key": key, "value": body.value}


@app.get("/api/cognee/active")
def active_dataset() -> dict:
    """Which Cognee graph the app currently targets (the cutover pointer)."""
    return {"active_dataset": cognee_client.active_dataset()}


@app.post("/api/cognee/rebuild")
async def rebuild_dataset(dry_run: bool = False) -> dict:
    """Rebuild the graph from SQLite under a fresh name and cut over on success.

    Thin delegate: the pointer read/write lives in rebuild_dataset(), the heavy
    build loop is isolated there too (liftable to n8n later). Pass ?dry_run=true
    to preview the new name + note count without calling cognify.
    """
    if not cognee_client.is_configured():
        raise HTTPException(status_code=503, detail="Cognee not configured (.env)")
    result = await rebuild.rebuild_dataset(dry_run=dry_run)
    if result.get("ok") is False:
        raise HTTPException(status_code=502, detail=result.get("error", "rebuild failed"))
    return result


@app.post("/api/search")
async def search(body: SearchIn) -> dict:
    if not cognee_client.is_configured():
        raise HTTPException(status_code=503, detail="Cognee not configured (.env)")
    try:
        result = await cognee_client.search(body.query, body.mode)
    except cognee_client.CogneeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"query": body.query, "mode": body.mode, "results": result}


# ---- Ollama status + chat relay (n8n brain not built yet) ---------------

@app.get("/api/ollama/status")
async def ollama_status() -> dict:
    """Whether the existing ollama container is reachable + its pulled models.

    Lists tags only — never invokes a model. Used by the UI to enable/disable chat.
    """
    return await relay.ollama_tags()


class ChatIn(BaseModel):
    message: str
    mode: str = Field(default="read", pattern="^(read|write)$")
    history: list[dict] = Field(default_factory=list)


@app.post("/api/chat")
async def chat(body: ChatIn) -> dict:
    """Thin relay to the n8n chat webhook (or a mock until it's wired).

    No AI/Ollama/Cognee logic here — FastAPI just forwards {message, mode, history}
    and passes {reply, mode, sources} back. mode is the explicit user toggle.
    """
    try:
        return await relay.chat(body.message, body.mode, body.history)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"chat webhook error: {e}")


# ---- Sync layer (queue + cron-driven worker) ---------------------------
# SQLite is the source of truth; the Cognee graph is derived. The sync worker pushes
# pending notes into the graph and syncs deletions out. cognify is the expensive step,
# so it runs at most ONCE per /run and ONLY when >=1 note was actually added.

# Single-flight: the n8n cron and a manual trigger must never run /sync/run at once
# (a concurrent double-cognify is exactly the cost mistake we're guarding against).
_sync_lock = asyncio.Lock()

# One /run pulls at most this many of each queue. Keeps a single sweep bounded.
SYNC_BATCH = int(os.environ.get("SYNC_BATCH", "200"))


@app.get("/api/sync/status")
def sync_status() -> dict:
    """Queue depths. The cron worker checks this FIRST and does nothing (never cognifies)
    when both pending and pending_removals are 0."""
    return db.sync_counts()


@app.get("/api/sync/failed")
def sync_failed() -> dict:
    """Quarantined failed notes + their error reasons, for user review before requeue."""
    return {"failed": db.list_failed()}


@app.post("/api/sync/failed/{note_id}/requeue")
def sync_requeue(note_id: int) -> dict:
    if not db.requeue_failed(note_id):
        raise HTTPException(status_code=404, detail="No failed note with that id")
    return {"requeued": note_id}


@app.get("/api/sync/pending/next")
def sync_pending_next(limit: int = 50) -> dict:
    """Peek the next batch of pending notes (for an external per-item orchestrator)."""
    return {"items": db.list_pending(limit)}


class PendingAckIn(BaseModel):
    done: list[int] = Field(default_factory=list)
    failed: list[dict] = Field(default_factory=list)  # [{"id": int, "reason": str}]


@app.post("/api/sync/pending/ack")
def sync_pending_ack(body: PendingAckIn) -> dict:
    """Per-item ack for the granular path. NOTE: marking done here does not cognify —
    an external orchestrator using this path owns its own cognify. The built-in worker
    uses /api/sync/run instead, which cognifies once."""
    done = db.mark_done(body.done)
    failed = 0
    for f in body.failed:
        try:
            db.mark_failed(int(f["id"]), str(f.get("reason", "")))
            failed += 1
        except (KeyError, ValueError, TypeError):
            continue
    return {"marked_done": done, "marked_failed": failed}


@app.get("/api/sync/removal/next")
def sync_removal_next(limit: int = 50) -> dict:
    return {"items": db.list_pending_removals(limit)}


class RemovalAckIn(BaseModel):
    done: list[int] = Field(default_factory=list)


@app.post("/api/sync/removal/ack")
def sync_removal_ack(body: RemovalAckIn) -> dict:
    return {"marked_done": db.mark_removals_done(body.done)}


async def _do_sync_run() -> dict:
    """One full sweep: add pending notes (chunked, grouped by node_set), sync removals
    out, then cognify ONCE — only if at least one note was successfully added. Notes are
    marked done only AFTER cognify succeeds (before that they aren't searchable)."""
    counts = db.sync_counts()
    if counts["pending"] == 0 and counts["pending_removals"] == 0:
        # #1 cost protection: never cognify on an empty queue.
        return {"ran": True, "empty": True, "added_notes": 0, "chunks": 0,
                "removed": 0, "failed": 0, "cognified": False}

    pending = db.list_pending(SYNC_BATCH)
    added_ids: list[int] = []
    total_chunks = 0
    failed = 0
    for note in pending:
        payload = f"{note['title']}\n\n{note['text']}".strip()
        try:
            if payload:
                total_chunks += await cognee_client.add_note_chunks(note["id"], payload)
            added_ids.append(note["id"])
        except cognee_client.CogneeError as e:
            db.mark_failed(note["id"], str(e))
            failed += 1
        except Exception as e:  # noqa: BLE001 - quarantine, don't crash the sweep
            db.mark_failed(note["id"], f"{type(e).__name__}: {e}")
            failed += 1

    # Removals: best-effort delete from the graph. A hard 4xx (incremental delete not
    # supported) still clears the tombstone — a full rebuild is the real reconciliation.
    removals = db.list_pending_removals(SYNC_BATCH)
    removal_done: list[int] = []
    for rm in removals:
        try:
            res = await cognee_client.delete_note_data(rm["note_id"])
            if res["status"] < 500:
                removal_done.append(rm["id"])
            # transient 5xx -> leave pending for next cycle
        except httpx.HTTPError:
            pass  # network blip -> retry next cycle

    # cognify ONCE, only if new data actually landed. This is the single gate that keeps
    # the hands-off timer from silently burning the token budget.
    cognified = False
    if added_ids:
        try:
            await cognee_client.cognify()
            cognified = True
            db.mark_done(added_ids)
        except cognee_client.CogneeError as e:
            for nid in added_ids:
                db.mark_failed(nid, f"cognify failed: {e}")
            failed += len(added_ids)

    if removal_done:
        db.mark_removals_done(removal_done)

    return {"ran": True, "empty": False, "added_notes": len(added_ids),
            "chunks": total_chunks, "removed": len(removal_done),
            "failed": failed, "cognified": cognified}


@app.post("/api/sync/run")
async def sync_run() -> dict:
    """Manual/cron trigger for a full sweep. Single-flight: returns 409 if a sweep
    (cron or manual) is already in progress."""
    if not cognee_client.is_configured():
        raise HTTPException(status_code=503, detail="Cognee not configured (.env)")
    if _sync_lock.locked():
        raise HTTPException(status_code=409, detail="sync already running")
    async with _sync_lock:
        return await _do_sync_run()


# ---- Static React (single-container prod). Mounted only if the build exists.
STATIC_DIR = os.environ.get("ZK_STATIC_DIR", "/app/static")

if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        # Let unknown non-API routes fall through to index.html (client-side routing).
        index = os.path.join(STATIC_DIR, "index.html")
        return FileResponse(index)

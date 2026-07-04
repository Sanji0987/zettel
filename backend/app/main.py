"""FastAPI backend — read/write path over SQLite, plus Cognee ingest/search.

In the single-container build, this also serves the compiled React app
from /app/static. In dev (two-server), CORS is open so Vite on :5173
can call the API on :8000.
"""
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from . import cognee_client
from . import rebuild


app = FastAPI(title="Zettelkeistan API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    db.init_db()


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
    title: str = Field(default="", max_length=500)
    text: str = Field(default="")
    label: str = Field(default="", max_length=200)
    references: str = Field(default="")


@app.post("/api/notes/import")
def import_notes(items: list[NoteImport]) -> dict:
    """Bulk-import notes from a vault (files -> SQLite). Skips exact dup title+text.

    SQLite stays the source of truth; the vault folder is a sync checkpoint.
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


# ---- Cognee integration -------------------------------------------------

class SearchIn(BaseModel):
    query: str
    mode: str = Field(default="quick", pattern="^(quick|explore)$")


@app.get("/api/cognee/status")
def cognee_status() -> dict:
    return {"configured": cognee_client.is_configured()}


@app.post("/api/notes/{note_id}/ingest")
async def ingest_note(note_id: int) -> dict:
    """Push one note's text to Cognee."""
    if not cognee_client.is_configured():
        raise HTTPException(status_code=503, detail="Cognee not configured (.env)")
    note = db.get_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    payload = f"{note['title']}\n\n{note['text']}".strip()
    try:
        await cognee_client.add(payload)
        await cognee_client.cognify()
    except cognee_client.CogneeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ingested": note_id}


@app.post("/api/search")
async def search(body: SearchIn) -> dict:
    if not cognee_client.is_configured():
        raise HTTPException(status_code=503, detail="Cognee not configured (.env)")
    try:
        result = await cognee_client.search(body.query, body.mode)
    except cognee_client.CogneeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"query": body.query, "mode": body.mode, "results": result}


@app.get("/api/cognee/active")
def active_dataset() -> dict:
    """Which Cognee graph the app currently targets (the cutover pointer)."""
    return {"active_dataset": cognee_client.active_dataset()}


@app.post("/api/cognee/rebuild")
async def rebuild_dataset(dry_run: bool = False) -> dict:
    """Rebuild the graph from SQLite under a fresh name and cut over on success.

    Thin delegate: the pointer read/write and the heavy build loop live in
    rebuild_dataset() (liftable to n8n later). Pass ?dry_run=true to preview.
    """
    if not cognee_client.is_configured():
        raise HTTPException(status_code=503, detail="Cognee not configured (.env)")
    result = await rebuild.rebuild_dataset(dry_run=dry_run)
    if result.get("ok") is False:
        raise HTTPException(status_code=502, detail=result.get("error", "rebuild failed"))
    return result


# ---- Static React (single-container prod). Mounted only if the build exists.
STATIC_DIR = os.environ.get("ZK_STATIC_DIR", "/app/static")

if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        # Let unknown non-API routes fall through to index.html (client-side routing).
        index = os.path.join(STATIC_DIR, "index.html")
        return FileResponse(index)

"""SQLite layer — canonical source of truth for notes.

Phase 1: plain notes store. The fields the later Cognee/n8n phases need
(label, references, pending_ingest) are baked in now so no migration is
required later. pending_ingest is unused in Phase 1 but present and indexed.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("ZK_DB_PATH", "/data/zettelkeistan.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT    NOT NULL DEFAULT '',
    text          TEXT    NOT NULL DEFAULT '',
    label         TEXT    NOT NULL DEFAULT '',
    references_   TEXT    NOT NULL DEFAULT '',   -- source URLs / citations, newline-separated
    pending_ingest INTEGER NOT NULL DEFAULT 0,   -- Phase 2+: sweep flag for Cognee
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_pending ON notes(pending_ingest);
CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at);

-- Settings table: unused in Phase 1, ready for the three model slots later.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # WAL: better concurrency for the single-user local case.
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_note(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "text": row["text"],
        "label": row["label"],
        "references": row["references_"],
        "pending_ingest": bool(row["pending_ingest"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_note(title: str, text: str, label: str = "", references: str = "") -> dict:
    ts = _now()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO notes (title, text, label, references_, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, text, label, references, ts, ts),
        )
        note_id = cur.lastrowid
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _row_to_note(row)


def list_notes() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM notes ORDER BY updated_at DESC").fetchall()
        return [_row_to_note(r) for r in rows]


def get_note(note_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _row_to_note(row) if row else None


def update_note(note_id: int, title: str, text: str, label: str, references: str) -> dict | None:
    with get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not exists:
            return None
        conn.execute(
            """UPDATE notes
               SET title = ?, text = ?, label = ?, references_ = ?, updated_at = ?
               WHERE id = ?""",
            (title, text, label, references, _now(), note_id),
        )
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _row_to_note(row)


def delete_note(note_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return cur.rowcount > 0


def import_notes(items: list[dict]) -> int:
    """Bulk-insert notes from a vault import. Returns the count actually inserted.

    Skips exact duplicates (same title AND text) — both against existing rows and
    within the batch.
    """
    ts = _now()
    inserted = 0
    with get_conn() as conn:
        seen = {
            (r["title"], r["text"])
            for r in conn.execute("SELECT title, text FROM notes").fetchall()
        }
        for it in items:
            title = it.get("title", "") or ""
            text = it.get("text", "") or ""
            key = (title, text)
            if key in seen:
                continue
            conn.execute(
                """INSERT INTO notes (title, text, label, references_, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (title, text, it.get("label", "") or "", it.get("references", "") or "", ts, ts),
            )
            seen.add(key)
            inserted += 1
    return inserted


# ---- settings (key/value) ------------------------------------------------
# Small typed accessors over the existing `settings` table. Used for the
# Cognee "active_dataset" pointer (which graph the app currently targets).


def get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row is not None else default


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )

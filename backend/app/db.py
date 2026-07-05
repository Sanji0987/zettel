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

-- Sync tombstones: a deleted note that was already in the graph leaves a row here
-- so the removal can be synced OUT to Cognee (the note itself is gone from `notes`).
CREATE TABLE IF NOT EXISTS note_removals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id    INTEGER NOT NULL,
    node_set   TEXT    NOT NULL,             -- the "note_<id>" group tag to delete
    status     TEXT    NOT NULL DEFAULT 'pending',   -- pending | done
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_removals_status ON note_removals(status);
"""


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn) -> None:
    """Add the sync-status columns to `notes` on existing DBs (CREATE IF NOT EXISTS
    won't alter an existing table). Idempotent — safe to run every startup.

    Sync state model: pending -> done | failed.
      - pending: created/edited, not yet in the graph.
      - done:    successfully added + cognified.
      - failed:  a sync attempt errored; quarantined for user review, never retried
                 blindly, never dropped.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(notes)").fetchall()}
    if "sync_status" not in cols:
        conn.execute("ALTER TABLE notes ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'pending'")
        # Backfill: notes already ingested (pending_ingest=0) are 'done'; the rest pending.
        conn.execute("UPDATE notes SET sync_status = 'done' WHERE pending_ingest = 0")
    if "sync_error" not in cols:
        conn.execute("ALTER TABLE notes ADD COLUMN sync_error TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_sync ON notes(sync_status)")


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
    keys = row.keys()
    return {
        "id": row["id"],
        "title": row["title"],
        "text": row["text"],
        "label": row["label"],
        "references": row["references_"],
        "pending_ingest": bool(row["pending_ingest"]),
        # sync_* may be absent on a row selected before migration in odd cases; default safely.
        "sync_status": row["sync_status"] if "sync_status" in keys else "pending",
        "sync_error": row["sync_error"] if "sync_error" in keys else "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# Stored-column limits (mirrored by the API validation for create/update).
TITLE_MAX = 500
LABEL_MAX = 200


def create_note(title: str, text: str, label: str = "", references: str = "") -> dict:
    ts = _now()
    with get_conn() as conn:
        # New note starts pending_ingest=1: it isn't in the graph until ingested/rebuilt.
        cur = conn.execute(
            """INSERT INTO notes (title, text, label, references_, pending_ingest, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)""",
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
        # Edited content is out of sync with the graph -> back to pending (and clear any
        # prior failure so a fixed note re-enters the queue).
        conn.execute(
            """UPDATE notes
               SET title = ?, text = ?, label = ?, references_ = ?,
                   pending_ingest = 1, sync_status = 'pending', sync_error = '', updated_at = ?
               WHERE id = ?""",
            (title, text, label, references, _now(), note_id),
        )
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _row_to_note(row)


def clear_pending_ingest(note_id: int) -> None:
    """Mark a note as ingested (called after a successful Cognee add+cognify)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE notes SET pending_ingest = 0, sync_status = 'done', sync_error = '' WHERE id = ?",
            (note_id,),
        )


def delete_note(note_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT sync_status FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            return False
        # Only tombstone notes that actually made it into the graph. A note deleted while
        # still pending/failed was never added, so there's nothing to sync out.
        if row["sync_status"] == "done":
            conn.execute(
                """INSERT INTO note_removals (note_id, node_set, status, created_at)
                   VALUES (?, ?, 'pending', ?)""",
                (note_id, f"note_{note_id}", _now()),
            )
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return True


# ---- sync queue (pending / done / failed + removal tombstones) ----------
# The sync worker (n8n cron -> POST /api/sync/run) drives these. FAILED notes are
# quarantined for user review — never retried blindly, never silently dropped.


def list_pending(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notes WHERE sync_status = 'pending' ORDER BY updated_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_note(r) for r in rows]


def mark_done(ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE notes SET sync_status = 'done', pending_ingest = 0, sync_error = '' "
            f"WHERE id IN ({placeholders})",
            tuple(ids),
        )
        return cur.rowcount


def mark_failed(note_id: int, reason: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE notes SET sync_status = 'failed', sync_error = ?, pending_ingest = 1 WHERE id = ?",
            ((reason or "")[:1000], note_id),
        )


def list_failed() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notes WHERE sync_status = 'failed' ORDER BY updated_at DESC"
        ).fetchall()
        return [_row_to_note(r) for r in rows]


def requeue_failed(note_id: int) -> bool:
    """Move a quarantined failed note back to pending (user reviewed it). No-op unless
    the note is currently failed."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE notes SET sync_status = 'pending', sync_error = '', pending_ingest = 1 "
            "WHERE id = ? AND sync_status = 'failed'",
            (note_id,),
        )
        return cur.rowcount > 0


def list_pending_removals(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM note_removals WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"id": r["id"], "note_id": r["note_id"], "node_set": r["node_set"], "created_at": r["created_at"]}
            for r in rows
        ]


def mark_removals_done(ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE note_removals SET status = 'done' WHERE id IN ({placeholders})",
            tuple(ids),
        )
        return cur.rowcount


def sync_counts() -> dict:
    with get_conn() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM notes WHERE sync_status = 'pending'"
        ).fetchone()["c"]
        failed = conn.execute(
            "SELECT COUNT(*) AS c FROM notes WHERE sync_status = 'failed'"
        ).fetchone()["c"]
        removals = conn.execute(
            "SELECT COUNT(*) AS c FROM note_removals WHERE status = 'pending'"
        ).fetchone()["c"]
        return {"pending": pending, "failed": failed, "pending_removals": removals}


def import_notes(items: list[dict]) -> int:
    """Bulk-insert notes from a vault import. Returns the count actually inserted.

    Skips exact duplicates (same title AND text) — both against existing rows and
    within the batch. Imported notes get pending_ingest=1 so they flow into the
    next Cognee rebuild.
    """
    ts = _now()
    inserted = 0
    with get_conn() as conn:
        seen = {
            (r["title"], r["text"])
            for r in conn.execute("SELECT title, text FROM notes").fetchall()
        }
        for it in items:
            title = (it.get("title", "") or "")[:TITLE_MAX]
            text = it.get("text", "") or ""
            key = (title, text)
            if key in seen:
                continue
            conn.execute(
                """INSERT INTO notes (title, text, label, references_, pending_ingest, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?)""",
                (title, text, (it.get("label", "") or "")[:LABEL_MAX], it.get("references", "") or "", ts, ts),
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

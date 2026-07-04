"""Atomic Cognee rebuild + cutover.

Design (see API_CONTRACT / architecture notes):
  - SQLite is the source of truth. The Cognee graph is derived and rebuildable.
  - In-place rebuild (delete + recreate the SAME dataset id) fails upstream with a
    server-side 500 (UndefinedTableError). The working strategy is to rebuild under
    a FRESH name -> new id, then flip the "active_dataset" pointer once cognify
    succeeds. The old dataset is left intact as a fallback (not auto-deleted).

Layering / n8n note:
  - The pointer READ (which dataset are we on) and WRITE (the cutover) belong to
    FastAPI's read path and stay here in `rebuild_dataset()`.
  - The heavy BUILD LOOP (add every note + cognify) is isolated in `_build_graph()`
    so it can later be lifted into an n8n workflow that owns the build path. FastAPI
    would then just read the pointer, trigger n8n, and flip the pointer on success.
  - Nothing here lives inline in a request handler — the endpoint delegates to
    `rebuild_dataset()`.
"""
import re

from . import db
from . import cognee_client


def _next_dataset_name(current: str) -> str:
    """Mint the next versioned name. `foo` -> `foo_v2`, `foo_v2` -> `foo_v3`."""
    m = re.match(r"^(.*)_v(\d+)$", current)
    if m:
        base, n = m.group(1), int(m.group(2))
        return f"{base}_v{n + 1}"
    return f"{current}_v2"


async def _fresh_unused_name(start: str) -> str:
    """Bump the candidate name past any dataset that already exists on the tenant.

    Guards the retry case: if a previous rebuild died mid-build leaving a half-made
    `_v4`, the next attempt must NOT reuse `_v4` (its deterministic id is poisoned and
    re-adding into it can hit the tenant's delete/recreate corruption). Skip to `_v5`.
    """
    try:
        existing = {
            d.get("name")
            for d in await cognee_client.list_datasets()
            if isinstance(d, dict)
        }
    except cognee_client.CogneeError:
        existing = set()  # can't list -> fall back to the naive next name
    name = start
    while name in existing:
        name = _next_dataset_name(name)
    return name


def _note_payload(note: dict) -> str:
    """Same shape the single-note ingest uses: title + blank line + text."""
    return f"{note['title']}\n\n{note['text']}".strip()


async def _build_graph(new_name: str, notes: list[dict]) -> None:
    """The liftable build loop: create the dataset, add every note, cognify ONCE.

    Runs cognify exactly one time. Raises cognee_client.CogneeError on failure so
    the caller can decide NOT to flip the pointer. This is the unit that would move
    to an n8n workflow.
    """
    await cognee_client.ensure_dataset(new_name)
    for note in notes:
        payload = _note_payload(note)
        if payload:
            await cognee_client.add(payload, dataset=new_name)
    await cognee_client.cognify(new_name)  # ONCE — never loop this (costs tokens).


async def rebuild_dataset(dry_run: bool = False) -> dict:
    """Rebuild the graph from SQLite under a fresh name and atomically cut over.

    dry_run=True logs what it WOULD do (new name, note count) and touches no
    network / no cognify — safe to call freely.

    On a real run: only if cognify succeeds is the "active_dataset" pointer flipped.
    If cognify fails, the pointer is left untouched (app keeps using the old graph)
    and the error is returned. The old dataset is never auto-deleted.
    """
    old_name = db.get_setting(cognee_client.ACTIVE_DATASET_KEY, cognee_client.DEFAULT_DATASET)
    new_name = _next_dataset_name(old_name)
    notes = db.list_notes()
    note_count = len(notes)

    if dry_run:
        # Preview only — stays network-free. The real run may bump new_dataset if this
        # name is already taken (see _fresh_unused_name).
        return {"old_dataset": old_name, "new_dataset": new_name, "note_count": note_count,
                "dry_run": True, "cutover": False}

    new_name = await _fresh_unused_name(new_name)
    base = {"old_dataset": old_name, "new_dataset": new_name, "note_count": note_count}

    try:
        await _build_graph(new_name, notes)
    except cognee_client.CogneeError as e:
        # Build failed -> do NOT flip the pointer; app stays on the old graph.
        return {**base, "ok": False, "cutover": False, "error": str(e)}

    # Cutover: this is the only line that changes what the app targets.
    db.set_setting(cognee_client.ACTIVE_DATASET_KEY, new_name)
    return {**base, "ok": True, "cutover": True}

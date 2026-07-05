"""Cognee Cloud client — REST wrapper.

Config from env (never hardcoded):
  COGNEE_BASE_URL   https://tenant-xxxx.aws.cognee.ai
  COGNEE_TENANT_ID  tenant UUID
  COGNEE_API_KEY    your (rotated) key

Cloud endpoints. Auth via X-Api-Key + X-Tenant-Id.
  POST /api/v1/datasets   JSON {"name"}                            -> create-or-return
  POST /api/v1/add        multipart: file field `data` + form `datasetName`
  POST /api/v1/cognify    JSON {"datasets": [name]}
  POST /api/v1/search     JSON {"query", "search_type", "datasets": [name]}
  DELETE /api/v1/datasets/{id}
"""
import os
import httpx

from . import db
from . import chunking

BASE_URL = os.environ.get("COGNEE_BASE_URL", "").rstrip("/")
TENANT_ID = os.environ.get("COGNEE_TENANT_ID", "")
API_KEY = os.environ.get("COGNEE_API_KEY", "")
DEFAULT_DATASET = os.environ.get("COGNEE_DATASET", "zettelkeistan")

# Key in the SQLite `settings` table holding the currently-active dataset name.
ACTIVE_DATASET_KEY = "active_dataset"


def active_dataset() -> str:
    """The dataset the app currently targets (the cutover pointer in SQLite).

    Falls back to DEFAULT_DATASET when unset. add/cognify/search resolve their
    dataset from here so a rebuild cutover (set via db.set_setting) is picked up
    on the very next call, with no restart.
    """
    return db.get_setting(ACTIVE_DATASET_KEY, DEFAULT_DATASET)


# UI toggle -> Cognee search_type
SEARCH_TYPES = {
    "quick": "CHUNKS",              # fast vector lookup
    "explore": "GRAPH_COMPLETION",  # graph traversal / connections
}


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-Api-Key": API_KEY,
        "X-Tenant-Id": TENANT_ID,
    }


def _auth_headers() -> dict:
    # Auth only — no Content-Type, so httpx can set the multipart boundary itself.
    return {
        "X-Api-Key": API_KEY,
        "X-Tenant-Id": TENANT_ID,
    }


def is_configured() -> bool:
    return bool(BASE_URL and TENANT_ID and API_KEY and "REPLACE" not in API_KEY)


class CogneeError(RuntimeError):
    pass


async def ensure_dataset(dataset: str = DEFAULT_DATASET) -> dict:
    """Create the dataset (or return existing). Idempotent. Returns dataset dict with id."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.post(
            f"{BASE_URL}/api/v1/datasets",
            json={"name": dataset},
            headers=_headers(),
        )
    if r.status_code >= 400:
        raise CogneeError(f"create dataset failed ({r.status_code}): {r.text[:300]}")
    return r.json() if r.content else {}


async def list_datasets() -> list:
    """List all datasets. Returns the parsed JSON (list of dataset dicts)."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(f"{BASE_URL}/api/v1/datasets", headers=_headers())
    if r.status_code >= 400:
        raise CogneeError(f"list datasets failed ({r.status_code}): {r.text[:300]}")
    return r.json() if r.content else []


async def delete_dataset(dataset_id: str) -> dict:
    """DELETE /api/v1/datasets/{dataset_id} — remove the whole dataset.

    Returns {"status": int, "text": str}. Auth headers only; follows 307 redirects.
    """
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.delete(
            f"{BASE_URL}/api/v1/datasets/{dataset_id}",
            headers=_auth_headers(),
        )
    return {"status": r.status_code, "text": r.text[:600]}


async def add(text: str, dataset: str | None = None, node_set: list[str] | None = None) -> dict:
    dataset = dataset or active_dataset()
    # /add is a multipart file-upload endpoint (per the live OpenAPI schema): the note
    # goes in the `data` file field and the dataset in the `datasetName` form field.
    # JSON is rejected. Auth-only headers so httpx sets the multipart boundary itself.
    await ensure_dataset(dataset)
    files = [("data", ("note.txt", text.encode("utf-8"), "text/plain"))]
    data = {"datasetName": dataset}
    if node_set:
        # VERIFIED ENCODING: node_set must be a PLAIN repeated form field (one part per
        # tag), NOT json-encoded. json.dumps(["note_42"]) is stored as the literal string
        # '["note_42"]' (double-encoded). Passing a list value makes httpx emit one
        # `node_set=<tag>` part per element, yielding clean tags. See scripts/cognee_probe.py.
        data["node_set"] = list(node_set)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.post(
            f"{BASE_URL}/api/v1/add",
            files=files,
            data=data,
            headers=_auth_headers(),
        )
    if r.status_code >= 400:
        raise CogneeError(f"add failed ({r.status_code}): {r.text[:300]}")
    return r.json() if r.content else {}


def note_node_set(note_id: int) -> str:
    """The node_set tag that groups one note's chunks. node_set is our ONLY grouping
    mechanism over REST (external_metadata is not supported), so the note id is encoded
    into the tag itself."""
    return f"note_{note_id}"


async def add_note_chunks(note_id: int, text: str, dataset: str | None = None) -> int:
    """Split a note and add each chunk as a separate /add call, ALL grouped under the
    single node_set tag "note_<id>". Does NOT cognify — the caller cognifies ONCE after
    a whole batch (cognify is the expensive step). Returns the number of chunks added.
    """
    dataset = dataset or active_dataset()
    tag = note_node_set(note_id)
    chunks = chunking.split_note(text)
    for chunk in chunks:
        await add(chunk, dataset=dataset, node_set=[tag])
    return len(chunks)


async def delete_note_data(note_id: int, dataset: str | None = None) -> dict:
    """Best-effort removal of a note's data from the graph by its node_set tag.

    CAVEAT: delete-by-node_set over REST is NOT among the primitives verified by
    scripts/cognee_probe.py. The reliable reconciliation is a full rebuild
    (rebuild.py), which reconstructs the graph from SQLite and naturally drops
    deleted notes. This attempts the incremental delete and reports the raw result;
    the sync worker treats a hard 4xx as "nothing to do here, defer to rebuild".
    """
    dataset = dataset or active_dataset()
    payload = {"dataset_name": dataset, "node_set": [note_node_set(note_id)]}
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.post(f"{BASE_URL}/api/v1/delete", json=payload, headers=_headers())
    return {"status": r.status_code, "text": r.text[:300]}


async def cognify(dataset: str | None = None) -> dict:
    dataset = dataset or active_dataset()
    async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
        r = await client.post(
            f"{BASE_URL}/api/v1/cognify",
            json={"datasets": [dataset]},
            headers=_headers(),
        )
    if r.status_code >= 400:
        raise CogneeError(f"cognify failed ({r.status_code}): {r.text[:300]}")
    return r.json() if r.content else {}


async def search(query: str, mode: str = "quick", dataset: str | None = None) -> dict:
    dataset = dataset or active_dataset()
    search_type = SEARCH_TYPES.get(mode, SEARCH_TYPES["quick"])
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        r = await client.post(
            f"{BASE_URL}/api/v1/search",
            json={"query": query, "search_type": search_type, "datasets": [dataset]},
            headers=_headers(),
        )
    if r.status_code >= 400:
        raise CogneeError(f"search failed ({r.status_code}): {r.text[:300]}")
    return r.json() if r.content else {}

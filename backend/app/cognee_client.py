"""Cognee Cloud client — REST wrapper.

Config from env (never hardcoded):
  COGNEE_BASE_URL   https://tenant-xxxx.aws.cognee.ai
  COGNEE_TENANT_ID  tenant UUID
  COGNEE_API_KEY    your key

Cloud endpoints. Auth via X-Api-Key + X-Tenant-Id.
"""
import os
import httpx

BASE_URL = os.environ.get("COGNEE_BASE_URL", "").rstrip("/")
TENANT_ID = os.environ.get("COGNEE_TENANT_ID", "")
API_KEY = os.environ.get("COGNEE_API_KEY", "")
DEFAULT_DATASET = os.environ.get("COGNEE_DATASET", "zettelkeistan")

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


def is_configured() -> bool:
    return bool(BASE_URL and TENANT_ID and API_KEY and "REPLACE" not in API_KEY)


class CogneeError(RuntimeError):
    pass


async def ensure_dataset(dataset: str = DEFAULT_DATASET) -> dict:
    """Create the dataset (or return existing). Idempotent."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.post(
            f"{BASE_URL}/api/v1/datasets",
            json={"name": dataset},
            headers=_headers(),
        )
    if r.status_code >= 400:
        raise CogneeError(f"create dataset failed ({r.status_code}): {r.text[:300]}")
    return r.json() if r.content else {}


async def add(text: str, dataset: str = DEFAULT_DATASET) -> dict:
    await ensure_dataset(dataset)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.post(
            f"{BASE_URL}/api/v1/add",
            json={"data": [text], "datasetName": dataset},
            headers=_headers(),
        )
    if r.status_code >= 400:
        raise CogneeError(f"add failed ({r.status_code}): {r.text[:300]}")
    return r.json() if r.content else {}


async def cognify(dataset: str = DEFAULT_DATASET) -> dict:
    async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
        r = await client.post(
            f"{BASE_URL}/api/v1/cognify",
            json={"datasets": [dataset]},
            headers=_headers(),
        )
    if r.status_code >= 400:
        raise CogneeError(f"cognify failed ({r.status_code}): {r.text[:300]}")
    return r.json() if r.content else {}


async def search(query: str, mode: str = "quick", dataset: str = DEFAULT_DATASET) -> dict:
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

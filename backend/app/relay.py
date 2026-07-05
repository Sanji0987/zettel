"""Thin relay to external services on the shared Docker network (n8n-net).

Two responsibilities, both PURE RELAY — no AI / Ollama / Cognee logic lives here:
  - ollama_tags(): list the models the existing `ollama` container has pulled
    (GET {OLLAMA_BASE_URL}/api/tags). We never invoke a model from FastAPI.
  - chat(): forward a chat turn to the n8n webhook (the "brain"). Until that
    webhook exists, return a MOCK reply so the frontend is usable pre-n8n.

Config from env:
  OLLAMA_BASE_URL    default http://ollama:11434 (container name on n8n-net)
  N8N_CHAT_WEBHOOK   default "" -> mock mode
"""
import os

import httpx

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
N8N_CHAT_WEBHOOK = os.environ.get("N8N_CHAT_WEBHOOK", "").strip()
# The n8n sync worker's webhook — lets a manual "sync now" kick the worker immediately.
# The worker ALSO runs on its own schedule (cron lives in n8n, not FastAPI). Empty = no-op.
N8N_SYNC_WEBHOOK = os.environ.get("N8N_SYNC_WEBHOOK", "").strip()

_TIMEOUT = httpx.Timeout(10.0, connect=3.0)


async def ollama_tags() -> dict:
    """Ping the existing ollama container's /api/tags. List only, never invoke.

    Returns {"reachable": bool, "models": [name, ...]}. Any connection/HTTP error
    is swallowed into reachable=False so a stopped ollama container just disables
    chat in the UI rather than erroring.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return {"reachable": False, "models": []}
    models = [m.get("name") for m in data.get("models", []) if m.get("name")]
    return {"reachable": True, "models": models}


async def chat(message: str, mode: str, history: list[dict]) -> dict:
    """Relay one chat turn to the n8n webhook, or return a mock if unwired.

    mode is the explicit user read/write toggle — passed through untouched, never
    inferred here. Response shape is always {reply, mode, sources, draft}, where
    draft is {title, text} in write mode (an edit-gate preview, not yet saved) or
    None in read mode.
    """
    if not N8N_CHAT_WEBHOOK:
        # TEMPORARY: n8n brain not built yet. Lets the chat UI round-trip before the
        # webhook exists. Remove once N8N_CHAT_WEBHOOK is set to the real workflow.
        return {
            "reply": f"[mock] n8n not wired. echo: {message}",
            "mode": mode,
            "sources": [],
            "draft": None,
        }

    payload = {"message": message, "mode": mode, "history": history}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0), follow_redirects=True) as client:
        resp = await client.post(N8N_CHAT_WEBHOOK, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return {
        "reply": data.get("reply", ""),
        "mode": data.get("mode", mode),
        "sources": data.get("sources", []),
        "draft": data.get("draft"),  # {title, text} in write mode, else None
    }


async def trigger_sync() -> dict:
    """Kick the n8n sync worker immediately (optional — it also runs on a schedule).

    Pure relay: FastAPI's /api/sync/run does the actual work; this just pokes n8n so a
    user doesn't have to wait for the next scheduled tick. No-op when unconfigured.
    """
    if not N8N_SYNC_WEBHOOK:
        return {"triggered": False, "reason": "N8N_SYNC_WEBHOOK unset"}
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.post(N8N_SYNC_WEBHOOK, json={"source": "manual"})
        resp.raise_for_status()
    return {"triggered": True}

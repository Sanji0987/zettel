"""Throwaway isolation probe for Cognee Cloud REST primitives.

NOT wired into the app. Proves, against a THROWAWAY dataset named "test":
  1. Does node_set survive REST /add (form field? query param?).
  2. Does node_set grouping survive cognify + show up in GRAPH_COMPLETION search.
  3. (optional) Can we inspect what landed in the dataset.
Then deletes the "test" dataset.

Run inside the app container so COGNEE_* env is present (the key is never printed):
  docker exec -i zettelkeistan python3 - < scripts/cognee_probe.py

Rules honored: dataset "test" only, ONE cognify, never touch zettelkeistan_v3.
"""
import json
import os
import sys

import httpx

BASE_URL = os.environ.get("COGNEE_BASE_URL", "").rstrip("/")
TENANT_ID = os.environ.get("COGNEE_TENANT_ID", "")
API_KEY = os.environ.get("COGNEE_API_KEY", "")

DATASET = "test"  # throwaway — never zettelkeistan_v3
NODE_SET = ["note_probe_parent"]

CHUNKS = [
    "The probe note discusses topic ALPHA: alpha is the first concept.",
    "The probe note discusses topic BETA: beta is the second concept.",
    "The probe note discusses topic GAMMA: gamma connects alpha and beta.",
]


def hdr_json():
    return {"Content-Type": "application/json", "X-Api-Key": API_KEY, "X-Tenant-Id": TENANT_ID}


def hdr_auth():
    # No Content-Type so httpx sets the multipart boundary itself.
    return {"X-Api-Key": API_KEY, "X-Tenant-Id": TENANT_ID}


def show(label, r):
    body = r.text[:700] if r.text else "<empty>"
    print(f"  [{label}] HTTP {r.status_code}")
    print(f"    resp: {body}")
    return r


def guard():
    missing = [n for n, v in (("BASE_URL", BASE_URL), ("TENANT_ID", TENANT_ID), ("API_KEY", API_KEY)) if not v]
    if missing:
        print(f"ABORT: missing env {missing} — run inside the container.")
        sys.exit(1)
    # Absolute safety: never let this script address the real dataset.
    assert DATASET == "test", "probe must only touch dataset 'test'"


def create_dataset(client):
    print(f"\n== Create dataset '{DATASET}' ==")
    r = client.post(f"{BASE_URL}/api/v1/datasets", json={"name": DATASET}, headers=hdr_json())
    show("datasets", r)
    ds_id = None
    try:
        j = r.json()
        ds_id = j.get("id") if isinstance(j, dict) else None
    except Exception:
        pass
    return ds_id


def add_chunk(client, text, mode):
    """mode='form' -> node_set in multipart form; mode='query' -> node_set as query param.
    node_set is sent JSON-encoded (a list). Returns the response."""
    files = [("data", ("chunk.txt", text.encode("utf-8"), "text/plain"))]
    data = {"datasetName": DATASET}
    url = f"{BASE_URL}/api/v1/add"
    if mode == "form":
        data["node_set"] = json.dumps(NODE_SET)
    elif mode == "query":
        url = f"{url}?node_set={json.dumps(NODE_SET)}"
    return client.post(url, files=files, data=data, headers=hdr_auth())


def probe1(client):
    print("\n== Probe 1: node_set over /add ==")
    # Decide the accepted form using chunk 1.
    print("\n-- chunk1 attempt A: node_set as FORM field --")
    r = show("add form", add_chunk(client, CHUNKS[0], "form"))
    mode = None
    if r.status_code < 400:
        mode = "form"
    else:
        print("\n-- chunk1 attempt B: node_set as QUERY param --")
        r = show("add query", add_chunk(client, CHUNKS[0], "query"))
        if r.status_code < 400:
            mode = "query"
        else:
            print("\n-- chunk1 attempt C: NO node_set (baseline — does /add even work here?) --")
            files = [("data", ("chunk.txt", CHUNKS[0].encode("utf-8"), "text/plain"))]
            show("add plain", client.post(f"{BASE_URL}/api/v1/add", files=files,
                                          data={"datasetName": DATASET}, headers=hdr_auth()))
    if mode is None:
        print("\n>> node_set REJECTED in both form and query form. See responses above.")
        return None
    print(f"\n>> node_set ACCEPTED as: {mode}. Adding chunks 2 & 3 the same way.")
    for i, text in enumerate(CHUNKS[1:], start=2):
        show(f"add chunk{i} ({mode})", add_chunk(client, text, mode))
    return mode


def probe2(client):
    print("\n== Probe 2: cognify (ONE) + connected-search ==")
    r = client.post(f"{BASE_URL}/api/v1/cognify", json={"datasets": [DATASET]}, headers=hdr_json())
    show("cognify", r)
    query = "How do alpha and beta relate in the probe note?"
    print(f"\n-- search GRAPH_COMPLETION: {query!r} --")
    r = client.post(f"{BASE_URL}/api/v1/search",
                    json={"query": query, "search_type": "GRAPH_COMPLETION", "datasets": [DATASET]},
                    headers=hdr_json())
    show("search", r)


def probe3(client, ds_id):
    print("\n== Probe 3 (optional): inspect dataset contents ==")
    tried = []
    candidates = []
    if ds_id:
        candidates += [f"/api/v1/datasets/{ds_id}/data", f"/api/v1/datasets/{ds_id}/graph"]
    for path in candidates:
        try:
            r = client.get(f"{BASE_URL}{path}", headers=hdr_json())
            show(f"GET {path}", r)
            tried.append(path)
        except Exception as e:
            print(f"  GET {path} -> error {e}")
    if not tried:
        print("  (no dataset id / no cheap inspection endpoint tried)")


def cleanup(client, ds_id):
    print("\n== Cleanup: delete dataset 'test' ==")
    if not ds_id:
        # Resolve id by listing, so we still clean up.
        try:
            r = client.get(f"{BASE_URL}/api/v1/datasets", headers=hdr_json())
            for d in (r.json() if r.content else []):
                if isinstance(d, dict) and d.get("name") == DATASET:
                    ds_id = d.get("id")
                    break
        except Exception as e:
            print(f"  could not resolve id: {e}")
    if not ds_id:
        print("  no dataset id resolved — nothing deleted (check the dashboard).")
        return
    r = client.delete(f"{BASE_URL}/api/v1/datasets/{ds_id}", headers=hdr_auth())
    show("delete", r)


def main():
    guard()
    print(f"Cognee probe against BASE_URL host={BASE_URL.split('//')[-1]} dataset={DATASET!r}")
    with httpx.Client(timeout=600, follow_redirects=True) as client:
        ds_id = create_dataset(client)
        mode = probe1(client)
        if mode is not None:
            probe2(client)
            probe3(client, ds_id)
        else:
            print("\nSkipping cognify/search — node_set not accepted; no tokens spent.")
        cleanup(client, ds_id)
    print("\n== DONE ==")


if __name__ == "__main__":
    main()

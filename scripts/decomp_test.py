#!/usr/bin/env python3
"""Throwaway harness: does gemma4:e2b decompose complex questions into a good
sub-question tree? Pure model test via Ollama directly — NO Cognee, NO n8n.

Run it inside a container on n8n-net so it can reach ollama by name, e.g.:
    docker exec -i zettelkeistan python3 - < scripts/decomp_test.py

Notes:
- gemma4:e2b crashes (GGML_SCHED_MAX_SPLIT_INPUTS) at its default 131072 context,
  so num_ctx is pinned to 8192 here.
- think is disabled + <think> blocks stripped defensively (E2B emits them).
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error

BASE = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
MODEL = os.environ.get("DECOMP_MODEL", "gemma4:e2b")

SYSTEM = (
    "Break the user's question into the minimal set of simpler sub-questions needed "
    "to answer it fully. Output ONLY a JSON array of sub-question strings, ordered so "
    "earlier ones can be answered first (leaves before roots). No explanation, no "
    "thinking in the output."
)

QUESTIONS = [
    "Why did the Roman Republic transition into the Roman Empire, and what role did Julius Caesar play versus Augustus?",
    "How does photosynthesis in plants differ from chemosynthesis in deep-sea bacteria, and why does that difference matter for their ecosystems?",
    "What were the main causes of the 2008 financial crisis, and how did they connect the housing market to global banks?",
    "Compare how TCP and UDP handle data reliability, and explain which one video streaming should use and why.",
    "How does natural selection lead to antibiotic resistance in bacteria, and why does overprescribing antibiotics accelerate it?",
    "What is the difference between supervised and unsupervised machine learning, and which would you use to detect fraud in transactions?",
    "Why is the sky blue during the day but red at sunset, and what does that reveal about how light interacts with the atmosphere?",
    "How did the invention of the printing press affect literacy, religion, and political power in Europe?",
    "What are the tradeoffs between nuclear, solar, and wind power for reducing carbon emissions, considering cost, reliability, and land use?",
    "How does the human immune system distinguish self from non-self, and why do autoimmune diseases represent a failure of that process?",
]


def _get(path, timeout=15):
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(path, payload, timeout=240):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def strip_thinking(s):
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S | re.I)
    s = re.sub(r"<\|think\|>.*?<\|/?think\|>", "", s, flags=re.S | re.I)
    s = re.sub(r"<\|?/?think\|?>", "", s, flags=re.I)
    # also strip ```json fences
    s = re.sub(r"```(?:json)?", "", s, flags=re.I)
    return s.strip()


def extract_json_array(s):
    m = re.search(r"\[.*\]", s, flags=re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
        return arr if isinstance(arr, list) else None
    except Exception:
        return None


def decompose(q):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": q},
        ],
        "stream": False,
        "think": False,
        "options": {"num_ctx": 8192, "temperature": 0.2},
    }
    d = _post("/api/chat", payload)
    return d.get("message", {}).get("content", "")


def main():
    try:
        tags = _get("/api/tags")
    except Exception as e:
        print(f"Ollama NOT reachable at {BASE}: {type(e).__name__}: {e}")
        sys.exit(1)
    names = [m.get("name") for m in tags.get("models", [])]
    print(f"Ollama reachable at {BASE}. Models: {names}")
    if MODEL not in names:
        print(f"WARNING: {MODEL} not found in tags — continuing anyway")

    ok_count = 0
    counts = []
    for i, q in enumerate(QUESTIONS, 1):
        print("\n" + "=" * 80)
        print(f"Q{i}: {q}")
        try:
            raw = decompose(q)
        except Exception as e:
            print(f"  REQUEST ERROR: {type(e).__name__}: {e}")
            print("  {parsed_ok: False, count: 0}")
            counts.append(0)
            continue
        cleaned = strip_thinking(raw)
        arr = extract_json_array(cleaned)
        if arr is None:
            print("  PARSE FAILED. Raw (cleaned) output:")
            print("  " + cleaned[:900].replace("\n", "\n  "))
            print("  {parsed_ok: False, count: 0}")
            counts.append(0)
        else:
            for j, sq in enumerate(arr, 1):
                print(f"    {j}. {sq}")
            ok_count += 1
            counts.append(len(arr))
            print(f"  {{parsed_ok: True, count: {len(arr)}}}")

    print("\n" + "=" * 80)
    print(f"SUMMARY: parsed_ok {ok_count}/{len(QUESTIONS)}; sub-question counts = {counts}")


if __name__ == "__main__":
    main()

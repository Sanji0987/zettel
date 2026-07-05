# Zettelkeistan n8n workflow backups

Byte-accurate exports (via `n8n export:workflow --pretty`) of the workflows: the three
that make up the **chat brain**, plus the **sync worker** (cron ingest). Each file is an
n8n export array (`[ { ...workflow... } ]`), directly importable.

## The workflows (import all; order doesn't matter — refs are by ID)

| File | Name | ID | Role |
|------|------|----|------|
| `cognee-retrieve-one.json` | Cognee Retrieve One | `5uv43KZGPL1Icge9` | Leaf retrieval unit: `{query}` → one Cognee `GRAPH_COMPLETION` search on `zettelkeistan_v3` → `{answer, sources}`. The **only** Cognee caller. |
| `answer-with-optional-split.json` | Answer With Optional Split | `EcK7bqSZd2CiEBvH` | Depth-2 helper: length-gates one L1 sub-question; under → retrieve directly, over → decompose once (L2, cap 5) → retrieve each → combine. Calls only Cognee Retrieve One (no recursion). |
| `zettelkeistan-chat-brain.json` | Zettelkeistan Chat Brain | `esKtUJ1YYfLlyOAT` | Main. Webhook `POST /webhook/zettel-chat` → mode switch → write (draft) / read (length gate → simple or L1-decompose → synthesize). |
| `zettelkeistan-sync-worker.json` | Zettelkeistan Sync Worker | `C68CgGpRqaGBZaGi` | Cron ingest. Schedule (20 min) → `GET http://zettelkeistan:8000/api/sync/status` → IF `pending>0 OR pending_removals>0` → `POST /api/sync/run`. No credentials. Never runs the sweep on an empty queue. |

## Cross-references (IMPORTANT)
The main workflow's `Execute B` node references `EcK7bqSZd2CiEBvH`; both the main
`Execute Retrieval` and the splitter's `Retrieve Direct`/`Retrieve L2` reference
`5uv43KZGPL1Icge9`. These are stored by workflow **ID**.
- **Restoring to the SAME n8n instance** (import keeps IDs) → references stay valid.
- **Restoring to a DIFFERENT instance** → if IDs change, re-point each `Execute Workflow`
  node's target (Cognee Retrieve One / Answer With Optional Split).

## Credentials (NOT in the export — secrets are never exported)
Nodes reference credentials by ID/name; recreate these on a fresh instance:
- **Groq account** (`groqApi`, id `woCh9CG7qUGXmSCX`) — the chat brain's model nodes. Holds
  the Groq API key.
- **Cognee account** (`cogneeApi`, id `rvhw3tMxtNWBoYSU`) — Base URL
  `https://tenant-b24860eb-9dce-41a1-9f11-13d7a8f9cdd7.aws.cognee.ai`, plus the API key.
- **Ollama account** (`ollamaApi`, id `Zl9lspYFi45GrFwM`) — Base URL `http://ollama:11434`.
  No longer used by the chat brain (kept for the `ollama-version` branch / app status ping).

## Restore
Inside the n8n container:
```
docker cp cognee-retrieve-one.json n8n:/tmp/ && docker exec n8n n8n import:workflow --input=/tmp/cognee-retrieve-one.json
docker cp answer-with-optional-split.json n8n:/tmp/ && docker exec n8n n8n import:workflow --input=/tmp/answer-with-optional-split.json
docker cp zettelkeistan-chat-brain.json n8n:/tmp/ && docker exec n8n n8n import:workflow --input=/tmp/zettelkeistan-chat-brain.json
docker cp zettelkeistan-sync-worker.json n8n:/tmp/ && docker exec n8n n8n import:workflow --input=/tmp/zettelkeistan-sync-worker.json
```
Then re-attach credentials (if new instance), and activate all (UI toggle or
`n8n update:workflow --id=<id> --active=true`). The sync worker needs no credentials —
it only calls the app's own REST API over `n8n-net`.

## Config notes
- Length threshold: env `DECOMP_WORD_THRESHOLD` (default **40**), read in the main
  `Length Gate` node; the splitter reuses the passed-through threshold. High by design.
- Sub-question caps: 5 per level, in `Parse L1` / `Parse L2` code nodes.
- Model: **Groq `openai/gpt-oss-120b`** (cloud) on every model node — `lmChatGroq`,
  temperature 0.2, credential **Groq account** (`groqApi`, id `woCh9CG7qUGXmSCX`). The
  earlier local-Ollama version (`gemma4:e2b`, `num_ctx 8192`) is frozen on the
  `ollama-version` git branch.
- App wiring: FastAPI relay posts to `N8N_CHAT_WEBHOOK=http://n8n:5678/webhook/zettel-chat`.

_A copy of these files also lives in the app repo at `zettelkeistan/n8n/`._

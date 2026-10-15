# 知识引擎（M3 本地库问答）

Local desktop Q&A agent. M0 is the Electron + React shell, the FastAPI sidecar, and the Alembic migration. M1 adds grill-me clarification. M2 records Jev-style decisions in shadow mode. M3 imports local files, indexes them, and answers from those chunks.

Import md, txt, pdf, or docx. Each file is chunked and written to SQLite FTS5 plus a vector index (sqlite-vec when the extension loads, otherwise a blob of the same vectors). `POST /chat` streams `citation`, `delta`, and `done` over SSE. Citations name the source file and chunk. If the library has documents but nothing matches, the answer is `当前知识库中没有找到相关内容。` and no citation is emitted. An empty library still returns the M0 stub, so older chat tests stay valid.

Shadow decisions are unchanged: every `/chat` still logs intent and channel probes, `applied` stays false while real routing is off, and the decider does not switch retrieval channels. A confirmed brief's `scope` filters hits and is repeated in the answer. `personal` mode does not return `web` or `connector` rows.

Chat and embeddings are OpenAI-compatible. Set `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` (or `EMBED_BASE_URL`, `EMBED_API_KEY`, `EMBED_MODEL`). With no key, indexing uses a deterministic hashing embedder and answers are extractive. That offline path is what the tests and the desktop demo use.

The Jev client is still the rule backend. `register_decision_backend` is the seam for a later model endpoint; `build_decision_client("remote")` raises until one is registered. Web search is a chain of Google then `WEB_SEARCH_FALLBACK` (default `fallback`). Neither provider calls the network. Freshness checks and connectors are still 501 routes.

Offline retrieval baseline, recorded here rather than treated as a production score: on the five-question fixture in `services/sidecar/tests/test_m3_library.py`, top-1 citation accuracy is 5/5 (1.0) with the hashing embedder.

## Layout

```
apps/desktop/          Electron + React + TypeScript (electron-vite)
services/sidecar/      Python FastAPI sidecar
vendor/MediaCrawler/   placeholder only — not cloned
```

## Sidecar health check

Python 3.11 or 3.12. From a clean checkout:

```bash
cd services/sidecar
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -r requirements-core.txt
```

`requirements-core.txt` is enough to boot the sidecar, including `pypdf` and `sqlite-vec`. `requirements.txt` also lists torch, FlagEmbedding, and transformers. Those libraries are not imported when the process starts. Set `EMBED_PROVIDER=bge` only when you intend to load them.

```bash
export SIDECAR_TOKEN=devtoken
export DATA_DIR="$PWD/../../.data"   # any writable directory
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

In another shell:

```bash
curl -sS -H "x-sidecar-token: $SIDECAR_TOKEN" http://127.0.0.1:8765/health
```

Expected:

```json
{"ok":true,"version":"0.1.0","data_dir":"...","db":"ready","models":{"embedder":"not_loaded","reranker":"not_loaded"}}
```

When `SIDECAR_TOKEN` is set, every route — including `/health` — requires the header `x-sidecar-token`. A missing or wrong token returns `401`.

Import a file and ask about it (offline extractive answer, no API key):

```bash
curl -sS -H "x-sidecar-token: $SIDECAR_TOKEN" -H "content-type: application/json" \
  -d '{"filename":"qingjiao-notes.md","text":"每斤青椒放盐三克。腌制时间是两小时。"}' \
  http://127.0.0.1:8765/library/import

curl -sS -H "x-sidecar-token: $SIDECAR_TOKEN" http://127.0.0.1:8765/library/items

curl -N -H "x-sidecar-token: $SIDECAR_TOKEN" -H "content-type: application/json" \
  -d '{"mode":"personal","message":"青椒腌制要多久","sources":["local"]}' \
  http://127.0.0.1:8765/chat
```

You should see `event: citation` (filename and chunk), then `event: delta`, then `event: done` with `"provider":"offline"`. A question the file does not cover returns the not-found sentence and no citation. `POST /search` returns the same hits without generating. `POST /index/rebuild` with `{"scope":"pending"}` or `{"scope":"all"}` reindexes. `DELETE /library/items/{id}` removes the row, chunks, FTS rows, and vectors.

One-shot sample import without curl, from the repo root:

```bash
cd services/sidecar
export DATA_DIR="$PWD/../../.data"
.venv/bin/python scripts/import_sample.py
```

The sample is `samples/m3/qingjiao-notes.md`.

Chat with no indexed chunks is still the M0 stub (decision events, one delta, one done). Trivial empty chat:

```bash
curl -N -H "x-sidecar-token: $SIDECAR_TOKEN" -H "content-type: application/json" \
  -d '{"mode":"personal","messages":[{"role":"user","content":"hi"}]}' \
  http://127.0.0.1:8765/chat
```

You should see one or more `event: decision` lines, then `event: delta`, then `event: done`. An empty body still returns the same stub delta and done payload as M1 when the library has no chunks. Pass `brief_id` after confirming a clarification, or `"clarify": "skip"`, and the `done` event includes the brief. Real routing is off by default, so a decision that would have picked `web` does not change `retrieval.sources`.

Shadow logs (developer). Each `/chat` writes `decision_log` rows for `intent`, `need_clarify`, `route_channels`, `needs_verify`, and `relevance`. Nothing in those rows should contain an API key or the sidecar token.

```bash
curl -sS -H "x-sidecar-token: $SIDECAR_TOKEN" \
  "http://127.0.0.1:8765/decision/logs?limit=20&offset=0"
```

The response is `{ "items", "limit", "offset", "total" }`. `items` are newest first. Toggle shadow mode (default on) or turn real routing on (default off):

```bash
curl -sS -H "x-sidecar-token: $SIDECAR_TOKEN" -H "content-type: application/json" \
  -d '{"enabled":true}' \
  http://127.0.0.1:8765/decision/shadow

curl -sS -H "x-sidecar-token: $SIDECAR_TOKEN" http://127.0.0.1:8765/settings/decision

curl -sS -H "x-sidecar-token: $SIDECAR_TOKEN" -H "content-type: application/json" \
  -d '{"shadow":true,"real_routing":false}' \
  http://127.0.0.1:8765/settings/decision
```

Settings live in `$DATA_DIR/config/decision.json`. With shadow on, or with real routing off, `applied` stays false and the chat text does not change. If the placeholder raises, the row is stored with `fallback` `llm` or `default` and `/chat` still returns delta and done. There is no built-in production confidence cutoff; set `confidence_floor` only when you want one. Marking a row `applied` does not switch retrieval, clarification, or verification in this milestone.

Clarify one question at a time:

```bash
curl -sS -H "x-sidecar-token: $SIDECAR_TOKEN" -H "content-type: application/json" \
  -d '{"keyword":"考研数学复盘怎么做","domain_hint":"learning"}' \
  http://127.0.0.1:8765/clarify/start
```

Answer with `action` `accept`, `edit` (`value` required), or `park`. When the response `state` is `ready_to_confirm`, confirm it:

```bash
curl -sS -H "x-sidecar-token: $SIDECAR_TOKEN" -H "content-type: application/json" \
  -d '{}' \
  http://127.0.0.1:8765/clarify/12/confirm
```

`POST /clarify/start` with `"skip": true`, or `POST /chat` with `"clarify": "skip"`, writes `brief` = `skipped` and hands that brief to retrieval. `GET /clarify/active` restores an unfinished session after a restart. `CLARIFY_MAX_QUESTIONS` defaults to 7 (1–7). Question text can be polished when `CLARIFY_LLM_API_KEY` or `LLM_API_KEY` is set; otherwise the template is used.

These routes still return **501**:

| Method | Path |
|---|---|
| POST | `/verify` |
| GET | `/verify/{job_id}` |
| GET, POST | `/connectors` |
| POST | `/connectors/{id}/sync` |
| GET | `/connectors/{id}/status` |
| DELETE | `/connectors/{id}` |

Apply migrations without starting the server. `DATA_DIR` is used when you leave the default URL in `alembic.ini` alone. `init_db(path)` always targets `<path>/data/app.db`.

```bash
cd services/sidecar
export DATA_DIR="$PWD/../../.data"
alembic upgrade head
python -c "from app.db.session import init_db; print(init_db())"
```

On an empty file the migration creates the v1.0 tables plus v1.1 columns, then M3 adds `fts_chunks` (FTS5 trigram) and `chunk_vector`. On a database that already has v1.0 tables (`content_item`, `conversation`, `message`) it adds the new nullable columns and creates `clarify_session`, `connector_account`, `decision_log`, and `verify_job`, then the M3 index tables. Existing rows get `fetched_at = collected_at` and `freshness_state = 'unknown'`. Head revision is `0002_m3`.

Tests:

```bash
cd services/sidecar
python -m pytest -q
```

## Desktop shell

Node.js 22.

```bash
npm install
npm run dev
```

The main process picks a free localhost port, starts `uvicorn app.main:app`, and stops that child on quit. Python resolution order:

1. `SIDECAR_PYTHON`
2. `<repo>/.venv/bin/python` (Windows: `.venv\Scripts\python.exe`)
3. `<repo>/services/sidecar/.venv/bin/python`
4. `python3` on `PATH`

The window is the library (import, list, delete, index status) and a chat pane. Citations are buttons; choosing one shows the chunk text and can open the stored file. Feature flags live in `apps/desktop/src/shared/features.ts`. `features.clarify`, `features.jev`, and `features.verify` default to `false`. Set `VITE_FEATURE_CLARIFY=true` (or `1`) before `npm run dev` to mount the clarifier. Set `VITE_FEATURE_JEV=true` to mount the decision panel. `features.verify` still leaves its panel unmounted.

## Not in this revision

M4–M6 are not implemented: no freshness jobs, no connector sync, and no live web search. MediaCrawler is not downloaded. BGE-M3 and the reranker are not loaded unless `EMBED_PROVIDER=bge`. The Jev rule client is not a model endpoint. Google search and the fallback provider raise `SearchUnavailable` instead of calling out. `/verify` and `/connectors` still return 501.

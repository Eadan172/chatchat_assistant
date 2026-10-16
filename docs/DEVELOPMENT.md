# Developer notes

Everyday setup and how to ask questions are in the root [README.md](../README.md). This file is the layout, HTTP routes, and feature flags.

# 知识引擎（M5 ima 连接器，M6 提问）

Local desktop Q&A agent. M0 is the Electron + React shell, the FastAPI sidecar, and the Alembic migration. M1 adds grill-me clarification. M2 records Jev-style decisions in shadow mode. M3 imports local files, indexes them, and answers from those chunks. M4 checks those files for freshness with a content hash and source mtime. M5 adds a Tencent ima connector (official OpenAPI only) that can list knowledge bases, search, and import selected notes or files into that same library. M6 adds `POST /ask`: clarify, search local + ima + the live web, and stream a cited answer. It stays off until `FEATURE_ASK` is set.

Import md, txt, pdf, or docx. Each file is chunked and written to SQLite FTS5 plus a vector index (sqlite-vec when the extension loads, otherwise a blob of the same vectors). `POST /chat` streams `citation`, `delta`, and `done` over SSE. Citations name the source file and chunk. If the library has documents but nothing matches, the answer is `当前知识库中没有找到相关内容。` and no citation is emitted. An empty library still returns the M0 stub, so older chat tests stay valid.

Shadow decisions are unchanged: every `/chat` still logs intent and channel probes, `applied` stays false while real routing is off, and the decider does not switch retrieval channels. A confirmed brief's `scope` filters hits and is repeated in the answer. `personal` mode does not return `web` or `connector` rows.

Chat and embeddings are OpenAI-compatible. Set `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` (or `EMBED_BASE_URL`, `EMBED_API_KEY`, `EMBED_MODEL`). With no key, indexing uses a deterministic hashing embedder and answers are extractive. That offline path is what the tests and the desktop demo use.

The Jev client is still the rule backend. `register_decision_backend` is the seam for a later model endpoint; `build_decision_client("remote")` raises until one is registered. If that hook is filled in, point it at DeepSeek (`https://api.deepseek.com`, `deepseek-v4-flash`). Gemini and `api.openai.com` time out from mainland China, so they are not the example default. Web search for `/chat` is a chain of Google then `WEB_SEARCH_FALLBACK` (default `fallback`). Neither provider calls the network. The ask pipeline is separate: with `SERPER_API_KEY`, `SERPAPI_API_KEY`, or `TAVILY_API_KEY` it calls that provider. M4 does not re-fetch the web; a `web` or `connector` row is marked `unknown`.

Local verify is off unless `FEATURE_VERIFY=1` (the desktop shell forwards `VITE_FEATURE_VERIFY`). While it is off, `POST /verify` and `GET /verify/{job_id}` stay 501 and chat does not re-read files. `needs_verify` is still only a shadow log. While it is on, `POST /verify` with `{"item_ids":[1],"mode":"hash_mtime"}` re-reads `source_path` when that file exists. A changed hash or a newer mtime sets `freshness_state` to `stale` and `prompt_update` to true. The body is left alone until `mode` is `refetch`, which re-ingests and reindexes. A missing or unreadable path sets `unknown` and is never left as `fresh`. The same check runs inside `/chat` when the question hits a freshness cue (`最新`, `现在`, `当前`, and the words already used by the rule client) or when `"verify":"force"`. `"verify":"skip"` still answers and adds a short 未查证 note. Repeated checks inside `VERIFY_DEBOUNCE_S` (default 30) skip the full re-read when the mtime has not moved. Embedding downloads, if you set `EMBED_PROVIDER=bge`, use `HF_ENDPOINT=https://hf-mirror.com` unless you already set one. `huggingface.co` is not the primary.

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

Import a file and ask about it (offline extractive answer, no API key). The bundled sample is `samples/m3/qingjiao-notes.md`.

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
  -d '{"keyword":"示例主题怎么入门","domain_hint":"learning"}' \
  http://127.0.0.1:8765/clarify/start
```

Answer with `action` `accept`, `edit` (`value` required), or `park`. When the response `state` is `ready_to_confirm`, confirm it:

```bash
curl -sS -H "x-sidecar-token: $SIDECAR_TOKEN" -H "content-type: application/json" \
  -d '{}' \
  http://127.0.0.1:8765/clarify/12/confirm
```

`POST /clarify/start` with `"skip": true`, or `POST /chat` with `"clarify": "skip"`, writes `brief` = `skipped` and hands that brief to retrieval. `GET /clarify/active` restores an unfinished session after a restart. `CLARIFY_MAX_QUESTIONS` defaults to 7 (1–7). Question text can be polished when `CLARIFY_LLM_API_KEY` or `LLM_API_KEY` is set; otherwise the template is used.

With verify enabled:

```bash
export FEATURE_VERIFY=1
curl -sS -H "x-sidecar-token: $SIDECAR_TOKEN" -H "content-type: application/json" \
  -d '{"item_ids":[1],"mode":"hash_mtime"}' \
  http://127.0.0.1:8765/verify
curl -sS -H "x-sidecar-token: $SIDECAR_TOKEN" http://127.0.0.1:8765/verify/1
```

`mode` `refetch` writes the file back into the library. Without `FEATURE_VERIFY`, both verify routes still return **501**.

With `FEATURE_IMA` unset, these routes still return **501**:

| Method | Path |
|---|---|
| GET, POST | `/connectors` |
| POST | `/connectors/{id}/sync` |
| GET | `/connectors/{id}/status` |
| DELETE | `/connectors/{id}` |
| GET, POST, DELETE | `/settings/ima` |
| GET | `/connectors/ima/bases`, `/connectors/ima/folders` |
| POST | `/connectors/ima/search`, `/connectors/ima/import` |

With `FEATURE_IMA=1` those routes are the ima connector. `POST /settings/key` is still the M0 stub and does not store provider keys. Ask keys go through `POST /settings/ask` into `$DATA_DIR/secrets/ask.json`.

## Ask

`POST /ask` stays **501** until `FEATURE_ASK=1`. The desktop shell forwards `VITE_FEATURE_ASK` or `FEATURE_ASK`. The route streams SSE: `clarify_question`, `searching`, `notice`, `citation`, `delta`, `done`, `error`. `/chat` is not modified.

DeepSeek plans the search and writes the answer. The key is `DEEPSEEK_API_KEY`, or `deepseek_api_key` in `$DATA_DIR/secrets/ask.json` (mode `0600`). Live web search uses `SERPER_API_KEY`, then `SERPAPI_API_KEY`, then `TAVILY_API_KEY`, with the file fields `serper_api_key`, `serpapi_api_key`, and `tavily_api_key`. Handlers never return those values.

A nearby-food question that still leaves place, cuisine, basis, price, or metro open asks those one at a time. A place question has an empty recommendation, so accepting it with no value is an error. Food, place, and travel questions search the web unless the user names notes, a local file, or a knowledge base.

Apply migrations without starting the server. `DATA_DIR` is used when you leave the default URL in `alembic.ini` alone. `init_db(path)` always targets `<path>/data/app.db`.

```bash
cd services/sidecar
export DATA_DIR="$PWD/../../.data"
alembic upgrade head
python -c "from app.db.session import init_db; print(init_db())"
```

On an empty file the migration creates the v1.0 tables plus v1.1 columns, then M3 adds `fts_chunks` (FTS5 trigram) and `chunk_vector`, then M4 adds nullable `source_path` and `freshness_reason`. On a database that already has v1.0 tables (`content_item`, `conversation`, `message`) it adds the new nullable columns and creates `clarify_session`, `connector_account`, `decision_log`, and `verify_job`, then the M3 index tables, then the M4 columns. Existing rows get `fetched_at = collected_at` and `freshness_state = 'unknown'`. Head revision is `0003_m4`.

Tests:

```bash
cd services/sidecar
python -m pytest -q
```

From the repo root, desktop tests are `npm test --workspace desktop`.

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

The window is the library (import, list, delete, index status) and a chat pane. Citations are buttons; choosing one shows the chunk text and can open the stored file. Feature flags live in `apps/desktop/src/shared/features.ts`. `features.clarify`, `features.jev`, `features.verify`, `features.ima`, and `features.ask` default to `false`. Set `VITE_FEATURE_CLARIFY=true` (or `1`) before `npm run dev` to mount the separate clarifier panel. Set `VITE_FEATURE_JEV=true` to mount the decision panel. Set `VITE_FEATURE_VERIFY=true` to show 查证 on library rows and citations, the stale update prompt, and automatic checks when a question contains a freshness cue. The shell copies that flag to the sidecar as `FEATURE_VERIFY`. `features.ima` also defaults to `false`. Set `VITE_FEATURE_IMA=true` to show the ima panel; the shell copies it as `FEATURE_IMA`. While that flag is off, `/connectors` and `/settings/ima` stay **501**. Set `VITE_FEATURE_ASK=true` to mount the ask chat path and the key form. The shell copies `VITE_FEATURE_ASK` or `FEATURE_ASK` to the sidecar as `FEATURE_ASK`. While that flag is off, `POST /ask` stays **501**.

ima credentials are `IMA_CLIENT_ID` and `IMA_API_KEY` (the official client also accepts `IMA_OPENAPI_CLIENTID` and `IMA_OPENAPI_APIKEY`). If those are unset, the settings panel stores them in `$DATA_DIR/secrets/ima.json` (mode `0600`), not in SQLite. Requests go to `https://ima.qq.com` unless `IMA_BASE_URL` is set. Endpoint paths and the `ima-openapi-clientid` / `ima-openapi-apikey` headers live in `services/sidecar/app/connectors/ima_client.py`. A live check (status and field names only) showed `search_knowledge_base` items use `kb_id` / `kb_name` rather than `id` / `name`, `search_knowledge` returns `data.info_list`, `search_note` returns `search_note_infos`, and a bad API key is HTTP 401 (`凭据无效`). A real-key check that does not print content:

```bash
cd services/sidecar
export IMA_CLIENT_ID=在这里粘贴你的ClientID IMA_API_KEY=在这里粘贴你的密钥
python scripts/ima_smoke.py
```

Imported rows use `platform=ima`, `source_kind=connector`, and the ima id in `external_id`. With the ima account enabled they are retrieved in personal mode because they already sit in the local library; the citation badge says 应用知识库. Disconnecting the account leaves the rows on disk and drops them from new answers. With `FEATURE_VERIFY=1`, `POST /verify` re-fetches those rows through the API and compares `content_hash` (`fresh` / `stale`). If ima cannot be reached the state is `unknown`. List and search calls are cached (`IMA_CACHE_TTL_S`, default 60) and spaced (`IMA_MIN_INTERVAL_S`, default 0.2, `IMA_MAX_PER_MINUTE`, default 30). Those numbers are local guards — ima's quota is not published. LLM calls stay on the configured OpenAI-compatible endpoint (DeepSeek from mainland China). Embedding downloads still use `HF_ENDPOINT=https://hf-mirror.com` when `EMBED_PROVIDER=bge`.

```bash
VITE_FEATURE_VERIFY=true VITE_FEATURE_JEV=true VITE_FEATURE_ASK=true npm run dev
```

Import with “按路径导入” when you want 查证 to re-read that same file. A picked file is copied into the library directory and that copy is the source.

When the desktop starts the sidecar, `DATA_DIR` is Electron `app.getPath('userData')`, so secrets saved from the window are not the repo `.data` directory. Environment variables still win.

## Not in this revision

MediaCrawler is not downloaded. BGE-M3 and the reranker are not loaded unless `EMBED_PROVIDER=bge`. The Jev rule client is not a model endpoint. The `/chat` search chain still raises `SearchUnavailable` instead of calling out. Live web search runs only on `POST /ask` when a Serper, SerpApi, or Tavily key is set and `FEATURE_ASK` is on. `/connectors` returns 501 until `FEATURE_IMA` is on. `/verify` returns 501 until `FEATURE_VERIFY` is on. `/ask` returns 501 until `FEATURE_ASK` is on. Notion and local-folder connectors are still placeholders.

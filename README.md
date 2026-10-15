# 知识引擎（M2 Jev shadow）

Local desktop Q&A agent. M0 is the Electron + React shell, the FastAPI sidecar, and the Alembic migration. M1 adds grill-me clarification. M2 records Jev-style decisions in shadow mode: every `/chat` writes intent and channel logs with `applied=false` while real routing stays off, so the answer path matches M1.

The real Jev endpoint is not selected. `DecisionClient.choice` / `score` / `noul` are the swap point, and the process uses a local rule-based placeholder. Freshness checks, connectors, and web search are still packages and 501 routes.

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

`requirements-core.txt` is enough to boot M0. `requirements.txt` also lists torch, FlagEmbedding, and transformers for later milestones. Those libraries are not imported when the process starts.

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

Trivial chat stream (no retrieval, no model call):

```bash
curl -N -H "x-sidecar-token: $SIDECAR_TOKEN" -H "content-type: application/json" \
  -d '{"mode":"personal","messages":[{"role":"user","content":"hi"}]}' \
  http://127.0.0.1:8765/chat
```

You should see one or more `event: decision` lines, then `event: delta`, then `event: done`. An empty body still returns the same stub delta and done payload as M1. Pass `brief_id` after confirming a clarification, or `"clarify": "skip"`, and the `done` event includes the brief. Generation stays a stub. Real routing is off by default, so a decision that would have picked `web` does not change `retrieval.sources`.

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

On an empty file the migration creates the v1.0 tables plus v1.1 columns. On a database that already has v1.0 tables (`content_item`, `conversation`, `message`) it adds the new nullable columns and creates `clarify_session`, `connector_account`, `decision_log`, and `verify_job`. Existing rows get `fetched_at = collected_at` and `freshness_state = 'unknown'`.

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

Feature flags live in `apps/desktop/src/shared/features.ts`. `features.clarify`, `features.jev`, and `features.verify` default to `false`, so the window matches the M0 shell. Set `VITE_FEATURE_CLARIFY=true` (or `1`) before `npm run dev` to mount the clarifier: one question, a recommendation chip, progress, and a recap card. Set `VITE_FEATURE_JEV=true` to mount the decision-layer settings (shadow on, real routing off). `features.verify` still leaves its panel unmounted. The `features.jev` value in source stays `false`.

## Not in this revision

M3–M6 are not implemented: no RAG, no crawlers, no verification jobs, no connector sync, and no web search. MediaCrawler is not downloaded. `/chat` accepts and stores a brief, records shadow decisions, then still returns the generation stub. A real Jev SDK is not called.

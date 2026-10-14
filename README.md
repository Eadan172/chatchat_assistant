# 知识引擎（M0 scaffold）

Local desktop Q&A agent. This revision is the **M0 skeleton** only: an Electron + React shell, a FastAPI sidecar with stub routes, and an Alembic migration that can build an empty database or upgrade a v1.0-shaped one.

Clarifier, Jev routing, freshness checks, connectors, and web search are packages and routes only. They do not run.

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

You should see `event: delta` then `event: done`.

v1.1 routes are registered and return **501**:

| Method | Path |
|---|---|
| POST | `/clarify/start` |
| POST | `/clarify/{session_id}/answer` |
| POST | `/clarify/{session_id}/confirm` |
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

Feature flags live in `apps/desktop/src/shared/features.ts`. `features.clarify`, `features.jev`, and `features.verify` default to `false`, and the placeholder panels under `src/renderer/src/components/{clarify,sources,verify}` stay unmounted.

## Not in this revision

M1–M6 behavior is not implemented: no design-tree questions, no Jev decisions, no RAG, no crawlers, no verification jobs, no connector sync, and no web search. MediaCrawler is not downloaded.

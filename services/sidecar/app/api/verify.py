"""POST /verify and GET /verify/{job_id}.

Local files are checked when FEATURE_VERIFY is on. The route stays 501 while
the flag is off so older clients keep the previous response. Web re-fetch is
not implemented; unreachable remote sources are marked unknown.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from app.freshness.policies import verify_enabled
from app.freshness.verifier import VerifyRequestError, job_snapshot, run_verify

router = APIRouter()


@router.post("")
async def start_verify(request: Request) -> dict:
    if not verify_enabled():
        raise HTTPException(status_code=501, detail="not implemented")
    payload = await _body(request)
    item_ids = payload.get("item_ids")
    mode = payload.get("mode") or "hash_mtime"
    if not isinstance(item_ids, list) or not item_ids or not all(isinstance(item, int) and not isinstance(item, bool) for item in item_ids):
        raise HTTPException(status_code=422, detail="item_ids required")
    if mode not in {"hash_mtime", "refetch"}:
        raise HTTPException(status_code=422, detail="mode must be hash_mtime or refetch")
    registry = getattr(request.app.state, "registry", None)
    try:
        job_id = run_verify(item_ids, mode=mode, registry=registry)
    except VerifyRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    snapshot = job_snapshot(job_id) or {"job_id": job_id}
    return {"job_id": job_id, "state": snapshot.get("state"), "stats": snapshot.get("stats") or {}}


@router.get("/{job_id}")
def get_verify(job_id: int) -> dict:
    if not verify_enabled():
        raise HTTPException(status_code=501, detail="not implemented")
    snapshot = job_snapshot(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="未找到查证任务")
    return snapshot


async def _body(request: Request) -> dict:
    raw = await request.body()
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="invalid json") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="invalid json")
    return data

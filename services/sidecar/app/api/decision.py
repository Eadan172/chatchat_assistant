"""Developer routes for the Jev shadow log."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.decision.shadow import list_logs, update_settings

router = APIRouter()


class ShadowIn(BaseModel):
    enabled: bool


def _save(action) -> dict:
    try:
        return action()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/logs")
def decision_logs(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return list_logs(limit=limit, offset=offset)


@router.post("/shadow")
def set_shadow(body: ShadowIn) -> dict:
    saved = _save(lambda: update_settings(shadow=body.enabled))
    return {"enabled": saved["shadow"], **saved}

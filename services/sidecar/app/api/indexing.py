"""POST /index/rebuild — re-chunk pending rows, or every row when scope is all."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

from app.index.pipeline import rebuild_index

router = APIRouter()


class RebuildBody(BaseModel):
    scope: str = "pending"


@router.post("/index/rebuild")
async def rebuild(request: Request) -> dict:
    raw = await request.body()
    scope = "pending"
    if raw and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="invalid json") from exc
        try:
            scope = RebuildBody.model_validate(data).scope
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid rebuild body") from exc
    return rebuild_index(scope=scope)

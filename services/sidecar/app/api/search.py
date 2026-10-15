"""POST /search — local hybrid retrieval without generation."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

from app.retrieval.multi_source import search_local_library

router = APIRouter()


class SearchBody(BaseModel):
    query: str = ""
    mode: str = "personal"
    top_k: int = 8
    sources: list[str] | None = None


@router.post("")
async def search(request: Request) -> dict:
    raw = await request.body()
    if not raw or not raw.strip():
        payload = SearchBody()
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="invalid json") from exc
        try:
            payload = SearchBody.model_validate(data)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid search body") from exc
    hits = search_local_library(
        payload.query,
        mode=payload.mode,
        sources=payload.sources,
        brief=None,
        top_k=payload.top_k,
    )
    return {"hits": hits, "stub": False}

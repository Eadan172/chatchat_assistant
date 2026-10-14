"""POST /index/rebuild — accepted and ignored. Indexing is not implemented in M0."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.post("/index/rebuild")
def rebuild_index() -> dict:
    return {"accepted": False, "stub": True}

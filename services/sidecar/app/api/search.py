"""POST /search — empty hit list. Hybrid retrieval is not implemented in M0."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.post("")
def search() -> dict:
    return {"hits": [], "stub": True}

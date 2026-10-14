"""Library routes. M0 does not read or delete content rows."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/items")
def list_items() -> dict:
    return {"items": [], "total": 0, "stub": True}


@router.delete("/items/{item_id}")
def delete_item(item_id: int) -> dict:
    return {"id": item_id, "deleted": False, "stub": True}

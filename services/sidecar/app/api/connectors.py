"""Connector routes. Registered in M0 and intentionally unimplemented."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _not_ready() -> None:
    raise HTTPException(status_code=501, detail="not implemented")


@router.get("")
def list_connectors() -> None:
    _not_ready()


@router.post("")
def create_connector() -> None:
    _not_ready()


@router.post("/{connector_id}/sync")
def sync_connector(connector_id: int) -> None:
    _not_ready()


@router.get("/{connector_id}/status")
def connector_status(connector_id: int) -> None:
    _not_ready()


@router.delete("/{connector_id}")
def delete_connector(connector_id: int) -> None:
    _not_ready()

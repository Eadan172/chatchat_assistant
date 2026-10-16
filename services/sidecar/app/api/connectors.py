"""ima connector routes.

The feature flag defaults off. While ``FEATURE_IMA`` is unset these routes
stay 501, matching the M0 stubs. When it is on, the routes talk to the
official ima OpenAPI. Secrets are never written to SQLite or the response.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select

from app.connectors.ima_client import ImaError
from app.connectors.ima_secrets import configured, credential_source
from app.connectors.ima_service import (
    ensure_account,
    import_items,
    list_children,
    list_overview,
    open_ima_client,
    public_account,
    search,
)
from app.db.models import ConnectorAccount
from app.db.session import open_session

router = APIRouter()

_SECRET_KEYS = {"api_key", "apikey", "client_id", "clientid", "secret", "token", "password", "ima_api_key"}


def ima_enabled() -> bool:
    raw = (os.getenv("FEATURE_IMA") or os.getenv("VITE_FEATURE_IMA") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _guard() -> None:
    if not ima_enabled():
        raise HTTPException(status_code=501, detail="not implemented")


def _http_error(exc: ImaError) -> HTTPException:
    status = {"missing_key": 400, "bad_key": 401, "rate_limit": 429, "unreachable": 502, "api": 502}.get(exc.kind, 502)
    return HTTPException(status_code=status, detail=exc.message)


async def _json_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw or not raw.strip():
        return {}
    import json

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="invalid json") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="invalid json")
    return payload


@router.get("")
def list_connectors() -> dict:
    _guard()
    with open_session() as db:
        rows = db.exec(select(ConnectorAccount).order_by(ConnectorAccount.id)).all()
        accounts = [public_account(row) for row in rows if row.connector_type == "ima"]
    return {
        "types": [{"type": "ima", "display_name": "腾讯 ima"}],
        "accounts": accounts,
        "configured": configured(),
        "credential_source": credential_source(),
    }


@router.post("")
async def create_connector(request: Request) -> dict:
    _guard()
    body = await _json_body(request)
    connector_type = str(body.get("type") or body.get("connector_type") or "").strip()
    if connector_type != "ima":
        raise HTTPException(status_code=422, detail="目前只支持 ima 连接器。")
    if _has_secret(body.get("config")) or _has_secret(body):
        raise HTTPException(status_code=400, detail="密钥不会写入连接器配置。请在设置中保存 IMA_CLIENT_ID 和 IMA_API_KEY。")
    if not configured():
        from app.connectors.ima_client import MISSING_KEY_MESSAGE

        raise HTTPException(status_code=400, detail=MISSING_KEY_MESSAGE)
    display_name = str(body.get("display_name") or "腾讯 ima").strip() or "腾讯 ima"
    with open_session() as db:
        row = ensure_account(db, display_name)
        return public_account(row)


@router.get("/ima/bases")
def list_ima_bases() -> dict:
    _guard()
    try:
        with open_ima_client() as client:
            overview = list_overview(client)
        with open_session() as db:
            ensure_account(db)
        return overview
    except ImaError as exc:
        raise _http_error(exc) from exc


@router.get("/ima/folders")
def list_ima_folders(
    knowledge_base_id: str | None = None,
    folder_id: str | None = None,
    notebook_id: str | None = None,
    cursor: str = "",
) -> dict:
    _guard()
    try:
        with open_ima_client() as client:
            return list_children(
                client,
                knowledge_base_id=knowledge_base_id,
                folder_id=folder_id,
                notebook_id=notebook_id,
                cursor=cursor,
            )
    except ImaError as exc:
        raise _http_error(exc) from exc


@router.post("/ima/search")
async def search_ima(request: Request) -> dict:
    _guard()
    body = await _json_body(request)
    query = str(body.get("query") or "").strip()
    knowledge_base_id = str(body.get("knowledge_base_id") or "").strip() or None
    try:
        with open_ima_client() as client:
            return search(client, query, knowledge_base_id=knowledge_base_id)
    except ImaError as exc:
        raise _http_error(exc) from exc


@router.post("/ima/import")
async def import_ima(request: Request) -> dict:
    _guard()
    body = await _json_body(request)
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="请选择要导入的笔记或文件。")
    cleaned = [item for item in items if isinstance(item, dict)]
    registry = getattr(request.app.state, "registry", None)
    try:
        with open_ima_client() as client:
            with open_session() as db:
                return import_items(db, client, cleaned, registry=registry)
    except ImaError as exc:
        raise _http_error(exc) from exc


@router.post("/{connector_id}/sync")
async def sync_connector(connector_id: str) -> dict:
    _guard()
    account_id = _parse_id(connector_id)
    try:
        with open_ima_client() as client:
            overview = list_overview(client)
    except ImaError as exc:
        _remember_error(account_id, exc.message)
        raise _http_error(exc) from exc
    with open_session() as db:
        row = db.get(ConnectorAccount, account_id)
        if row is None or row.connector_type != "ima":
            raise HTTPException(status_code=404, detail="未找到连接器")
        from app.db.models import utcnow

        row.last_sync_at = utcnow()
        row.last_error = None
        row.sync_cursor = None
        config = dict(row.config or {})
        config["last_kb_count"] = overview.get("kb_count") or 0
        row.config = config
        db.add(row)
        db.commit()
        db.refresh(row)
        payload = public_account(row)
    payload["kb_count"] = overview.get("kb_count") or 0
    payload["imported"] = 0
    payload["detail"] = "已刷新知识库列表。请选择条目再导入。官方配额未确认，不会自动拉全库。"
    return payload


@router.get("/{connector_id}/status")
def connector_status(connector_id: str) -> dict:
    _guard()
    account_id = _parse_id(connector_id)
    with open_session() as db:
        row = db.get(ConnectorAccount, account_id)
        if row is None or row.connector_type != "ima":
            raise HTTPException(status_code=404, detail="未找到连接器")
        payload = public_account(row)
    payload["configured"] = configured()
    payload["credential_source"] = credential_source()
    return payload


@router.delete("/{connector_id}")
def delete_connector(connector_id: str) -> dict:
    _guard()
    account_id = _parse_id(connector_id)
    with open_session() as db:
        row = db.get(ConnectorAccount, account_id)
        if row is None or row.connector_type != "ima":
            raise HTTPException(status_code=404, detail="未找到连接器")
        db.delete(row)
        db.commit()
    return {
        "id": account_id,
        "deleted": True,
        "detail": "已断开 ima。已导入的条目仍留在本地库，但新的提问不会再检索它们。",
    }


def _parse_id(connector_id: str) -> int:
    if not str(connector_id).isdigit():
        raise HTTPException(status_code=422, detail="connector id 无效")
    return int(connector_id)


def _has_secret(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if str(key).lower() in _SECRET_KEYS and item:
            return True
    return False


def _remember_error(account_id: int, message: str) -> None:
    with open_session() as db:
        row = db.get(ConnectorAccount, account_id)
        if row is None:
            return
        row.last_error = message[:500]
        db.add(row)
        db.commit()

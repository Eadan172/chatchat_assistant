"""Unified import API.

``GET  /import/sources``                — every channel, implemented or placeholder
``POST /import/sources/{id}/browse``    — list containers (vaults, KBs, folders)
``POST /import/sources/{id}/import``    — pull selected items through the channel
``POST /import/documents``              — normalized single-document import

A new channel only implements ``ImportSource`` and calls ``register()``; the
routes and the desktop UI stay unchanged.
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.db.session import open_session
from app.i18n import locale_from, t
from app.imports.protocol import ChannelUnavailable
from app.imports.registry import all_sources, get_source
from app.ingest.local_file import ImportError as DocumentImportError
from app.ingest.local_file import import_payload

router = APIRouter()
logger = logging.getLogger(__name__)


def _locale(request: Request, explicit: str | None = None) -> str:
    return locale_from(explicit, request.headers)


def _source_or_404(source_id: str, locale: str):
    source = get_source(source_id)
    if source is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "import.unknown_source", "source": source_id, "message": source_id},
        )
    return source


class BrowseBody(BaseModel):
    parent_id: str | None = None
    locale: str | None = None


class ChannelImportBody(BaseModel):
    item_ids: list[str] = Field(default_factory=list)
    container_id: str | None = None
    locale: str | None = None


class DocumentBody(BaseModel):
    filename: str
    text: str | None = None
    content_base64: str | None = None
    source: str = "local"
    external_id: str | None = None
    title: str | None = None
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    locale: str | None = None


@router.get("/import/sources")
def list_sources(request: Request, locale: str | None = None) -> dict:
    code = _locale(request, locale)
    rows = all_sources(code)
    return {"sources": rows, "total": len(rows), "implemented": sum(1 for row in rows if row["implemented"])}


@router.get("/import/sources/{source_id}")
def get_source_detail(source_id: str, request: Request, locale: str | None = None) -> dict:
    code = _locale(request, locale)
    source = _source_or_404(source_id, code)
    rows = {row["id"]: row for row in all_sources(code)}
    return {"source": rows.get(source_id, source.descriptor.public(code))}


@router.post("/import/sources/{source_id}/browse")
def browse_source(source_id: str, body: BrowseBody, request: Request) -> dict:
    code = _locale(request, body.locale)
    source = _source_or_404(source_id, code)
    try:
        containers = source.browse(body.parent_id, code)
    except ChannelUnavailable as exc:
        raise HTTPException(
            status_code=501,
            detail={
                "code": "import.not_implemented",
                "source": exc.source_id,
                "message": exc.message or t("import.not_implemented", code, name=source_id),
                "missing": exc.missing,
            },
        ) from exc
    return {"containers": [item.public() for item in containers], "items": [], "source": source_id}


@router.post("/import/sources/{source_id}/import")
def import_from_source(source_id: str, body: ChannelImportBody, request: Request) -> dict:
    code = _locale(request, body.locale)
    source = _source_or_404(source_id, code)
    descriptor = source.descriptor
    if not descriptor.implemented or descriptor.ui == "panel":
        raise HTTPException(
            status_code=501,
            detail={
                "code": "import.not_implemented",
                "source": source_id,
                "message": t("import.not_implemented", code, name=descriptor.name_en if code == "en" else descriptor.name_zh),
                "missing": [field["key"] for field in descriptor.config_fields if not descriptor.configured],
            },
        )
    missing = [field["key"] for field in descriptor.config_fields if not descriptor.configured]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "import.missing_config",
                "source": source_id,
                "message": t("import.missing_config", code, name=descriptor.name_zh, fields=", ".join(missing)),
                "missing": missing,
            },
        )
    documents: list[dict] = []
    for item_id in body.item_ids:
        try:
            document = source.fetch(item_id, code)
        except ChannelUnavailable as exc:
            raise HTTPException(status_code=501, detail={"code": "import.not_implemented", "message": exc.message}) from exc
        documents.append(_store(document, source_id, request))
    return {"imported": documents, "total": len(documents), "source": source_id}


@router.post("/import/documents")
def import_document(body: DocumentBody, request: Request) -> dict:
    """Normalized import entry used by the upload UI and every future channel."""
    code = _locale(request, body.locale)
    if body.text is None and not body.content_base64:
        raise HTTPException(status_code=400, detail={"code": "import.empty", "message": "content required"})
    try:
        data = body.text.encode("utf-8") if body.text is not None else base64.b64decode(body.content_base64 or "")
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": "import.bad_base64", "message": str(exc)}) from exc
    source = body.source.strip() or "local"
    origin = {
        "platform": source,
        "source_id": body.external_id or body.filename,
        "source_kind": "connector" if source not in ("local", "local_file") else "local_file",
        "title": body.title or body.filename,
    }
    try:
        with open_session() as db:
            result = import_payload(db, filename=body.filename, data=data, origin=origin)
    except DocumentImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": "import.failed", "message": exc.detail}) from exc
    return {"item": result, "source": source, "locale": code}


def _store(document, source_id: str, request: Request) -> dict:
    data = document.data
    if data is None:
        data = (document.text or "").encode("utf-8")
    origin = {
        "platform": source_id,
        "source_id": document.external_id or document.filename,
        "source_kind": "connector",
        "title": document.title or document.filename,
    }
    try:
        with open_session() as db:
            return import_payload(db, filename=document.filename, data=data, origin=origin)
    except DocumentImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": "import.failed", "message": exc.detail}) from exc

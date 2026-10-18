"""Conversation memory API: conversations, folders, projects, forks, import/export."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db.session import open_session
from app.i18n import locale_from
from app.memory import service
from app.memory.service import MemoryStoreError

router = APIRouter()
logger = logging.getLogger(__name__)


def _guard(action):
    try:
        return action()
    except MemoryStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": "memory.error", "message": exc.message}) from exc


class ConversationCreate(BaseModel):
    title: str | None = None
    mode: str = "personal"
    folder_id: int | None = None
    project_id: int | None = None
    locale: str | None = None
    style: str | None = None


class ConversationPatch(BaseModel):
    title: str | None = None
    folder_id: int | None = None
    project_id: int | None = None
    locale: str | None = None
    style: str | None = None
    mode: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


class MessageIn(BaseModel):
    role: str = "user"
    content: str = ""
    citations: list[Any] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None


class ForkIn(BaseModel):
    message_id: int | None = None
    title: str | None = None


class FolderIn(BaseModel):
    name: str
    project_id: int | None = None
    sort_order: int = 0


class FolderPatch(BaseModel):
    name: str | None = None
    project_id: int | None = None
    sort_order: int | None = None


class ProjectIn(BaseModel):
    name: str
    description: str | None = None
    color: str | None = None


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None


class ImportIn(BaseModel):
    filename: str = "conversations.json"
    content: str
    folder_id: int | None = None
    project_id: int | None = None
    locale: str | None = None


class ClearIn(BaseModel):
    confirm: str = ""
    keep_folders: bool = False
    keep_projects: bool = False


@router.get("/conversations")
def list_conversations(
    request: Request,
    folder_id: int | None = None,
    project_id: int | None = None,
    q: str | None = None,
    archived: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    del request
    with open_session() as db:
        return service.list_conversations(
            db,
            folder_id=folder_id,
            project_id=project_id,
            q=q,
            archived=archived,
            limit=limit,
            offset=offset,
        )


@router.post("/conversations")
def create_conversation(body: ConversationCreate) -> dict:
    with open_session() as db:
        return _guard(lambda: service.create_conversation(db, **body.model_dump()))


@router.get("/conversations/stats")
def conversation_stats() -> dict:
    with open_session() as db:
        return service.stats(db)


@router.get("/conversations/export")
def export_all(fmt: str = Query(default="json", alias="format")) -> dict:
    with open_session() as db:
        return service.export_all(db, fmt if fmt in ("json", "md") else "json")


@router.post("/conversations/import")
def import_conversations(body: ImportIn, request: Request) -> dict:
    locale = locale_from(body.locale, request.headers)
    raw = body.content.encode("utf-8")
    if len(raw) > 16 * 1024 * 1024:
        raise HTTPException(status_code=413, detail={"code": "memory.too_large", "message": "file too large"})
    with open_session() as db:
        return _guard(
            lambda: service.import_conversations(
                db,
                raw,
                filename=body.filename,
                folder_id=body.folder_id,
                project_id=body.project_id,
            )
        ) | {"locale": locale}


@router.post("/conversations/clear")
def clear_conversations(body: ClearIn) -> dict:
    """Delete every conversation, message and related state. Requires an explicit confirm token."""
    if body.confirm != "DELETE_ALL":
        raise HTTPException(
            status_code=400,
            detail={"code": "memory.confirm_required", "message": "confirm must be DELETE_ALL"},
        )
    with open_session() as db:
        return _guard(
            lambda: service.purge_all(
                db,
                keep_folders=body.keep_folders,
                keep_projects=body.keep_projects,
            )
        )


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int) -> dict:
    with open_session() as db:
        return _guard(lambda: service.detail(db, conversation_id))


@router.patch("/conversations/{conversation_id}")
def patch_conversation(conversation_id: int, body: ConversationPatch) -> dict:
    provided = body.model_dump(exclude_unset=True)
    with open_session() as db:
        return _guard(lambda: service.update_conversation(db, conversation_id, **provided))


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int) -> dict:
    with open_session() as db:
        return _guard(lambda: service.delete_conversation(db, conversation_id))


@router.post("/conversations/{conversation_id}/messages")
def append_message(conversation_id: int, body: MessageIn) -> dict:
    with open_session() as db:
        return _guard(
            lambda: service.append_message(
                db,
                conversation_id,
                role=body.role,
                content=body.content,
                citations=body.citations,
                provider=body.provider,
                model=body.model,
            )
        )


@router.post("/conversations/{conversation_id}/fork")
def fork_conversation(conversation_id: int, body: ForkIn) -> dict:
    with open_session() as db:
        return _guard(
            lambda: service.fork_conversation(
                db,
                conversation_id,
                message_id=body.message_id,
                title=body.title,
            )
        )


@router.get("/conversations/{conversation_id}/export")
def export_conversation(conversation_id: int, fmt: str = Query(default="json", alias="format")) -> dict:
    with open_session() as db:
        return _guard(lambda: service.export_conversation(db, conversation_id, fmt if fmt in ("json", "md") else "json"))


@router.get("/folders")
def list_folders(project_id: int | None = None) -> dict:
    with open_session() as db:
        return service.list_folders(db, project_id=project_id)


@router.post("/folders")
def create_folder(body: FolderIn) -> dict:
    with open_session() as db:
        return _guard(lambda: service.create_folder(db, **body.model_dump()))


@router.patch("/folders/{folder_id}")
def patch_folder(folder_id: int, body: FolderPatch) -> dict:
    provided = body.model_dump(exclude_unset=True)
    with open_session() as db:
        return _guard(lambda: service.update_folder(db, folder_id, **provided))


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: int) -> dict:
    with open_session() as db:
        return _guard(lambda: service.delete_folder(db, folder_id))


@router.get("/projects")
def list_projects() -> dict:
    with open_session() as db:
        return service.list_projects(db)


@router.post("/projects")
def create_project(body: ProjectIn) -> dict:
    with open_session() as db:
        return _guard(lambda: service.create_project(db, **body.model_dump()))


@router.patch("/projects/{project_id}")
def patch_project(project_id: int, body: ProjectPatch) -> dict:
    provided = body.model_dump(exclude_unset=True)
    with open_session() as db:
        return _guard(lambda: service.update_project(db, project_id, **provided))


@router.delete("/projects/{project_id}")
def delete_project(project_id: int) -> dict:
    with open_session() as db:
        return _guard(lambda: service.delete_project(db, project_id))

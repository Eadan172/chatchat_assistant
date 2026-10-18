"""Conversation memory: CRUD, folders, projects, forks, export and import."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.db.models import Conversation, ConversationFolder, Message, Project, utcnow
from app.i18n import t
from app.memory.importers import ParsedConversation, parse

DEFAULT_FORK_TOKENS = 1_000_000


class MemoryStoreError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _now() -> datetime:
    return utcnow()


def approx_tokens(text: str | None) -> int:
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    latin = max(len(text) - cjk, 0)
    return cjk + latin // 4


def fork_threshold() -> int:
    raw = (os.getenv("CONTEXT_FORK_TOKENS") or "").strip()
    try:
        return max(int(raw), 200)
    except ValueError:
        return DEFAULT_FORK_TOKENS


def summary(row: Conversation, *, message_count: int, last_at: datetime | None, preview: str, tokens: int) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "mode": row.mode,
        "folder_id": row.folder_id,
        "project_id": row.project_id,
        "forked_from_id": row.forked_from_id,
        "forked_from_message_id": row.forked_from_message_id,
        "locale": row.locale,
        "style": row.style,
        "pinned": bool(row.pinned),
        "archived": bool(row.archived),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(last_at or row.updated_at or row.created_at),
        "message_count": message_count,
        "preview": preview,
        "tokens": tokens,
    }


def list_conversations(
    db: Session,
    *,
    folder_id: int | None = None,
    project_id: int | None = None,
    q: str | None = None,
    archived: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    statement = select(Conversation)
    if archived is not None:
        statement = statement.where(Conversation.archived == archived)
    if folder_id is not None:
        statement = statement.where(Conversation.folder_id == folder_id)
    if project_id is not None:
        statement = statement.where(Conversation.project_id == project_id)
    if q and q.strip():
        statement = statement.where(Conversation.title.contains(q.strip()))
    rows = list(db.exec(statement).all())
    rows.sort(key=lambda row: (bool(row.pinned), row.updated_at or row.created_at or _now()), reverse=True)
    total = len(rows)
    window = rows[offset : offset + max(limit, 0)]

    counts: dict[int, tuple[int, datetime | None, str, int]] = {}
    if window:
        ids = [int(row.id) for row in window if row.id is not None]
        messages = list(
            db.exec(select(Message).where(Message.conversation_id.in_(ids)).order_by(Message.id)).all()
        )
        for message in messages:
            key = int(message.conversation_id)
            count, _last, _preview, tokens = counts.get(key, (0, None, "", 0))
            counts[key] = (
                count + 1,
                message.created_at,
                (message.content or _preview)[:120] if message.content else _preview,
                tokens + approx_tokens(message.content),
            )
    items = []
    for row in window:
        count, last_at, preview, tokens = counts.get(int(row.id), (0, None, "", 0))
        items.append(summary(row, message_count=count, last_at=last_at, preview=preview, tokens=tokens))
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def get_conversation(db: Session, conversation_id: int) -> Conversation:
    row = db.get(Conversation, conversation_id)
    if row is None:
        raise MemoryStoreError(404, t("memory.not_found"))
    return row


def detail(db: Session, conversation_id: int) -> dict:
    row = get_conversation(db, conversation_id)
    messages = list(
        db.exec(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)).all()
    )
    tokens = sum(approx_tokens(message.content) for message in messages)
    limit = fork_threshold()
    return {
        "conversation": summary(
            row,
            message_count=len(messages),
            last_at=messages[-1].created_at if messages else None,
            preview=(messages[-1].content[:120] if messages else ""),
            tokens=tokens,
        ),
        "messages": [public_message(message) for message in messages],
        "context": {"tokens": tokens, "limit": limit, "needs_fork": tokens > limit},
    }


def public_message(message: Message) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "citations": list(message.citations or []),
        "provider": message.provider,
        "model": message.model,
        "created_at": _iso(message.created_at),
    }


def create_conversation(
    db: Session,
    *,
    title: str | None = None,
    mode: str = "personal",
    folder_id: int | None = None,
    project_id: int | None = None,
    locale: str | None = None,
    style: str | None = None,
) -> dict:
    _check_folder(db, folder_id)
    _check_project(db, project_id)
    row = Conversation(
        title=(title or "").strip() or t("memory.title"),
        mode=mode or "personal",
        folder_id=folder_id,
        project_id=project_id,
        locale=locale,
        style=style,
        updated_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return detail(db, int(row.id))


def update_conversation(db: Session, conversation_id: int, **fields: Any) -> dict:
    row = get_conversation(db, conversation_id)
    for name in ("title", "locale", "style", "mode"):
        if name in fields and fields[name] is not None:
            if name == "title":
                value = str(fields[name]).strip()
                if not value:
                    continue
                setattr(row, name, value)
            else:
                setattr(row, name, fields[name])
    # Explicit null moves a conversation out of its folder/project.
    if "folder_id" in fields:
        _check_folder(db, fields["folder_id"])
        row.folder_id = fields["folder_id"]
    if "project_id" in fields:
        _check_project(db, fields["project_id"])
        row.project_id = fields["project_id"]
    if "pinned" in fields and fields["pinned"] is not None:
        row.pinned = bool(fields["pinned"])
    if "archived" in fields and fields["archived"] is not None:
        row.archived = bool(fields["archived"])
    row.updated_at = _now()
    db.add(row)
    db.commit()
    return detail(db, conversation_id)


def delete_conversation(db: Session, conversation_id: int) -> dict:
    from sqlmodel import delete as bulk_delete

    row = get_conversation(db, conversation_id)
    # Delete children first so SQLite never sees a dangling foreign key.
    db.exec(bulk_delete(Message).where(Message.conversation_id == conversation_id))
    db.delete(row)
    db.commit()
    return {"id": conversation_id, "deleted": True}


def append_message(db: Session, conversation_id: int, *, role: str, content: str, **extra: Any) -> dict:
    row = get_conversation(db, conversation_id)
    message = Message(
        conversation_id=conversation_id,
        role=role or "user",
        content=content or "",
        citations=extra.get("citations") or [],
        provider=extra.get("provider"),
        model=extra.get("model"),
    )
    db.add(message)
    row.updated_at = _now()
    db.add(row)
    db.commit()
    db.refresh(message)
    return public_message(message)


def fork_conversation(
    db: Session,
    conversation_id: int,
    *,
    message_id: int | None = None,
    title: str | None = None,
) -> dict:
    source = get_conversation(db, conversation_id)
    messages = list(
        db.exec(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)).all()
    )
    kept = [message for message in messages if message_id is None or (message.id or 0) <= message_id]
    branch_point = kept[-1].id if kept else None
    label = (title or "").strip() or f"{source.title} · fork"
    clone = Conversation(
        title=label[:200],
        mode=source.mode,
        folder_id=source.folder_id,
        project_id=source.project_id,
        forked_from_id=source.id,
        forked_from_message_id=branch_point,
        locale=source.locale,
        style=source.style,
        updated_at=_now(),
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    for message in kept:
        db.add(
            Message(
                conversation_id=int(clone.id),
                role=message.role,
                content=message.content,
                citations=list(message.citations or []),
                provider=message.provider,
                model=message.model,
                prompt_tokens=message.prompt_tokens,
                completion_tokens=message.completion_tokens,
                latency_ms=message.latency_ms,
                created_at=message.created_at,
            )
        )
    db.commit()
    return detail(db, int(clone.id))


def export_conversation(db: Session, conversation_id: int, fmt: str = "json") -> dict:
    payload = detail(db, conversation_id)
    if fmt == "md":
        return {"format": "md", "content": to_markdown(payload), "filename": _filename(payload["conversation"])}
    return {
        "format": "json",
        "payload": {
            "schema": "chatchat.conversation.v1",
            "exported_at": _iso(_now()),
            **payload,
        },
        "filename": _filename(payload["conversation"]) + ".json",
    }


def export_all(db: Session, fmt: str = "json") -> dict:
    rows = list(db.exec(select(Conversation).order_by(Conversation.id)).all())
    conversations = [detail(db, int(row.id)) for row in rows if row.id is not None]
    if fmt == "md":
        body = "\n\n---\n\n".join(to_markdown(item) for item in conversations)
        return {"format": "md", "content": body, "filename": "chatchat-conversations.md"}
    return {
        "format": "json",
        "payload": {
            "schema": "chatchat.export.v1",
            "exported_at": _iso(_now()),
            "conversations": conversations,
        },
        "filename": "chatchat-conversations.json",
    }


def to_markdown(payload: dict) -> str:
    conversation = payload.get("conversation") or {}
    lines = [f"# {conversation.get('title') or t('memory.title')}", ""]
    for message in payload.get("messages") or []:
        label = "用户" if message.get("role") == "user" else "助手"
        lines.append(f"**{label}**")
        lines.append("")
        lines.append(str(message.get("content") or ""))
        citations = message.get("citations") or []
        if citations:
            refs = ", ".join(f"[{item.get('n')}] {item.get('filename') or item.get('title')}" for item in citations)
            lines.append("")
            lines.append(f"> 参考: {refs}")
        lines.append("")
    return "\n".join(lines).strip()


def import_conversations(
    db: Session,
    raw: bytes,
    *,
    filename: str = "",
    folder_id: int | None = None,
    project_id: int | None = None,
) -> dict:
    _check_folder(db, folder_id)
    _check_project(db, project_id)
    try:
        parsed = parse(raw, filename)
    except ValueError as exc:
        raise MemoryStoreError(400, str(exc)) from exc
    created: list[dict] = []
    for item in parsed:
        created.append(_persist_import(db, item, folder_id=folder_id, project_id=project_id))
    return {"imported": created, "total": len(created)}


def _persist_import(
    db: Session,
    item: ParsedConversation,
    *,
    folder_id: int | None,
    project_id: int | None,
) -> dict:
    row = Conversation(
        title=(item.title or t("memory.title"))[:200],
        mode="personal",
        folder_id=folder_id,
        project_id=project_id,
        source_policy={"imported_from": item.source, "external_id": item.external_id},
        updated_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    for message in item.messages:
        db.add(
            Message(
                conversation_id=int(row.id),
                role=message.role,
                content=message.content,
                created_at=message.created_at or _now(),
            )
        )
    db.commit()
    loaded = detail(db, int(row.id))
    return loaded["conversation"]


def list_folders(db: Session, *, project_id: int | None = None) -> dict:
    statement = select(ConversationFolder)
    if project_id is not None:
        statement = statement.where(ConversationFolder.project_id == project_id)
    rows = list(db.exec(statement).all())
    rows.sort(key=lambda row: (row.sort_order or 0, row.id or 0))
    counts = _folder_counts(db)
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "project_id": row.project_id,
                "sort_order": row.sort_order,
                "conversation_count": counts.get(int(row.id or 0), 0),
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ],
        "total": len(rows),
    }


def create_folder(db: Session, *, name: str, project_id: int | None = None, sort_order: int = 0) -> dict:
    _check_project(db, project_id)
    row = ConversationFolder(name=(name or "").strip() or "Folder", project_id=project_id, sort_order=sort_order, updated_at=_now())
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "name": row.name, "project_id": row.project_id, "sort_order": row.sort_order, "created_at": _iso(row.created_at)}


def update_folder(db: Session, folder_id: int, **fields: Any) -> dict:
    row = db.get(ConversationFolder, folder_id)
    if row is None:
        raise MemoryStoreError(404, t("memory.folder_not_found"))
    if fields.get("name") is not None and str(fields["name"]).strip():
        row.name = str(fields["name"]).strip()
    if "project_id" in fields and fields["project_id"] is not None:
        _check_project(db, fields["project_id"])
        row.project_id = fields["project_id"]
    if fields.get("sort_order") is not None:
        row.sort_order = int(fields["sort_order"])
    row.updated_at = _now()
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name, "project_id": row.project_id, "sort_order": row.sort_order}


def delete_folder(db: Session, folder_id: int) -> dict:
    row = db.get(ConversationFolder, folder_id)
    if row is None:
        raise MemoryStoreError(404, t("memory.folder_not_found"))
    for conversation in db.exec(select(Conversation).where(Conversation.folder_id == folder_id)).all():
        conversation.folder_id = None
        db.add(conversation)
    db.delete(row)
    db.commit()
    return {"id": folder_id, "deleted": True}


def list_projects(db: Session) -> dict:
    rows = list(db.exec(select(Project)).all())
    rows.sort(key=lambda row: (row.name or "").lower())
    counts: dict[int, int] = {}
    for conversation in db.exec(select(Conversation)).all():
        if conversation.project_id:
            counts[int(conversation.project_id)] = counts.get(int(conversation.project_id), 0) + 1
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "description": row.description,
                "color": row.color,
                "conversation_count": counts.get(int(row.id or 0), 0),
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ],
        "total": len(rows),
    }


def create_project(db: Session, *, name: str, description: str | None = None, color: str | None = None) -> dict:
    row = Project(name=(name or "").strip() or "Project", description=description, color=color, updated_at=_now())
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "name": row.name, "description": row.description, "color": row.color, "created_at": _iso(row.created_at)}


def update_project(db: Session, project_id: int, **fields: Any) -> dict:
    row = db.get(Project, project_id)
    if row is None:
        raise MemoryStoreError(404, t("memory.project_not_found"))
    if fields.get("name") is not None and str(fields["name"]).strip():
        row.name = str(fields["name"]).strip()
    for name in ("description", "color"):
        if name in fields and fields[name] is not None:
            setattr(row, name, fields[name])
    row.updated_at = _now()
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name, "description": row.description, "color": row.color}


def delete_project(db: Session, project_id: int) -> dict:
    row = db.get(Project, project_id)
    if row is None:
        raise MemoryStoreError(404, t("memory.project_not_found"))
    for folder in db.exec(select(ConversationFolder).where(ConversationFolder.project_id == project_id)).all():
        folder.project_id = None
        db.add(folder)
    for conversation in db.exec(select(Conversation).where(Conversation.project_id == project_id)).all():
        conversation.project_id = None
        db.add(conversation)
    db.delete(row)
    db.commit()
    return {"id": project_id, "deleted": True}


def purge_all(db: Session, *, keep_folders: bool = False, keep_projects: bool = False) -> dict:
    """Delete every conversation and its related state, after writing a backup.

    The backup covers conversations, messages, folders and projects so a test
    run can be restored; knowledge-base documents are never touched.
    """
    from app.db.models import ClarifySession, DecisionLog

    conversations = list(db.exec(select(Conversation)).all())
    messages = list(db.exec(select(Message)).all())
    folders = list(db.exec(select(ConversationFolder)).all())
    projects = list(db.exec(select(Project)).all())
    sessions = list(db.exec(select(ClarifySession)).all())
    decisions = list(db.exec(select(DecisionLog)).all())

    backup = {
        "schema": "chatchat.purge-backup.v1",
        "exported_at": _iso(_now()),
        "conversations": [detail(db, int(row.id)) for row in conversations if row.id is not None],
        "folders": [
            {"id": row.id, "name": row.name, "project_id": row.project_id, "sort_order": row.sort_order}
            for row in folders
        ],
        "projects": [
            {"id": row.id, "name": row.name, "description": row.description, "color": row.color}
            for row in projects
        ],
    }
    backup_path = _write_backup(backup)

    # Children first: message -> conversation, then folders/projects. Bulk
    # statements avoid ORM flush ordering tripping the foreign keys.
    from sqlmodel import delete as bulk_delete

    db.exec(bulk_delete(Message))
    db.exec(bulk_delete(ClarifySession))
    db.exec(bulk_delete(DecisionLog))
    db.exec(bulk_delete(Conversation))
    if not keep_folders:
        db.exec(bulk_delete(ConversationFolder))
    elif not keep_projects:
        # Folders stay, so detach them before their project rows disappear.
        for row in folders:
            row.project_id = None
            db.add(row)
        db.commit()
    if not keep_projects:
        db.exec(bulk_delete(Project))
    db.commit()

    return {
        "ok": True,
        "deleted": {
            "conversations": len(conversations),
            "messages": len(messages),
            "folders": 0 if keep_folders else len(folders),
            "projects": 0 if keep_projects else len(projects),
            "clarify_sessions": len(sessions),
            "decision_logs": len(decisions),
        },
        "kept": {"folders": keep_folders, "projects": keep_projects},
        "backup_path": str(backup_path),
        "backup_file": backup_path.name,
        "backup_bytes": backup_path.stat().st_size,
    }


def _write_backup(payload: dict) -> Path:
    import json

    root = Path(os.getenv("DATA_DIR") or ".")
    folder = root / "backup"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"conversations-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def stats(db: Session) -> dict:
    rows = list(db.exec(select(Conversation)).all())
    messages = list(db.exec(select(Message)).all())
    tokens = sum(approx_tokens(message.content) for message in messages)
    return {
        "conversations": len(rows),
        "messages": len(messages),
        "folders": len(list(db.exec(select(ConversationFolder)).all())),
        "projects": len(list(db.exec(select(Project)).all())),
        "forked": sum(1 for row in rows if row.forked_from_id),
        "tokens": tokens,
        "fork_threshold": fork_threshold(),
    }


def _folder_counts(db: Session) -> dict[int, int]:
    counts: dict[int, int] = {}
    for conversation in db.exec(select(Conversation)).all():
        if conversation.folder_id:
            counts[int(conversation.folder_id)] = counts.get(int(conversation.folder_id), 0) + 1
    return counts


def _check_folder(db: Session, folder_id: int | None) -> None:
    if folder_id is None:
        return
    if db.get(ConversationFolder, folder_id) is None:
        raise MemoryStoreError(404, t("memory.folder_not_found"))


def _check_project(db: Session, project_id: int | None) -> None:
    if project_id is None:
        return
    if db.get(Project, project_id) is None:
        raise MemoryStoreError(404, t("memory.project_not_found"))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _filename(conversation: dict) -> str:
    title = str(conversation.get("title") or "conversation").strip()
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in title)[:60]
    return safe or "conversation"

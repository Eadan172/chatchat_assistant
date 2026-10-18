"""Import adapters for conversation exports from other tools.

Supported today:
- ChatGPT export (bulk ``conversations`` or a single ``mapping`` tree)
- ChatGPT / generic ``messages`` arrays
- Claude web export (``chat_messages``)
- Claude Code JSONL transcripts (``type: user|assistant`` lines)
- Markdown / plain text transcripts written as ``User:`` / ``Assistant:``

Every parser returns the same normalized shape, so the storage layer never
needs to know which product produced the file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.i18n import t


@dataclass(slots=True)
class ParsedMessage:
    role: str
    content: str
    created_at: datetime | None = None


@dataclass(slots=True)
class ParsedConversation:
    title: str
    messages: list[ParsedMessage] = field(default_factory=list)
    source: str = "generic"
    external_id: str | None = None
    created_at: datetime | None = None


ROLE_MAP = {
    "user": "user",
    "human": "user",
    "assistant": "assistant",
    "ai": "assistant",
    "bot": "assistant",
    "system": "system",
    "tool": "tool",
}


def parse(raw: bytes, filename: str = "") -> list[ParsedConversation]:
    text = _decode(raw)
    stripped = text.strip()
    if not stripped:
        raise ValueError(t("memory.empty_import"))

    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(stripped)
            return _from_json(data)
        except json.JSONDecodeError:
            pass
        # JSON Lines: Claude Code transcripts.
        rows: list[dict] = []
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
        if rows:
            return _from_jsonl(rows, filename)
    if filename.lower().endswith((".md", ".markdown", ".txt")):
        return _from_markdown(stripped, filename)
    if _looks_like_transcript(stripped):
        return _from_markdown(stripped, filename)
    raise ValueError(t("memory.bad_import"))


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _from_json(data: Any) -> list[ParsedConversation]:
    if isinstance(data, list):
        # A bare list of conversations, or a bare list of messages.
        if data and isinstance(data[0], dict) and _is_message(data[0]):
            return [ParsedConversation(title=_title_from_messages(data), messages=_messages(data))]
        return [_conversation(item) for item in data if isinstance(item, dict)]

    if not isinstance(data, dict):
        raise ValueError(t("memory.bad_import"))

    if isinstance(data.get("conversations"), list):
        return [_conversation(item) for item in data["conversations"] if isinstance(item, dict)]
    if isinstance(data.get("chats"), list):
        return [_conversation(item) for item in data["chats"] if isinstance(item, dict)]
    if "mapping" in data:
        return [_conversation(data)]
    if isinstance(data.get("messages"), list) or isinstance(data.get("chat_messages"), list):
        return [_conversation(data)]
    if "content" in data and ("title" in data or "name" in data):
        return [_conversation(data)]
    raise ValueError(t("memory.bad_import"))


def _conversation(item: dict) -> ParsedConversation:
    # "chatchat.purge-backup.v1" rows wrap metadata and messages together.
    if isinstance(item.get("conversation"), dict) and isinstance(item.get("messages"), list):
        meta = item["conversation"]
        messages = [parsed for parsed in (_message(row) for row in item["messages"]) if parsed]
        messages = [message for message in messages if message.content.strip()]
        if not messages:
            raise ValueError(t("memory.empty_import"))
        return ParsedConversation(
            title=str(meta.get("title") or "").strip() or _title_from_messages(messages),
            messages=messages,
            source="chatchat-backup",
            external_id=str(meta.get("id") or "") or None,
            created_at=_time(meta.get("created_at")),
        )

    title = str(item.get("title") or item.get("name") or "").strip()
    created = _time(item.get("create_time") or item.get("created_at") or item.get("updated_at"))
    if isinstance(item.get("mapping"), dict):
        messages = _from_mapping(item["mapping"], item.get("current_node"))
        source = "chatgpt"
    elif isinstance(item.get("chat_messages"), list):
        messages = [_message(row) for row in item["chat_messages"]]
        source = "claude"
    elif isinstance(item.get("messages"), list):
        messages = [_message(row) for row in item["messages"]]
        source = "generic"
    else:
        messages = []
        source = "generic"
    messages = [message for message in messages if message and message.content.strip()]
    if not messages:
        raise ValueError(t("memory.empty_import"))
    return ParsedConversation(
        title=title or _title_from_messages(messages),
        messages=messages,
        source=source,
        external_id=str(item.get("id") or item.get("conversation_id") or "") or None,
        created_at=created,
    )


def _from_mapping(mapping: dict, current_node: str | None) -> list[ParsedMessage]:
    node_id = current_node if current_node in mapping else None
    if node_id is None:
        node_id = next((key for key, row in mapping.items() if isinstance(row, dict) and not row.get("parent")), None)
    chain: list[dict] = []
    seen: set[str] = set()
    while node_id and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        node = mapping[node_id]
        if isinstance(node, dict):
            chain.append(node)
            node_id = node.get("parent")
        else:
            break
    chain.reverse()
    if not chain:  # fall back to insertion order
        chain = [row for row in mapping.values() if isinstance(row, dict)]
    messages: list[ParsedMessage] = []
    for node in chain:
        message = node.get("message")
        if isinstance(message, dict):
            parsed = _message(message)
            if parsed:
                messages.append(parsed)
    return messages


def _message(row: dict) -> ParsedMessage | None:
    if not isinstance(row, dict):
        return None
    author = row.get("author")
    role = ""
    if isinstance(author, dict):
        role = str(author.get("role") or "")
    role = role or str(row.get("role") or row.get("sender") or row.get("from") or "")
    role = ROLE_MAP.get(role.strip().lower(), role.strip().lower() or "user")

    content = row.get("content")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
            text = "".join(_part_text(part) for part in parts)
        elif isinstance(content.get("text"), str):
            text = content["text"]
    elif isinstance(content, list):
        text = "".join(_part_text(part) for part in content)
    if not text:
        for key in ("text", "message", "body", "content_text"):
            value = row.get(key)
            if isinstance(value, str):
                text = value
                break
    created = _time(row.get("create_time") or row.get("created_at") or row.get("timestamp"))
    return ParsedMessage(role=role, content=text.strip(), created_at=created)


def _part_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        for key in ("text", "content", "value"):
            value = part.get(key)
            if isinstance(value, str):
                return value
    return ""


def _from_jsonl(rows: list[dict], filename: str) -> list[ParsedConversation]:
    messages: list[ParsedMessage] = []
    title = ""
    for row in rows:
        kind = str(row.get("type") or "").lower()
        if kind in ("summary", "system", "meta"):
            title = title or str(row.get("summary") or "")
            continue
        payload = row.get("message") if isinstance(row.get("message"), dict) else row
        parsed = _message(payload)
        if parsed is None or kind in ("file-history-snapshot", "queue-operation"):
            continue
        if not parsed.content:
            continue
        messages.append(parsed)
        if not title and parsed.role == "user":
            title = parsed.content[:40]
    if not messages:
        raise ValueError(t("memory.empty_import"))
    return [
        ParsedConversation(
            title=title or filename.rsplit(".", 1)[0] or t("memory.title"),
            messages=messages,
            source="claude-code",
        )
    ]


def _from_markdown(text: str, filename: str) -> list[ParsedConversation]:
    lines = text.splitlines()
    title = ""
    if lines and lines[0].lstrip().startswith("#"):
        title = lines[0].lstrip("# ").strip()
        lines = lines[1:]
    messages: list[ParsedMessage] = []
    current_role: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if current_role and buffer:
            body = "\n".join(buffer).strip()
            if body:
                messages.append(ParsedMessage(role=current_role, content=body))
        buffer = []

    for line in lines:
        marker = _role_marker(line)
        if marker:
            flush()
            current_role = marker
            continue
        if current_role:
            buffer.append(line)
    flush()
    if not messages:
        raise ValueError(t("memory.bad_import"))
    return [
        ParsedConversation(
            title=title or filename.rsplit(".", 1)[0] or t("memory.title"),
            messages=messages,
            source="markdown",
        )
    ]


def _role_marker(line: str) -> str | None:
    compact = line.strip().lower()
    for prefix, role in (
        ("**user**", "user"),
        ("**human**", "user"),
        ("user:", "user"),
        ("human:", "user"),
        ("用户：", "user"),
        ("用户:", "user"),
        ("**assistant**", "assistant"),
        ("**ai**", "assistant"),
        ("assistant:", "assistant"),
        ("ai:", "assistant"),
        ("助手：", "assistant"),
        ("助手:", "assistant"),
    ):
        if compact.startswith(prefix):
            return role
    return None


def _looks_like_transcript(text: str) -> bool:
    lowered = text.lower()
    return "user:" in lowered and ("assistant:" in lowered or "ai:" in lowered)


def _is_message(row: dict) -> bool:
    return any(key in row for key in ("role", "author", "sender")) and "content" in row


def _messages(rows: list[dict]) -> list[ParsedMessage]:
    return [parsed for parsed in (_message(row) for row in rows) if parsed]


def _title_from_messages(messages: list[ParsedMessage] | list[dict]) -> str:
    for row in messages:
        if isinstance(row, ParsedMessage):
            if row.role == "user" and row.content:
                return row.content.strip().splitlines()[0][:40]
        elif isinstance(row, dict):
            role = str(row.get("role") or "")
            if role == "user":
                text = _message(row)
                if text and text.content:
                    return text.content.splitlines()[0][:40]
    return t("memory.title")


def _time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            seconds = float(value)
            if seconds > 1e12:
                seconds /= 1000.0
            return datetime.utcfromtimestamp(seconds)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed.replace(tzinfo=None)
        except ValueError:
            return None
    return None

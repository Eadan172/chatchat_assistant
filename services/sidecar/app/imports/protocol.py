"""Unified import channel contract for the knowledge base.

Every channel — the built-in file upload today, Obsidian / Feishu / Tencent
Docs later — exposes the same four calls so the desktop UI never needs a
channel-specific code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SourceDescriptor:
    id: str
    name_zh: str
    name_en: str
    kind: str = "connector"  # connector | local | placeholder
    implemented: bool = False
    configured: bool = False
    capabilities: list[str] = field(default_factory=list)
    config_fields: list[dict[str, Any]] = field(default_factory=list)
    docs_url: str | None = None
    ui: str = "unified"  # unified | panel
    note_zh: str | None = None
    note_en: str | None = None

    def public(self, locale: str = "zh") -> dict[str, Any]:
        english = locale == "en"
        return {
            "id": self.id,
            "name": self.name_en if english else self.name_zh,
            "kind": self.kind,
            "implemented": self.implemented,
            "configured": self.configured,
            "capabilities": list(self.capabilities),
            "config_fields": list(self.config_fields),
            "docs_url": self.docs_url,
            "ui": self.ui,
            "note": self.note_en if english else self.note_zh,
        }


@dataclass(slots=True)
class ContainerRef:
    id: str
    name: str
    kind: str = "folder"  # folder | knowledge_base | vault
    parent_id: str | None = None
    item_count: int | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "parent_id": self.parent_id,
            "item_count": self.item_count,
        }


@dataclass(slots=True)
class SourceItemRef:
    id: str
    name: str
    kind: str = "document"  # document | note | page | file
    size: int | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "size": self.size,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class ImportDocument:
    filename: str
    text: str | None = None
    data: bytes | None = None
    title: str | None = None
    external_id: str | None = None
    url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ChannelUnavailable(RuntimeError):
    """Raised when a channel is declared but not wired yet."""

    def __init__(self, source_id: str, message: str, *, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.source_id = source_id
        self.message = message
        self.missing = missing or []


class ImportSource:
    """Default implementation: declared but not connected."""

    descriptor: SourceDescriptor

    def status(self, locale: str = "zh") -> dict[str, Any]:
        return self.descriptor.public(locale)

    def browse(self, parent_id: str | None = None, locale: str = "zh") -> list[ContainerRef]:
        raise ChannelUnavailable(
            self.descriptor.id,
            self.descriptor.note_en if locale == "en" else self.descriptor.note_zh or "",
        )

    def fetch(self, item_id: str, locale: str = "zh") -> ImportDocument:
        raise ChannelUnavailable(
            self.descriptor.id,
            self.descriptor.note_en if locale == "en" else self.descriptor.note_zh or "",
        )

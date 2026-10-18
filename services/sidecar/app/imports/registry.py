"""Import channel registry. One place to add Obsidian / Feishu / Tencent Docs."""

from __future__ import annotations

from app.imports.protocol import ImportSource
from app.imports.sources import (
    ImaPanelSource,
    LocalUploadSource,
    feishu_source,
    notion_source,
    obsidian_source,
    tencent_docs_source,
)

_SOURCES: dict[str, ImportSource] | None = None


def _build() -> dict[str, ImportSource]:
    sources: list[ImportSource] = [
        LocalUploadSource(),
        ImaPanelSource(),
        obsidian_source(),
        feishu_source(),
        tencent_docs_source(),
        notion_source(),
    ]
    return {source.descriptor.id: source for source in sources}


def all_sources(locale: str = "zh") -> list[dict]:
    global _SOURCES
    if _SOURCES is None:
        _SOURCES = _build()
    rows: list[dict] = []
    for source in _SOURCES.values():
        refresh = getattr(source, "refresh", None)
        if callable(refresh):
            refresh()
        configured = getattr(source, "configured_now", None)
        if callable(configured):
            source.descriptor.configured = bool(configured())
        rows.append(source.descriptor.public(locale))
    return rows


def get_source(source_id: str) -> ImportSource | None:
    global _SOURCES
    if _SOURCES is None:
        _SOURCES = _build()
    return _SOURCES.get(source_id)


def register(source: ImportSource) -> None:
    """Entry point for a future channel: register() then it shows up in /import/sources."""
    global _SOURCES
    if _SOURCES is None:
        _SOURCES = _build()
    _SOURCES[source.descriptor.id] = source

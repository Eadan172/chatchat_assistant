"""Choose which ima hits may be cited.

Notes whose title and excerpt share too little with the question are dropped.
Rows that only have a title — typical of the ima subscription library, where
``get_media_info`` returns 220030 and ``media_type`` 6 (WeChat) has no link —
are listed after the answer, not passed to the model as evidence.

Relevance uses keyword overlap by default. ``ASK_NOTE_FILTER=vector`` switches
to the local hashing embedder. Neither path calls Gemini or OpenAI. BGE, when
``EMBED_PROVIDER=bge``, still downloads through the hf-mirror default in the
embedder.
"""

from __future__ import annotations

import os
import re
from typing import Any

from app.index.tokens import tokens

_EMPTY_SENTENCE = re.compile(r"有内容以[^。\n]{0,80}为题[。]?")
_EMPTY_LINE = re.compile(r"(?m)^[ \t]*[-*•]?\s*只有标题[^\n]{0,24}\n?")
_MARKS = re.compile(r"[\s\W_]+", re.UNICODE)


def relevance_threshold() -> float:
    default = 0.45 if _filter_mode() == "vector" else 0.2
    raw = (os.getenv("ASK_NOTE_RELEVANCE") or str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    if value < 0.0 or value > 1.0:
        return default
    return value


def relevance_score(query: str, title: str, text: str) -> float:
    document = f"{title}\n{text}".strip()
    if _filter_mode() == "vector":
        return _vector_score(query, document)
    return _overlap_score(query, document)


def note_is_relevant(query: str, title: str, text: str) -> bool:
    return relevance_score(query, title, text) + 1e-9 >= relevance_threshold()


def usable_excerpt(title: str, text: str) -> str:
    """Body that can support a claim. A title repeated as the excerpt is not one."""
    body = re.sub(r"\s+", " ", (text or "").strip())
    if not body:
        return ""
    if _norm(body) == _norm(title):
        return ""
    return body


def prepare_ima(query: str, hits: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split ima hits into citable rows and title-only related rows.

    Irrelevant notes are omitted from both. This does not call ``get_media_info``
    and does not search WeChat by title.
    """
    citable: list[dict[str, Any]] = []
    related: list[dict[str, Any]] = []
    for hit in hits:
        kind = str(hit.get("ima_kind") or hit.get("kind") or "")
        if kind == "folder":
            continue
        title = str(hit.get("title") or "").strip()
        text = str(hit.get("text") or "")
        if kind == "note" and not note_is_relevant(query, title, text):
            continue
        excerpt = usable_excerpt(title, text)
        if not excerpt:
            if title:
                related.append(hit)
            continue
        copied = dict(hit)
        copied["text"] = excerpt
        citable.append(copied)
    return citable, related


def related_section(items: list[dict[str, Any]]) -> str:
    titles: list[str] = []
    for item in items:
        title = str(item.get("title") or "").strip()
        if title and title not in titles:
            titles.append(title)
    if not titles:
        return ""
    lines = ["相关条目", *(f"- {title}" for title in titles)]
    return "\n".join(lines)


def append_related(text: str, items: list[dict[str, Any]]) -> str:
    section = related_section(items)
    base = (text or "").strip()
    if not section:
        return base
    if not base:
        return section
    return f"{base}\n\n{section}"


def strip_empty_points(text: str) -> str:
    """Remove filler that only restates a title."""
    cleaned = _EMPTY_SENTENCE.sub("", text or "")
    cleaned = _EMPTY_LINE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _filter_mode() -> str:
    mode = (os.getenv("ASK_NOTE_FILTER") or "overlap").strip().lower()
    if mode == "vector":
        return "vector"
    return "overlap"


def _significant(text: str) -> set[str]:
    return {token for token in tokens(text) if len(token) >= 2}


def _overlap_score(query: str, document: str) -> float:
    left = _significant(query)
    if not left:
        return 1.0
    right = _significant(document)
    if not right:
        return 0.0
    return len(left & right) / float(min(len(left), len(right)))


def _vector_score(query: str, document: str) -> float:
    from app.index.embedder import hash_embed

    return _cosine(hash_embed(query or " "), hash_embed(document or " "))


def _cosine(left: list[float], right: list[float]) -> float:
    size = min(len(left), len(right))
    if size == 0:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for index in range(size):
        dot += left[index] * right[index]
        left_norm += left[index] * left[index]
        right_norm += right[index] * right[index]
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm ** 0.5 * right_norm ** 0.5)


def _norm(value: str) -> str:
    return _MARKS.sub("", value or "").lower()

"""Paragraph chunker. tiktoken is not required and is not imported."""

from __future__ import annotations

import re

from app.index.tokens import token_count

_SENTENCE = re.compile(r"(?<=[。！？.!?])\s*")


def chunk_item(item, *, target: int = 480, overlap: int = 80) -> list[str]:
    """Aggregate paragraphs toward ``target`` tokens and keep a short overlap.

    The title is prepended to the first chunk. An item with no body yields no
    chunks.
    """
    body = getattr(item, "body_text", "") or ""
    title = (getattr(item, "title", None) or "").strip()
    paragraphs = [part.strip() for part in str(body).split("\n") if part.strip()]
    if not paragraphs and str(body).strip():
        paragraphs = [str(body).strip()]
    if not paragraphs:
        return []

    pieces: list[str] = []
    for paragraph in paragraphs:
        pieces.extend(_split_piece(paragraph, target))

    chunks: list[str] = []
    buffer: list[str] = []
    count = 0
    for piece in pieces:
        piece_count = max(1, token_count(piece))
        if buffer and count + piece_count > target:
            chunks.append("\n".join(buffer))
            tail = _tail(chunks[-1], overlap)
            buffer = [tail, piece] if tail else [piece]
            count = token_count("\n".join(buffer))
        else:
            buffer.append(piece)
            count += piece_count
    if buffer:
        chunks.append("\n".join(buffer))

    if title and chunks and not chunks[0].startswith(title):
        chunks[0] = f"{title}\n{chunks[0]}"
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _split_piece(text: str, target: int) -> list[str]:
    if token_count(text) <= target:
        return [text]
    sentences = [part.strip() for part in _SENTENCE.split(text) if part.strip()]
    if len(sentences) <= 1:
        return _window(text, target)
    grouped: list[str] = []
    buf: list[str] = []
    count = 0
    for sentence in sentences:
        sentence_count = max(1, token_count(sentence))
        if buf and count + sentence_count > target:
            grouped.append("".join(buf))
            buf = [sentence]
            count = sentence_count
        else:
            buf.append(sentence)
            count += sentence_count
    if buf:
        grouped.append("".join(buf))
    return grouped or [text]


def _window(text: str, target: int) -> list[str]:
    size = max(40, target)
    return [text[index : index + size] for index in range(0, len(text), size)]


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0:
        return ""
    return text[-overlap:].strip()

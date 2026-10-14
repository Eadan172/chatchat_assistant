"""LLM gateway stub.

openai / keyring are imported only if a method is called. M0 startup never
calls these methods, and torch is never imported here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Iterable


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str


class LLMGateway:
    def __init__(self, registry) -> None:
        self._registry = registry

    async def chat(self, messages: Iterable[ChatMessage], **kwargs) -> str:
        raise NotImplementedError("LLMGateway.chat is an M0 stub")

    async def chat_stream(self, messages: Iterable[ChatMessage], **kwargs) -> AsyncIterator[str]:
        raise NotImplementedError("LLMGateway.chat_stream is an M0 stub")
        yield ""  # pragma: no cover

    async def embed(self, texts: list[str], **kwargs) -> list[list[float]]:
        raise NotImplementedError("LLMGateway.embed is an M0 stub")

"""LLM gateway.

Remote calls use an OpenAI-compatible HTTP endpoint. openai / keyring / torch
are not imported here. A missing endpoint raises OfflineModel so retrieval can
answer without a key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Iterable

from app.llm.endpoint import Endpoint, chat_endpoint


class OfflineModel(RuntimeError):
    """No chat endpoint is configured, or the caller asked for the offline path."""


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str


class LLMGateway:
    def __init__(self, registry) -> None:
        self._registry = registry

    def resolve(self, *, provider_id: str | None = None, model: str | None = None) -> Endpoint | None:
        return chat_endpoint(self._registry, provider_id=provider_id, model=model)

    def complete(
        self,
        messages: Iterable[ChatMessage | dict],
        *,
        provider_id: str | None = None,
        model: str | None = None,
    ) -> tuple[str, int, int, str, str]:
        endpoint = self.resolve(provider_id=provider_id, model=model)
        if endpoint is None:
            raise OfflineModel("no chat endpoint configured")
        text, prompt_tokens, completion_tokens = _post_chat(endpoint, list(messages))
        return text, prompt_tokens, completion_tokens, endpoint.provider_id, endpoint.model

    async def chat(self, messages: Iterable[ChatMessage], **kwargs) -> str:
        text, _prompt, _completion, _provider, _model = self.complete(
            messages,
            provider_id=kwargs.get("provider_id"),
            model=kwargs.get("model"),
        )
        return text

    async def chat_stream(self, messages: Iterable[ChatMessage], **kwargs) -> AsyncIterator[str]:
        text = await self.chat(messages, **kwargs)
        if text:
            yield text

    async def embed(self, texts: list[str], **kwargs) -> list[list[float]]:
        from app.index.embedder import embed_texts

        return embed_texts(texts, self._registry)


def _post_chat(endpoint: Endpoint, messages: list[ChatMessage | dict]) -> tuple[str, int, int]:
    import httpx

    payload = {
        "model": endpoint.model,
        "messages": [_message(item) for item in messages],
        "temperature": 0.2,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {endpoint.api_key}"}
    with httpx.Client(timeout=endpoint.timeout_s) as client:
        response = client.post(endpoint.join("chat/completions"), json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("chat response had no choices")
    message = choices[0].get("message") or {}
    text = message.get("content") or ""
    usage = body.get("usage") or {}
    return str(text), int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def _message(item: ChatMessage | dict) -> dict[str, str]:
    if isinstance(item, ChatMessage):
        return {"role": item.role, "content": item.content}
    return {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")}

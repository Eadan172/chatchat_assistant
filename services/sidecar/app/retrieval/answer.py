"""Ground answers in retrieved chunks.

When an OpenAI-compatible chat endpoint is configured it is used. Any missing
key or failed call falls back to a deterministic extractive answer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.llm.gateway import LLMGateway, OfflineModel
from app.retrieval.hybrid import scope_text

logger = logging.getLogger(__name__)

NOT_FOUND = "当前知识库中没有找到相关内容。"
_SENTENCE = re.compile(r"[^。！？!?\n]+[。！？!?]?")


@dataclass(slots=True)
class Answer:
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    gap: str | None = None


def compose_answer(
    query: str,
    hits: list[dict],
    brief: dict | None,
    *,
    registry=None,
    provider_id: str | None = None,
    model: str | None = None,
) -> Answer:
    scope = scope_text(brief)
    if not hits:
        return Answer(
            text=_missing(scope),
            provider="offline",
            model="hash-extractive",
            gap="not_found",
        )
    gateway = LLMGateway(registry)
    try:
        text, prompt_tokens, completion_tokens, provider, model_name = gateway.complete(
            _messages(query, hits, scope),
            provider_id=provider_id,
            model=model,
        )
        if scope and scope not in text:
            text = f"按澄清范围「{scope}」。\n{text}"
        return Answer(
            text=text.strip() or _extractive(query, hits, scope),
            provider=provider,
            model=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    except OfflineModel:
        pass
    except Exception:
        logger.exception("chat model failed; using the extractive fallback")
    return Answer(text=_extractive(query, hits, scope), provider="offline", model="hash-extractive")


def _missing(scope: str | None) -> str:
    if scope:
        return f"按澄清范围「{scope}」检索后，{NOT_FOUND}"
    return NOT_FOUND


def _extractive(query: str, hits: list[dict], scope: str | None) -> str:
    lines: list[str] = []
    if scope:
        lines.append(f"按澄清范围「{scope}」。")
    for hit in hits[:3]:
        sentence = _best_sentence(query, str(hit.get("text") or ""))
        lines.append(f"[{hit.get('n')}] {sentence}")
    lines.append("以上内容来自知识库原文。")
    return "\n".join(lines)


def _best_sentence(query: str, text: str) -> str:
    from app.index.tokens import tokens

    wanted = {token for token in tokens(query) if len(token) >= 2}
    pieces = [part.strip() for part in _SENTENCE.findall(text) if part.strip()]
    if not pieces:
        return text[:180].strip()
    scored = [(len(wanted & set(tokens(piece))), index, piece) for index, piece in enumerate(pieces)]
    best = max(score for score, _index, _piece in scored)
    if best <= 0:
        return pieces[0][:400]
    chosen = [piece for score, _index, piece in scored if score == best]
    return "".join(chosen[:2])[:400]


def _messages(query: str, hits: list[dict], scope: str | None) -> list[dict[str, str]]:
    system = (
        "你是个人知识库助手。请严格依据【证据】回答："
        "只使用证据中的信息，不要引入外部知识；"
        "每处关键结论后用 [n] 标注证据编号；"
        "证据不足时明确回答“当前知识库中没有找到相关内容”，不要编造。"
    )
    if scope:
        system += f"用户已确认的范围约束：{scope}。回答必须落在该范围内。"
    evidence = "\n\n".join(
        f"[{hit.get('n')}] 文件={hit.get('filename') or hit.get('title')} 片段={hit.get('ordinal')}\n{hit.get('text')}"
        for hit in hits
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"【证据】\n{evidence}\n\n【问题】{query}"},
    ]

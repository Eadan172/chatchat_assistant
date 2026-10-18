"""Ground answers in retrieved chunks.

When an OpenAI-compatible chat endpoint is configured it is used. Any missing
key or failed call falls back to a deterministic extractive answer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.i18n import normalize_locale, t
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
    locale: str = "zh",
    style_directive: str | None = None,
) -> Answer:
    code = normalize_locale(locale)
    scope = scope_text(brief)
    if not hits:
        return Answer(
            text=_missing(scope, code),
            provider="offline",
            model="hash-extractive",
            gap="not_found",
        )
    gateway = LLMGateway(registry)
    try:
        text, prompt_tokens, completion_tokens, provider, model_name = gateway.complete(
            _messages(query, hits, scope, code, style_directive),
            provider_id=provider_id,
            model=model,
        )
        if scope and scope not in text:
            prefix = t("chat.scoped_prefix", code, scope=scope)
            text = f"{prefix}\n{text}"
        return Answer(
            text=text.strip() or _extractive(query, hits, scope, code),
            provider=provider,
            model=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    except OfflineModel:
        pass
    except Exception:
        logger.exception("chat model failed; using the extractive fallback")
    return Answer(text=_extractive(query, hits, scope, code), provider="offline", model="hash-extractive")


def _missing(scope: str | None, locale: str = "zh") -> str:
    message = t("chat.not_found", locale)
    if scope:
        return t("chat.scoped_missing", locale, scope=scope, message=message)
    return message


def _extractive(query: str, hits: list[dict], scope: str | None, locale: str = "zh") -> str:
    lines: list[str] = []
    if scope:
        lines.append(t("chat.scoped_prefix", locale, scope=scope))
    for hit in hits[:3]:
        sentence = _best_sentence(query, str(hit.get("text") or ""))
        lines.append(f"[{hit.get('n')}] {sentence}")
    lines.append(t("chat.extractive_tail", locale))
    return "\n".join(lines)


def _best_sentence(query: str, text: str) -> str:
    from app.index.tokens import tokens

    wanted = {token for token in tokens(query) if len(token) >= 2}
    pieces = [part.strip() for part in _SENTENCE.findall(text) if part.strip()]
    if not pieces:
        return text[:180].strip()
    scored = [(_sentence_score(query, piece, wanted), index, piece) for index, piece in enumerate(pieces)]
    best = max(score for score, _index, _piece in scored)
    if best <= 0:
        return pieces[0][:400]
    chosen = [piece for score, _index, piece in scored if score == best]
    return "".join(chosen[:2])[:400]


def _sentence_score(query: str, piece: str, wanted: set[str]) -> int:
    from app.index.tokens import tokens

    overlap = len(wanted & set(tokens(piece)))
    bonus = 0
    if any(mark in query for mark in ("多久", "多长时间", "什么时候", "何时")):
        if any(mark in piece for mark in ("小时", "分钟", "时间", "天")):
            bonus += 3
    if "多少" in query and any(mark in piece for mark in ("克", "斤", "勺", "毫升", "次")):
        bonus += 2
    return overlap + bonus


def _messages(
    query: str,
    hits: list[dict],
    scope: str | None,
    locale: str = "zh",
    style_directive: str | None = None,
) -> list[dict[str, str]]:
    if normalize_locale(locale) == "en":
        system = (
            "You are a personal knowledge-base assistant. Answer strictly from the [evidence]: "
            "use only facts stated there; cite every key claim with [n]; "
            'if the evidence is insufficient, say "No relevant content was found in the current knowledge base." '
            "and do not invent facts. Write the answer in English."
        )
    else:
        system = (
            "你是个人知识库助手。请严格依据【证据】回答："
            "只使用证据中的信息，不要引入外部知识；"
            "每处关键结论后用 [n] 标注证据编号；"
            "证据不足时明确回答“当前知识库中没有找到相关内容”，不要编造。"
        )
    if scope:
        if normalize_locale(locale) == "en":
            system += f" The user confirmed this scope: {scope}. Stay inside it."
        else:
            system += f"用户已确认的范围约束：{scope}。回答必须落在该范围内。"
    if style_directive:
        system += f" {style_directive}"
    evidence = "\n\n".join(
        f"[{hit.get('n')}] 文件={hit.get('filename') or hit.get('title')} 片段={hit.get('ordinal')}\n{hit.get('text')}"
        for hit in hits
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"【证据】\n{evidence}\n\n【问题】{query}"},
    ]

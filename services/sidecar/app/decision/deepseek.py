"""DeepSeek decision backend for Jev.

Registered through ``register_decision_backend("deepseek")``. Callers that
already use the rule backend stay on it. A missing key or a failed call raises
``DecisionUnavailable`` so the ask pipeline can fall back to the rules.

Endpoint: OpenAI-compatible ``https://api.deepseek.com``, model ``deepseek-chat``.
The key is ``DEEPSEEK_API_KEY`` (or the ask secrets file). This module does not
call Gemini or api.openai.com.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping, Sequence

from app.decision.client import DecisionResult, DecisionUnavailable, register_decision_backend

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

_PLAN_SYSTEM = (
    "你是检索路由器。只输出一个 JSON 对象，不要解释。"
    '格式：{"need_clarify":"yes或no","sources":["web","ima","local"],"queries":["检索词"],"confidence":0.8,'
    '"ambiguities":[{"term":"原词","options":["候选一","候选二"]}]}。'
    "sources 只能从 web、ima、local 里选择。"
    "web 表示联网，ima 表示 ima 知识库，local 表示本地库。"
    "queries 是 1 到 3 条改写后的中文检索词。"
    "问题含糊、范围不清时 need_clarify 为 yes；问题已经明确时为 no。"
    "如果地名、人名或其他实体有多个常见所指，need_clarify 必须为 yes，"
    "ambiguities 列出每个原词和候选；没有歧义时 ambiguities 为 []。"
    "不要编造候选，也不要把没有歧义的词写进去。"
    "如果 brief_status 是 clarified，说明已经澄清过，need_clarify 必须为 no，ambiguities 为 []，"
    "queries 要写进 scope 和 constraints。"
    "美食、餐厅、景点、旅游、出行、附近玩这类本地生活问题，sources 只选 web；"
    "只有用户明确要查笔记、本地文件或知识库时才加入 ima 或 local。"
    "地点还没写死、或菜系、排名标准、价格、离地铁远近有缺时，need_clarify 必须为 yes，"
    "不要擅自选定其中一个地点；ambiguities 列出该地点的多个候选。"
)

_SOURCE_ALIASES = {
    "web": "web",
    "联网": "web",
    "网上": "web",
    "网络": "web",
    "ima": "ima",
    "知识库": "ima",
    "connector": "ima",
    "local": "local",
    "本地": "local",
    "本地库": "local",
}


class DeepSeekDecisionClient:
    def __init__(self, api_key: str, *, base_url: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_BASE).strip().rstrip("/")
        self.model = (model or os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self._plans: dict[str, dict[str, Any]] = {}

    def plan(self, context: Mapping[str, Any]) -> dict[str, Any]:
        key = json.dumps(_plan_input(context), ensure_ascii=False, sort_keys=True)
        cached = self._plans.get(key)
        if cached is not None:
            return cached
        from app.ask.redact import install_redaction

        install_redaction()
        payload = _post_chat(
            self.base_url,
            self.api_key,
            self.model,
            [
                {"role": "system", "content": _PLAN_SYSTEM},
                {"role": "user", "content": key},
            ],
            temperature=0.0,
        )
        logger.info("deepseek decision http=%s", payload.get("_http_status"))
        parsed = _extract_json(_message_text(payload))
        if not parsed:
            raise DecisionUnavailable("deepseek plan was not json")
        plan = _normalize_plan(parsed)
        self._plans[key] = plan
        return plan

    def choice(
        self,
        question_id: str,
        options: Sequence[str],
        context: Mapping[str, Any],
        *,
        multi: bool = False,
    ) -> DecisionResult:
        plan = self.plan(context)
        if question_id == "need_clarify":
            answer = plan["need_clarify"]
            if answer not in options:
                raise DecisionUnavailable("need_clarify options do not include the model answer")
            return DecisionResult(answer, plan["confidence"])
        if question_id == "route_channels":
            channels = _channels(plan["sources"])
            allowed = [name for name in channels if name in options]
            if not allowed:
                raise DecisionUnavailable("route_channels options do not include the model answer")
            return DecisionResult(allowed if multi else allowed[0], plan["confidence"])
        if question_id == "intent":
            label = "lookup" if "lookup" in options else (options[0] if options else "")
            if label not in options:
                raise DecisionUnavailable("intent options are empty")
            return DecisionResult(label, min(plan["confidence"], 0.5))
        raise DecisionUnavailable(f"no deepseek choice for {question_id}")

    def score(self, question_id: str, context: Mapping[str, Any]) -> DecisionResult:
        del context
        if question_id != "relevance":
            raise DecisionUnavailable(f"no deepseek score for {question_id}")
        return DecisionResult(0.5, 0.4)

    def noul(self, question_id: str, context: Mapping[str, Any]) -> DecisionResult:
        if question_id != "needs_verify":
            raise DecisionUnavailable(f"no deepseek noul for {question_id}")
        from app.decision.client import RuleBasedDecisionClient

        return RuleBasedDecisionClient().noul(question_id, context)


def complete_deepseek(messages: list[dict[str, str]]) -> tuple[str, int, int]:
    """One synthesis call. Raises DecisionUnavailable when the call cannot be used."""
    from app.ask.redact import install_redaction
    from app.ask.secrets import secret_value

    install_redaction()
    key = secret_value("deepseek")
    if not key:
        raise DecisionUnavailable("deepseek key is not configured")
    base = (os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_BASE).strip().rstrip("/")
    model = (os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    payload = _post_chat(base, key, model, messages, temperature=0.2)
    logger.info("deepseek synthesize http=%s", payload.get("_http_status"))
    text = _message_text(payload).strip()
    if not text:
        raise DecisionUnavailable("deepseek synthesis was empty")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt = _as_int(usage.get("prompt_tokens") if isinstance(usage, dict) else None)
    if prompt is None and isinstance(usage, dict):
        prompt = _as_int(usage.get("promptTokens"))
    completion = _as_int(usage.get("completion_tokens") if isinstance(usage, dict) else None)
    if completion is None and isinstance(usage, dict):
        completion = _as_int(usage.get("completionTokens"))
    return text, prompt or 0, completion or 0


def _factory() -> DeepSeekDecisionClient:
    from app.ask.secrets import secret_value

    key = secret_value("deepseek")
    if not key:
        raise DecisionUnavailable("deepseek key is not configured")
    return DeepSeekDecisionClient(key)


def _plan_input(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "text": str(context.get("text") or ""),
        "brief_status": context.get("brief_status"),
        "scope": context.get("scope"),
        "constraints": context.get("constraints") or "",
        "kb_name": context.get("kb_name") or "",
        "clarify": context.get("clarify") or "auto",
    }


def _normalize_plan(data: dict[str, Any]) -> dict[str, Any]:
    clarify = str(data.get("need_clarify") or data.get("clarify") or "").strip().lower()
    if clarify not in {"yes", "no"}:
        raise DecisionUnavailable("deepseek need_clarify missing")
    raw_sources = data.get("sources") or data.get("channels") or []
    if isinstance(raw_sources, str):
        raw_sources = [part.strip() for part in raw_sources.split(",") if part.strip()]
    sources: list[str] = []
    if isinstance(raw_sources, list):
        for item in raw_sources:
            mapped = _SOURCE_ALIASES.get(str(item).strip().lower()) or _SOURCE_ALIASES.get(str(item).strip())
            if mapped and mapped not in sources:
                sources.append(mapped)
    if not sources:
        raise DecisionUnavailable("deepseek sources missing")
    raw_queries = data.get("queries")
    if raw_queries is None:
        raw_queries = data.get("query") or data.get("rewritten") or []
    if isinstance(raw_queries, str):
        raw_queries = [raw_queries]
    queries: list[str] = []
    if isinstance(raw_queries, list):
        for item in raw_queries:
            text = str(item).strip()
            if text and text not in queries:
                queries.append(text)
    if not queries:
        raise DecisionUnavailable("deepseek queries missing")
    confidence = data.get("confidence", 0.66)
    try:
        number = float(confidence)
    except (TypeError, ValueError):
        number = 0.66
    number = min(1.0, max(0.0, number))
    ambiguities = _normalize_ambiguities(data.get("ambiguities"))
    if ambiguities:
        clarify = "yes"
    return {
        "need_clarify": clarify,
        "sources": sources,
        "queries": queries[:3],
        "confidence": round(number, 4),
        "ambiguities": ambiguities,
    }


def _normalize_ambiguities(raw: Any) -> list[dict[str, Any]]:
    from app.clarifier.design_tree import clean_ambiguities

    if not isinstance(raw, list):
        return []
    return clean_ambiguities([item for item in raw if isinstance(item, dict)])


def _channels(sources: Sequence[str]) -> list[str]:
    ordered: list[str] = []
    if "local" in sources:
        ordered.append("local")
    if "ima" in sources:
        ordered.append("connector")
    if "web" in sources:
        ordered.append("web")
    return ordered


def _post_chat(base: str, api_key: str, model: str, messages: list[dict[str, str]], *, temperature: float) -> dict[str, Any]:
    import httpx

    url = _chat_url(base)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}
    body = {"model": model, "temperature": temperature, "messages": messages}
    try:
        with httpx.Client(timeout=_timeout(), follow_redirects=False) as client:
            response = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        logger.info("deepseek unreachable (%s)", type(exc).__name__)
        raise DecisionUnavailable("deepseek unreachable") from None
    if response.status_code >= 400:
        logger.info("deepseek http=%s", response.status_code)
        raise DecisionUnavailable("deepseek http error")
    try:
        payload = response.json()
    except Exception as exc:
        raise DecisionUnavailable("deepseek response was not json") from exc
    if not isinstance(payload, dict):
        raise DecisionUnavailable("deepseek response was not an object")
    payload["_http_status"] = response.status_code
    return payload


def _chat_url(base: str) -> str:
    root = base.rstrip("/")
    if root.endswith("/chat/completions"):
        return root
    if root.endswith("/v1"):
        return f"{root}/chat/completions"
    return f"{root}/chat/completions"


def _message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for part in content:
                    if isinstance(part, str):
                        parts.append(part)
                    elif isinstance(part, dict):
                        parts.append(str(part.get("text") or part.get("content") or ""))
                return "".join(parts)
        text = choices[0].get("text")
        if isinstance(text, str):
            return text
    return ""


def _extract_json(content: str) -> dict[str, Any] | None:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        fence = text.rfind("```")
        if fence >= 0:
            text = text[:fence]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    return data


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _timeout() -> float:
    raw = (os.getenv("DEEPSEEK_TIMEOUT_S") or "30").strip()
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    return min(max(value, 1.0), 90.0)


register_decision_backend("deepseek", _factory)

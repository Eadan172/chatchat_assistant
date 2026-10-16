"""One question at a time, with a recommendation.

Template copy is the source of truth. When an OpenAI-compatible key is set,
the question text can be polished. Any failure, missing key, or empty
recommendation falls back to the template so tests and offline use stay up.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def render_question(
    *,
    node_id: str,
    keyword: str,
    domain_hint: str | None,
    text: str,
    recommendation: str,
    reason: str,
) -> tuple[str, str, str]:
    """Return text, recommendation, and reason. Recommendation is never empty."""
    if node_id.startswith("ambiguity-") or node_id == "budget":
        return text, recommendation, reason
    try:
        polished = polish_question(
            node_id=node_id,
            keyword=keyword,
            domain_hint=domain_hint,
            text=text,
            recommendation=recommendation,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 — polish must not block a question
        logger.info("clarify question polish failed: %s", exc.__class__.__name__)
        polished = None
    if polished is None:
        return text, recommendation, reason
    polished_text, polished_recommendation, polished_reason = polished
    if not polished_text.strip() or not polished_recommendation.strip() or not polished_reason.strip():
        return text, recommendation, reason
    return polished_text.strip(), polished_recommendation.strip(), polished_reason.strip()


def polish_question(
    *,
    node_id: str,
    keyword: str,
    domain_hint: str | None,
    text: str,
    recommendation: str,
    reason: str,
) -> tuple[str, str, str] | None:
    """Polish one question. Returns None when no LLM is configured or the call fails."""
    api_key = os.getenv("CLARIFY_LLM_API_KEY") or os.getenv("LLM_API_KEY")
    if not api_key:
        return None
    try:
        content = _call_llm(
            api_key=api_key,
            payload={
                "node_id": node_id,
                "keyword": keyword,
                "domain_hint": domain_hint,
                "text": text,
                "recommendation": recommendation,
                "reason": reason,
            },
        )
    except Exception as exc:  # noqa: BLE001 — template fallback is the contract
        logger.info("clarify question polish unavailable: %s", exc.__class__.__name__)
        return None
    parsed = _extract_json(content)
    if not parsed:
        return None
    return (
        str(parsed.get("text") or ""),
        str(parsed.get("recommendation") or ""),
        str(parsed.get("reason") or ""),
    )


def _call_llm(*, api_key: str, payload: dict[str, Any]) -> str:
    import httpx

    base_url = (
        os.getenv("CLARIFY_LLM_BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.deepseek.com"
    ).rstrip("/")
    model = os.getenv("CLARIFY_LLM_MODEL") or os.getenv("LLM_MODEL") or "deepseek-v4-flash"
    timeout = float(os.getenv("CLARIFY_LLM_TIMEOUT_S") or "4")
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你只润色一道澄清问题。保持原意，不要新增问题。"
                    '只输出 JSON：{"text","recommendation","reason"}。'
                    "recommendation 和 reason 都必须是非空字符串。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
        )
        response.raise_for_status()
        data = response.json()
    return str(data["choices"][0]["message"]["content"])


def _extract_json(content: str) -> dict[str, Any] | None:
    text = content.strip()
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

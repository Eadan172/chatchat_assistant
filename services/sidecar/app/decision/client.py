"""Choice / Score / Noul client.

Callers depend on DecisionClient. RuleBasedDecisionClient is the local
placeholder. A future endpoint implements the same three methods and is
returned from build_decision_client; this module does not call a network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


class DecisionUnavailable(RuntimeError):
    """The decision backend cannot answer. Callers log a fallback and continue."""


@dataclass(frozen=True, slots=True)
class DecisionResult:
    """One judgement. `result` is JSON-friendly; confidence is in [0, 1]."""

    result: Any
    confidence: float


class DecisionClient(Protocol):
    """Swap point for a real Jev endpoint. Each method returns result + confidence."""

    def choice(
        self,
        question_id: str,
        options: Sequence[str],
        context: Mapping[str, Any],
        *,
        multi: bool = False,
    ) -> DecisionResult:
        """Pick one option, or a list of options when multi is true."""

    def score(self, question_id: str, context: Mapping[str, Any]) -> DecisionResult:
        """Return a relevance or risk score in [0, 1], plus confidence."""

    def noul(self, question_id: str, context: Mapping[str, Any]) -> DecisionResult:
        """Yes/no gate. `result` is a bool."""


_FRESHNESS = ("最新", "还有效", "过期", "今天", "现在", "更新", "latest", "still valid", "2024", "2025", "2026")
_HOWTO = ("怎么", "如何", "怎样", "how to", "how do")
_COMPARE = ("对比", "区别", "相比", "versus", " vs ")
_WEB = ("网上", "网络", "网页", "互联网", "搜索", "online", "web")
_CONNECTOR = ("notion", "笔记", "收藏", "本地文件夹", "应用知识库")


def _text(context: Mapping[str, Any]) -> str:
    value = context.get("text") or ""
    return value if isinstance(value, str) else str(value)


def _has(text: str, markers: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in markers)


def _result(result: Any, confidence: float) -> DecisionResult:
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence is not a number")
    number = float(confidence)
    if number < 0.0 or number > 1.0:
        raise ValueError("confidence out of range")
    return DecisionResult(result=result, confidence=round(number, 4))


class RuleBasedDecisionClient:
    """Deterministic stand-in. Question ids are the policy names."""

    def choice(
        self,
        question_id: str,
        options: Sequence[str],
        context: Mapping[str, Any],
        *,
        multi: bool = False,
    ) -> DecisionResult:
        allowed = tuple(options)
        if question_id == "need_clarify":
            return _result(self._need_clarify(context, allowed), self._need_clarify_confidence(context))
        if question_id == "route_channels":
            picked = self._channels(context, allowed)
            confidence = 0.74 if picked == ["local"] else 0.63
            return _result(picked if multi else picked[0], confidence)
        if question_id == "intent":
            label, confidence = self._intent(context, allowed)
            return _result(label, confidence)
        raise DecisionUnavailable(f"no rule for choice {question_id}")

    def score(self, question_id: str, context: Mapping[str, Any]) -> DecisionResult:
        if question_id != "relevance":
            raise DecisionUnavailable(f"no rule for score {question_id}")
        text = _text(context).strip()
        if not text:
            return _result(0.0, 0.5)
        score = min(1.0, len(text) / 80.0)
        if "?" in text or "？" in text:
            score = min(1.0, score + 0.15)
        if len(text) < 8 and " " not in text and "？" not in text and "?" not in text:
            score = min(score, 0.35)
        return _result(round(score, 4), 0.52)

    def noul(self, question_id: str, context: Mapping[str, Any]) -> DecisionResult:
        if question_id != "needs_verify":
            raise DecisionUnavailable(f"no rule for noul {question_id}")
        verify = context.get("verify")
        if verify == "force":
            return _result(True, 0.9)
        if verify == "skip":
            return _result(False, 0.9)
        if _has(_text(context), _FRESHNESS):
            return _result(True, 0.72)
        return _result(False, 0.64)

    def _need_clarify(self, context: Mapping[str, Any], options: Sequence[str]) -> str:
        answer = "no"
        clarify = context.get("clarify")
        text = _text(context).strip()
        if clarify == "force":
            answer = "yes"
        elif clarify == "skip" or context.get("has_brief"):
            answer = "no"
        elif not text:
            answer = "no"
        elif len(text) <= 12 and "?" not in text and "？" not in text:
            answer = "yes"
        if answer not in options:
            raise DecisionUnavailable("need_clarify options do not include the rule answer")
        return answer

    def _need_clarify_confidence(self, context: Mapping[str, Any]) -> float:
        clarify = context.get("clarify")
        if clarify in {"force", "skip"} or context.get("has_brief"):
            return 0.92
        text = _text(context).strip()
        if not text:
            return 0.4
        if len(text) <= 12 and "?" not in text and "？" not in text:
            return 0.66
        if "?" in text or "？" in text:
            return 0.71
        return 0.55

    def _channels(self, context: Mapping[str, Any], options: Sequence[str]) -> list[str]:
        text = _text(context)
        wanted = ["local"]
        if _has(text, _WEB):
            wanted.append("web")
        if _has(text, _CONNECTOR):
            wanted.append("connector")
        if context.get("mode") == "global" and _has(text, ("汇总", "全网", "各平台")):
            wanted.append("web")
        ordered = [name for name in ("local", "connector", "web") if name in wanted and name in options]
        if "local" in options and "local" not in ordered:
            ordered.insert(0, "local")
        if not ordered:
            raise DecisionUnavailable("route_channels options are empty")
        return ordered

    def _intent(self, context: Mapping[str, Any], options: Sequence[str]) -> tuple[str, float]:
        text = _text(context)
        if _has(text, _FRESHNESS):
            label, confidence = "freshness", 0.7
        elif _has(text, _HOWTO):
            label, confidence = "howto", 0.68
        elif _has(text, _COMPARE):
            label, confidence = "compare", 0.68
        elif len(text.strip()) <= 8:
            label, confidence = "explore", 0.5
        else:
            label, confidence = "lookup", 0.6
        if label not in options:
            raise DecisionUnavailable("intent options do not include the rule answer")
        return label, confidence


def build_decision_client(kind: str = "rule") -> DecisionClient:
    """Return the active backend. Only `rule` exists until an endpoint is chosen."""
    if kind != "rule":
        raise DecisionUnavailable(f"decision backend {kind!r} is not available")
    return RuleBasedDecisionClient()

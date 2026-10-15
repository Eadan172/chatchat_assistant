"""Decision policies.

These functions choose the question and call Choice / Score / Noul. They do
not read thresholds and they do not change chat. A different DecisionClient
can answer the same question ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from app.decision.client import DecisionClient, DecisionResult

QUESTION_VERSION = "m2-shadow-1"

CHANNELS: tuple[str, ...] = ("local", "connector", "web")
INTENTS: tuple[str, ...] = ("lookup", "howto", "compare", "freshness", "explore")
CLARIFY_OPTIONS: tuple[str, ...] = ("yes", "no")


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    question_id: str
    qtype: str
    result: Any
    confidence: float
    default: Any
    options: tuple[str, ...] = ()


def _confidence(raw: DecisionResult) -> float:
    if isinstance(raw.confidence, bool) or not isinstance(raw.confidence, (int, float)):
        raise ValueError("confidence is not a number")
    number = float(raw.confidence)
    if number < 0.0 or number > 1.0:
        raise ValueError("confidence out of range")
    return round(number, 4)


def need_clarify(client: DecisionClient, context: Mapping[str, Any]) -> PolicyDecision:
    raw = client.choice("need_clarify", CLARIFY_OPTIONS, context)
    if raw.result not in CLARIFY_OPTIONS:
        raise ValueError("need_clarify result is not yes or no")
    return PolicyDecision("need_clarify", "choice", raw.result, _confidence(raw), "no", CLARIFY_OPTIONS)


def route_channels(client: DecisionClient, context: Mapping[str, Any]) -> PolicyDecision:
    raw = client.choice("route_channels", CHANNELS, context, multi=True)
    values = raw.result
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list) or not values:
        raise ValueError("route_channels result is empty")
    if any(not isinstance(item, str) or item not in CHANNELS for item in values):
        raise ValueError("route_channels result is not a channel list")
    ordered = [name for name in CHANNELS if name in values]
    return PolicyDecision("route_channels", "choice", ordered, _confidence(raw), ["local"], CHANNELS)


def needs_verify(client: DecisionClient, context: Mapping[str, Any]) -> PolicyDecision:
    raw = client.noul("needs_verify", context)
    if not isinstance(raw.result, bool):
        raise ValueError("needs_verify result is not a boolean")
    return PolicyDecision("needs_verify", "noul", raw.result, _confidence(raw), False)


def intent(client: DecisionClient, context: Mapping[str, Any]) -> PolicyDecision:
    raw = client.choice("intent", INTENTS, context)
    if not isinstance(raw.result, str) or raw.result not in INTENTS:
        raise ValueError("intent result is not a known label")
    return PolicyDecision("intent", "choice", raw.result, _confidence(raw), "lookup", INTENTS)


def relevance(client: DecisionClient, context: Mapping[str, Any]) -> PolicyDecision:
    """Log-only specificity score. No chunks exist until retrieval is implemented."""
    raw = client.score("relevance", context)
    if isinstance(raw.result, bool) or not isinstance(raw.result, (int, float)):
        raise ValueError("relevance result is not a number")
    score = float(raw.result)
    if score < 0.0 or score > 1.0:
        raise ValueError("relevance result is outside 0..1")
    return PolicyDecision("relevance", "score", round(score, 4), _confidence(raw), 0.0)


PolicyRunner = Callable[[DecisionClient, Mapping[str, Any]], PolicyDecision]

POLICIES: tuple[PolicyRunner, ...] = (
    intent,
    need_clarify,
    route_channels,
    needs_verify,
    relevance,
)


def policy_defaults() -> dict[str, tuple[str, Any]]:
    """question_id -> (qtype, safe default) for rows written when the client fails."""
    return {
        "intent": ("choice", "lookup"),
        "need_clarify": ("choice", "no"),
        "route_channels": ("choice", ["local"]),
        "needs_verify": ("noul", False),
        "relevance": ("score", 0.0),
    }


def policy_options(question_id: str) -> Sequence[str]:
    if question_id == "need_clarify":
        return CLARIFY_OPTIONS
    if question_id == "route_channels":
        return CHANNELS
    if question_id == "intent":
        return INTENTS
    return ()

"""Design tree for grill-me clarification.

Nodes keep a question, dependencies, status (open/resolved/parked), the user's
decision, and a recommendation. Only the frontier is askable: an open node
whose dependencies are all resolved. Parking a node does not block siblings
that do not depend on it. Descendants of a parked node are parked as well,
because their dependencies can no longer be resolved.

The default tree is shared across life, learning, and work. Preferred order is
goal → scope → constraints → deps → tradeoffs → risks → next. Scope,
constraints, and risks all hang off goal, so they can proceed independently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


DEFAULT_MAX_QUESTIONS = 7
MAX_QUESTIONS_LIMIT = 7

NODE_FIELDS = {
    "goal": "goal",
    "scope": "scope",
    "constraints": "constraints",
    "deps": "dependencies",
    "tradeoffs": "tradeoffs",
    "risks": "risks",
    "next": "next_step",
    "budget": "constraints",
}

_GOAL_RECOMMENDATIONS = {
    "learning": "两周内能独立讲清「{keyword}」，并标出还没掌握的部分",
    "work": "本周内给「{keyword}」一个可执行结论，以及下一步由谁来做",
    "life": "用一个小行动验证「{keyword}」是否值得继续投入",
}


@dataclass
class DesignNode:
    id: str
    text: str
    recommendation: str
    reason: str
    depends_on: list[str]
    status: str = "open"
    decision: str | None = None
    action: str | None = None
    park_reason: str | None = None
    presented_text: str | None = None
    presented_recommendation: str | None = None
    presented_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "recommendation": self.recommendation,
            "reason": self.reason,
            "depends_on": list(self.depends_on),
            "status": self.status,
            "decision": self.decision,
            "action": self.action,
            "park_reason": self.park_reason,
            "presented_text": self.presented_text,
            "presented_recommendation": self.presented_recommendation,
            "presented_reason": self.presented_reason,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DesignNode:
        return cls(
            id=str(raw["id"]),
            text=str(raw.get("text") or ""),
            recommendation=str(raw.get("recommendation") or ""),
            reason=str(raw.get("reason") or ""),
            depends_on=[str(item) for item in raw.get("depends_on") or []],
            status=str(raw.get("status") or "open"),
            decision=raw.get("decision"),
            action=raw.get("action"),
            park_reason=raw.get("park_reason"),
            presented_text=raw.get("presented_text"),
            presented_recommendation=raw.get("presented_recommendation"),
            presented_reason=raw.get("presented_reason"),
        )

    def public_question(self) -> dict[str, str]:
        return {
            "id": self.id,
            "text": self.presented_text or self.text,
            "recommendation": self.presented_recommendation or self.recommendation,
            "reason": self.presented_reason or self.reason,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.presented_text or self.text,
            "recommendation": self.presented_recommendation or self.recommendation,
            "reason": self.presented_reason or self.reason,
            "status": self.status,
            "decision": self.decision,
            "park_reason": self.park_reason,
        }


@dataclass
class DesignTree:
    keyword: str
    domain_hint: str | None
    max_questions: int
    nodes: list[DesignNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "domain_hint": self.domain_hint,
            "max_questions": self.max_questions,
            "nodes": [node.to_dict() for node in self.nodes],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DesignTree:
        if not isinstance(raw, dict) or "nodes" not in raw:
            raise ValueError("clarify session has no design tree")
        return cls(
            keyword=str(raw.get("keyword") or ""),
            domain_hint=raw.get("domain_hint"),
            max_questions=int(raw.get("max_questions") or DEFAULT_MAX_QUESTIONS),
            nodes=[DesignNode.from_dict(item) for item in raw.get("nodes") or []],
        )

    def node(self, node_id: str) -> DesignNode | None:
        for item in self.nodes:
            if item.id == node_id:
                return item
        return None

    def progress(self) -> dict[str, int]:
        counts = {"resolved": 0, "open": 0, "parked": 0}
        for node in self.nodes:
            if node.status in counts:
                counts[node.status] += 1
        return counts

    def frontier(self) -> list[DesignNode]:
        by_id = {node.id: node for node in self.nodes}
        ready: list[DesignNode] = []
        for node in self.nodes:
            if node.status != "open":
                continue
            if any(dep not in by_id for dep in node.depends_on):
                continue
            if all(by_id[dep].status == "resolved" for dep in node.depends_on):
                ready.append(node)
        return ready

    def current(self) -> DesignNode | None:
        found = self.frontier()
        return found[0] if found else None

    def is_complete(self) -> bool:
        return bool(self.nodes) and all(node.status in {"resolved", "parked"} for node in self.nodes)

    def park_blocked(self) -> None:
        """Park open nodes that depend, directly or indirectly, on a parked node."""
        by_id = {node.id: node for node in self.nodes}
        changed = True
        while changed:
            changed = False
            for node in self.nodes:
                if node.status != "open":
                    continue
                if _depends_on_parked(node, by_id):
                    node.status = "parked"
                    node.action = "park"
                    node.decision = None
                    node.park_reason = node.park_reason or "上游决策已搁置"
                    changed = True

    def park_open(self, reason: str) -> None:
        for node in self.nodes:
            if node.status == "open":
                node.status = "parked"
                node.action = "park"
                node.decision = None
                node.park_reason = reason

    def apply(self, question_id: str, action: str, value: str | None, park_reason: str | None) -> None:
        node = self.node(question_id)
        if node is None:
            raise DesignTreeError(404, "未找到这个问题")
        if node.status == "resolved" and action == "edit":
            text = (value or "").strip()
            if not text:
                raise DesignTreeError(422, "修改时需要填写内容")
            node.decision = text
            node.action = "edit"
            return
        if node.status != "open":
            raise DesignTreeError(409, "这个问题已经处理过了")
        current = self.current()
        if current is None or current.id != node.id:
            raise DesignTreeError(409, "只能回答当前这一题")
        if action == "accept":
            recommendation = (node.presented_recommendation or node.recommendation).strip()
            if not recommendation:
                raise DesignTreeError(409, "这一题还没有推荐答案")
            node.decision = recommendation
            node.status = "resolved"
            node.action = "accept"
            node.park_reason = None
        elif action == "edit":
            text = (value or "").strip()
            if not text:
                raise DesignTreeError(422, "修改时需要填写内容")
            node.decision = text
            node.status = "resolved"
            node.action = "edit"
            node.park_reason = None
        elif action == "park":
            node.status = "parked"
            node.action = "park"
            node.decision = None
            node.park_reason = (park_reason or "").strip() or "用户搁置"
        else:
            raise DesignTreeError(422, "action 只能是 accept、edit 或 park")
        self.park_blocked()


class DesignTreeError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _depends_on_parked(node: DesignNode, by_id: dict[str, DesignNode]) -> bool:
    seen: set[str] = set()
    stack = list(node.depends_on)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        parent = by_id.get(current)
        if parent is None:
            continue
        if parent.status == "parked":
            return True
        stack.extend(parent.depends_on)
    return False


def _fill(template: str, keyword: str) -> str:
    return template.replace("{keyword}", keyword)


def _templates(keyword: str, domain_hint: str | None) -> list[dict[str, Any]]:
    hint = (domain_hint or "").strip().lower() or None
    goal_recommendation = _fill(
        _GOAL_RECOMMENDATIONS.get(hint, "先定义一个两周内能验收的结果：说清「{keyword}」做到什么算完成"),
        keyword,
    )
    rows: list[dict[str, Any]] = [
        {
            "id": "goal",
            "depends_on": [],
            "text": _fill("关于「{keyword}」，这次怎样才算成功？", keyword),
            "recommendation": goal_recommendation,
            "reason": "成功标准决定后面要检索什么、回答到什么程度。",
        },
        {
            "id": "scope",
            "depends_on": ["goal"],
            "text": "这次要覆盖哪些范围，哪些明确先不做？",
            "recommendation": _fill("只处理与「{keyword}」直接相关的材料，先不扩展到相邻话题。", keyword),
            "reason": "范围收得住，检索才不会散。",
        },
        {
            "id": "constraints",
            "depends_on": ["goal"],
            "text": "有哪些必须遵守的约束（时间、工具、预算或资料来源）？",
            "recommendation": "优先用你已经收藏的本地资料，并给一个较短的完成期限。",
            "reason": "约束决定证据从哪来、答案能有多深。",
        },
        {
            "id": "deps",
            "depends_on": ["scope"],
            "text": _fill("要回答「{keyword}」，还缺哪些前置信息或材料？", keyword),
            "recommendation": "先补齐直接相关的基础定义，以及你已经记下来的笔记。",
            "reason": "前置没齐时，不应该假装已经能查。",
        },
        {
            "id": "tradeoffs",
            "depends_on": ["constraints"],
            "text": "如果时间和完整度冲突，你更倾向哪一边？",
            "recommendation": "先要一个能用的简版结论，需要时再加深。",
            "reason": "这个取舍决定回答的详略。",
        },
        {
            "id": "risks",
            "depends_on": ["goal"],
            "text": "你最希望回答时刻意避开哪种风险？",
            "recommendation": "避开没有出处的结论，以及可能已经过时的做法。",
            "reason": "风险偏好会影响要不要标来源、要不要查证。",
        },
        {
            "id": "next",
            "depends_on": ["scope", "constraints"],
            "text": "澄清之后，你希望我下一步做什么？",
            "recommendation": "按已确认的范围检索，并给出可执行的下一步。",
            "reason": "下一步归属决定是否进入检索。",
        },
    ]
    return rows


def clamp_max_questions(value: int | None, fallback: int = DEFAULT_MAX_QUESTIONS) -> int:
    chosen = fallback if value is None else value
    if chosen < 1 or chosen > MAX_QUESTIONS_LIMIT:
        raise DesignTreeError(422, f"max_questions 需要在 1 到 {MAX_QUESTIONS_LIMIT} 之间")
    return chosen


def clean_ambiguities(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Keep {term, options} rows that name at least two distinct candidates."""
    found: list[dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term") or "").strip()
        options: list[str] = []
        values = item.get("options") or []
        if isinstance(values, str):
            values = [part.strip() for part in re.split(r"[、,，]", values) if part.strip()]
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value).strip()
            if text and text != term and text not in options:
                options.append(text)
        if term and len(options) >= 2:
            found.append({"term": term, "options": options[:6]})
    return found


def build_tree(
    keyword: str,
    domain_hint: str | None,
    max_questions: int,
    ambiguities: list[dict[str, Any]] | None = None,
) -> DesignTree:
    cleaned = clean_ambiguities(ambiguities)
    hint = (domain_hint or "").strip().lower() or None
    if cleaned:
        return DesignTree(
            keyword=keyword,
            domain_hint=hint,
            max_questions=max_questions,
            nodes=_ambiguity_nodes(cleaned, max_questions),
        )
    chosen: list[DesignNode] = []
    chosen_ids: set[str] = set()
    for row in _templates(keyword, domain_hint):
        if len(chosen) >= max_questions:
            break
        depends_on = list(row["depends_on"])
        if any(dep not in chosen_ids for dep in depends_on):
            continue
        chosen.append(
            DesignNode(
                id=row["id"],
                text=row["text"],
                recommendation=row["recommendation"],
                reason=row["reason"],
                depends_on=depends_on,
            )
        )
        chosen_ids.add(row["id"])
    return DesignTree(keyword=keyword, domain_hint=hint, max_questions=max_questions, nodes=chosen)


def _ambiguity_nodes(items: list[dict[str, Any]], max_questions: int) -> list[DesignNode]:
    """One multiple-choice question per ambiguous term, then budget and taste."""
    reserve_budget = max_questions >= 2
    room = max_questions - (1 if reserve_budget else 0)
    chosen = items[: max(room, 1)]
    nodes: list[DesignNode] = []
    ids: list[str] = []
    for index, item in enumerate(chosen):
        if len(nodes) >= max_questions:
            break
        node_id = f"ambiguity-{index}"
        ids.append(node_id)
        options = list(item["options"])
        lines = [f"「{item['term']}」指的是哪一个？", *(f"{number}. {option}" for number, option in enumerate(options, start=1))]
        nodes.append(
            DesignNode(
                id=node_id,
                text="\n".join(lines),
                recommendation=options[0],
                reason="这个词有多个常见所指，先选定再检索。",
                depends_on=[],
            )
        )
    if reserve_budget and len(nodes) < max_questions:
        nodes.append(
            DesignNode(
                id="budget",
                text="预算和口味有什么要求？",
                recommendation="预算不限，口味不限。",
                reason="预算和口味会改变筛选范围。",
                depends_on=ids,
            )
        )
    return nodes


def build_clarified_brief(tree: DesignTree) -> dict[str, Any]:
    values: dict[str, str | None] = {field: None for field in NODE_FIELDS.values()}
    parked: list[dict[str, str]] = []
    scope_bits: list[str] = []
    for node in tree.nodes:
        if node.id.startswith("ambiguity-") and node.status == "resolved" and node.decision:
            scope_bits.append(str(node.decision))
        field_name = NODE_FIELDS.get(node.id)
        if node.status == "resolved" and field_name:
            values[field_name] = node.decision
        elif node.status == "parked":
            parked.append({"id": node.id, "reason": node.park_reason or "用户搁置"})
    if scope_bits:
        values["scope"] = "；".join(scope_bits)
    return {
        "brief": "clarified",
        "status": "clarified",
        "keyword": tree.keyword,
        "domain_hint": tree.domain_hint,
        "goal": values["goal"],
        "scope": values["scope"],
        "constraints": values["constraints"],
        "dependencies": values["dependencies"],
        "tradeoffs": values["tradeoffs"],
        "risks": values["risks"],
        "next_step": values["next_step"],
        "parked": parked,
        "success_criteria": values["goal"],
        "decisions": [node.snapshot() for node in tree.nodes],
    }


def skipped_brief(keyword: str, domain_hint: str | None = None) -> dict[str, Any]:
    return {
        "brief": "skipped",
        "status": "skipped",
        "keyword": keyword,
        "domain_hint": (domain_hint or "").strip().lower() or None,
        "goal": None,
        "scope": None,
        "constraints": None,
        "dependencies": None,
        "tradeoffs": None,
        "risks": None,
        "next_step": None,
        "parked": [],
        "success_criteria": None,
        "decisions": [],
    }

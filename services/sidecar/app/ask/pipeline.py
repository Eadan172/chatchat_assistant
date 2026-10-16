"""Clarify, route, search, and answer. Used by POST /ask when FEATURE_ASK is on.

The existing /chat route does not call this module. A missing DeepSeek key or
a failed model call falls back to the rule decider and a grouped list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.ask.redact import install_redaction
from app.ask.synthesize import synthesize
from app.clarifier.design_tree import vague_food_query
from app.clarifier.session import (
    ClarifyError,
    answer_session,
    confirm_session,
    get_session_view,
    start_session,
)
from app.connectors.ima_client import MISSING_KEY_MESSAGE, ImaError
from app.connectors.ima_secrets import configured as ima_configured
from app.decision.client import DecisionUnavailable, RuleBasedDecisionClient, build_decision_client
from app.decision.policies import need_clarify, route_channels
from app.freshness.policies import apply_chat_freshness, verify_enabled
from app.retrieval.hybrid import scope_text
from app.web_search.live import search_web
from app.web_search.page import enrich_web_hits

logger = logging.getLogger(__name__)

ASK_CLARIFY_LIMIT = 3
FOOD_CLARIFY_LIMIT = 5
SOURCE_ORDER = ("local", "ima", "web")


@dataclass
class Plan:
    need_clarify: str
    sources: list[str]
    queries: list[str]
    backend: str
    fallback: str | None = None
    ambiguities: list[dict[str, Any]] = field(default_factory=list)


def iter_ask(
    *,
    message: str,
    session_id: int | None,
    answers: list[dict[str, Any]] | None,
    kb_name: str | None,
    mode: str,
    verify: str,
    clarify: str,
    skip_rest: bool,
    registry,
    db,
) -> Iterator[tuple[str, dict[str, Any]]]:
    install_redaction()
    text = (message or "").strip()
    if session_id is None and not text:
        raise ClarifyError(422, "请填写问题")
    name = (kb_name or "").strip()
    if session_id is not None:
        view = _advance(db, session_id, answers or [], skip_rest=skip_rest)
        if view["state"] != "confirmed":
            yield from _clarify_events(view)
            return
        brief = view.get("brief") if isinstance(view.get("brief"), dict) else None
        keyword = str(view.get("keyword") or text)
        yield from _search_and_answer(
            db,
            message=keyword,
            brief=brief,
            session_view=view,
            kb_name=name,
            mode=mode,
            verify=verify,
            clarify=clarify,
            registry=registry,
        )
        return

    plan = decide(text, brief=None, kb_name=name, clarify=clarify)
    probe = vague_food_query(text)
    if clarify != "skip" and (_wants_clarify(plan, clarify) or probe):
        view = start_session(
            db,
            keyword=text,
            conversation_id=None,
            domain_hint=None,
            skip=False,
            max_questions=FOOD_CLARIFY_LIMIT if probe else ASK_CLARIFY_LIMIT,
            ambiguities=plan.ambiguities,
        )
        yield from _clarify_events(view)
        return
    view = start_session(
        db,
        keyword=text,
        conversation_id=None,
        domain_hint=None,
        skip=True,
        max_questions=ASK_CLARIFY_LIMIT,
    )
    brief = view.get("brief") if isinstance(view.get("brief"), dict) else None
    yield from _search_and_answer(
        db,
        message=text,
        brief=brief,
        session_view=view,
        kb_name=name,
        mode=mode,
        verify=verify,
        clarify="skip",
        registry=registry,
    )


def decide(message: str, *, brief: dict[str, Any] | None, kb_name: str, clarify: str) -> Plan:
    context = _context(message, brief, kb_name, clarify)
    try:
        client = build_decision_client("deepseek")
    except DecisionUnavailable:
        client = None
    except Exception:
        logger.info("deepseek decision backend unavailable")
        client = None
    if client is not None and hasattr(client, "plan"):
        try:
            raw = client.plan(context)
            ambiguities = [] if context.get("has_brief") else list(raw.get("ambiguities") or [])
            need = "no" if context.get("has_brief") else str(raw["need_clarify"])
            sources = _route_sources(list(raw["sources"]), message, kb_name)
            return Plan(
                need_clarify=need,
                sources=sources,
                queries=list(raw["queries"]) or [message],
                backend="deepseek",
                ambiguities=ambiguities,
            )
        except DecisionUnavailable:
            logger.info("deepseek decision failed; using rule backend")
        except Exception:
            logger.info("deepseek decision failed; using rule backend")
    return _rule_plan(message, brief, kb_name, clarify, fallback="rule")


def _search_and_answer(
    db,
    *,
    message: str,
    brief: dict[str, Any] | None,
    session_view: dict[str, Any],
    kb_name: str,
    mode: str,
    verify: str,
    clarify: str,
    registry,
) -> Iterator[tuple[str, dict[str, Any]]]:
    import time

    from app.clarifier.session import attach_chat_turn
    from app.db.models import ClarifySession

    started = time.perf_counter()
    question = refined_question(message, brief)
    routed_clarify = "skip" if _clarified(brief) else clarify
    plan = decide(question, brief=brief, kb_name=kb_name, clarify=routed_clarify)
    sources = [name for name in SOURCE_ORDER if name in plan.sources]
    queries = [item.strip() for item in plan.queries if item and item.strip()]
    if question and question not in queries:
        queries.insert(0, question)
    if question == message and message.strip() and message.strip() not in queries:
        queries.append(message.strip())
    queries = queries[:3] or [question or message]
    yield "searching", {"sources": sources, "queries": queries, "backend": plan.backend, "fallback": plan.fallback}
    logger.info("ask search sources=%s backend=%s query_count=%s", ",".join(sources), plan.backend, len(queries))
    groups, notices = _retrieve(
        queries,
        sources,
        kb_name=kb_name,
        brief=brief,
        mode=mode,
        verify=verify,
        registry=registry,
    )
    for notice in notices:
        yield "notice", {"text": notice}
    from app.ask.secrets import secret_value

    missing_key = secret_value("deepseek") is None

    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        from app.decision.deepseek import complete_deepseek

        return complete_deepseek(messages)

    result = synthesize(
        question or queries[0],
        groups,
        registry=registry,
        complete=None if missing_key else complete,
        missing_key=missing_key,
    )
    if verify_enabled():
        local_hits = groups.get("local") or []
        freshness = apply_chat_freshness(message=message, verify=verify, hits=local_hits, registry=registry)
        for event in freshness.get("events") or []:
            yield "freshness", event
        note = freshness.get("note")
        if note and note not in result.text:
            result.text = f"{result.text}\n{note}"
    for citation in result.citations:
        yield "citation", citation
    for piece in _deltas(result.text):
        yield "delta", {"text": piece}
    row = db.get(ClarifySession, int(session_view["session_id"]))
    conversation_id = None
    if row is not None:
        conversation_id = attach_chat_turn(
            db,
            session_row=row,
            conversation_id=session_view.get("conversation_id"),
            user_text=message,
            assistant_text=result.text,
            mode=mode,
            sources=sources,
            verify=verify,
            clarify=clarify,
            citations=result.citations,
            provider=result.provider,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    yield "done", {
        "phase": "answer",
        "provider": result.provider,
        "model": result.model,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "conversation_id": conversation_id,
        "session_id": session_view.get("session_id"),
        "backend": plan.backend,
        "fallback": plan.fallback,
        "queries": queries,
        "sources": sources,
        "conflict": result.conflict,
    }


def _retrieve(
    queries: list[str],
    sources: list[str],
    *,
    kb_name: str,
    brief: dict[str, Any] | None,
    mode: str,
    verify: str,
    registry,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    del verify
    groups: dict[str, list[dict[str, Any]]] = {"local": [], "ima": [], "web": []}
    notices: list[str] = []
    if "local" in sources:
        groups["local"] = _search_local(queries, brief=brief, mode=mode, registry=registry)
    if "ima" in sources:
        hits, notice = _search_ima(queries[0], kb_name)
        groups["ima"] = hits
        if notice:
            notices.append(notice)
    if "web" in sources:
        found = search_web(queries[0])
        groups["web"] = enrich_web_hits(found.hits)
        if found.notice:
            notices.append(found.notice)
    return groups, notices


def _search_local(queries: list[str], *, brief: dict[str, Any] | None, mode: str, registry) -> list[dict[str, Any]]:
    from app.retrieval.multi_source import search_local_library

    seen: set[int] = set()
    hits: list[dict[str, Any]] = []
    for query in queries:
        try:
            found = search_local_library(query, mode=mode, sources=["local"], brief=brief, top_k=8, registry=registry)
        except Exception:
            logger.info("local search failed")
            continue
        for hit in found:
            chunk_id = hit.get("chunk_id")
            if isinstance(chunk_id, int) and chunk_id in seen:
                continue
            if isinstance(chunk_id, int):
                seen.add(chunk_id)
            hits.append(
                {
                    "title": hit.get("title") or hit.get("filename") or "本地文件",
                    "filename": hit.get("filename") or "",
                    "url": hit.get("url") or "",
                    "text": hit.get("text") or "",
                    "freshness_state": hit.get("freshness_state") or "unknown",
                    "platform": hit.get("platform") or "local_file",
                    "source_kind": hit.get("source_kind") or "local_file",
                    "source": "local",
                    "item_id": hit.get("item_id"),
                    "chunk_id": hit.get("chunk_id"),
                }
            )
    return hits


def _search_ima(query: str, kb_name: str) -> tuple[list[dict[str, Any]], str | None]:
    if not ima_configured():
        return [], MISSING_KEY_MESSAGE
    from app.connectors.ima_service import find_knowledge_base, open_ima_client, search

    try:
        with open_ima_client() as client:
            target = kb_name.strip() or query.strip()
            found = find_knowledge_base(client, target) if target else None
            notice = None
            kb_id = None
            if kb_name.strip() and found is None:
                notice = f"没有找到名为「{kb_name.strip()}」的 ima 知识库。"
            elif found is not None:
                kb_id = found["id"]
            result = search(client, query, knowledge_base_id=kb_id)
    except ImaError as exc:
        return [], exc.message
    hits: list[dict[str, Any]] = []
    for item in result.get("hits") or []:
        kind = str(item.get("kind") or "")
        if kind == "folder":
            continue
        # Subscription rows often have no body (get_media_info 220030) and
        # media_type 6 has no link. Do not copy the title into the excerpt.
        hits.append(
            {
                "title": item.get("title") or "ima 条目",
                "filename": "",
                "url": "",
                "text": item.get("snippet") or item.get("summary") or "",
                "freshness_state": "unknown",
                "platform": "ima",
                "source_kind": "connector",
                "source": "ima",
                "external_id": str(item.get("id") or ""),
                "media_type": item.get("media_type"),
                "parent_folder_id": item.get("parent_folder_id") or "",
                "ima_kind": kind,
            }
        )
    return hits, notice


def _advance(db, session_id: int, answers: list[dict[str, Any]], *, skip_rest: bool) -> dict[str, Any]:
    if skip_rest:
        return confirm_session(db, session_id, skip=True)
    for answer in answers:
        answer_session(
            db,
            session_id,
            question_id=str(answer.get("question_id") or ""),
            action=str(answer.get("action") or ""),
            value=answer.get("value"),
            park_reason=answer.get("park_reason"),
        )
    view = get_session_view(db, session_id)
    if view.get("state") == "ready_to_confirm":
        return confirm_session(db, session_id, skip=False)
    return view


def _clarify_events(view: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    question = view.get("question")
    yield "clarify_question", {
        "session_id": view.get("session_id"),
        "question": question,
        "tree_progress": view.get("tree_progress"),
        "keyword": view.get("keyword"),
    }
    yield "done", {"phase": "clarify", "session_id": view.get("session_id")}


def _wants_clarify(plan: Plan, clarify: str) -> bool:
    if clarify == "skip":
        return False
    if clarify == "force":
        return True
    return plan.need_clarify == "yes"


def _rule_plan(message: str, brief: dict[str, Any] | None, kb_name: str, clarify: str, *, fallback: str | None) -> Plan:
    client = RuleBasedDecisionClient()
    context = _context(message, brief, kb_name, clarify)
    try:
        answer = str(need_clarify(client, context).result)
    except Exception:
        answer = "no"
    try:
        channels = list(route_channels(client, context).result)
    except Exception:
        channels = ["local"]
    sources: list[str] = []
    for name in channels:
        if name == "connector":
            sources.append("ima")
        elif name in {"local", "web"} and name not in sources:
            sources.append(name)
    sources = _route_sources(sources, message, kb_name)
    queries = [message.strip()] if message.strip() else []
    scope = scope_text(brief)
    if scope:
        rewritten = f"{message.strip()} {scope}".strip()
        if rewritten and rewritten not in queries:
            queries.append(rewritten)
    if not queries:
        queries = [message or kb_name or "检索"]
    return Plan(answer, sources, queries[:3], "rule", fallback)


_LIFE = ("美食", "餐厅", "饭店", "餐馆", "小吃", "景点", "旅游", "旅行", "出行", "酒店", "攻略", "附近", "游玩", "去玩", "玩", "日料", "料理", "寿司")
_LIBRARY = ("笔记", "本地", "知识库", "ima", "收藏", "文件")


def life_web_only(text: str) -> bool:
    """Food, place, and travel questions are answered from the live web."""
    blob = text or ""
    if any(mark.lower() in blob.lower() for mark in _LIBRARY):
        return False
    return any(mark in blob for mark in _LIFE)


def refined_question(message: str, brief: dict[str, Any] | None) -> str:
    """Fold the chosen place and constraints into the question after clarification."""
    base = (message or "").strip()
    if not _clarified(brief) or not isinstance(brief, dict):
        return base
    bits: list[str] = []
    for key in ("scope", "constraints"):
        value = brief.get(key)
        if not isinstance(value, str):
            continue
        text = value.strip().rstrip("。")
        if not text or text in {"预算不限，口味不限"}:
            continue
        if text not in base and text not in bits:
            bits.append(text)
    if not bits:
        return base
    return " ".join([*bits, base] if base else bits).strip()


def _clarified(brief: dict[str, Any] | None) -> bool:
    if not isinstance(brief, dict):
        return False
    status = brief.get("status") or brief.get("brief")
    return status == "clarified"


def _route_sources(sources: list[str], message: str, kb_name: str) -> list[str]:
    ordered = _with_named_kb(sources, kb_name)
    if life_web_only(message) and not (kb_name or "").strip():
        return ["web"]
    return ordered


def _with_named_kb(sources: list[str], kb_name: str) -> list[str]:
    ordered = [name for name in SOURCE_ORDER if name in sources]
    if kb_name.strip() and "ima" not in ordered:
        ordered.append("ima")
        ordered = [name for name in SOURCE_ORDER if name in ordered]
    return ordered or ["local"]


def _context(message: str, brief: dict[str, Any] | None, kb_name: str, clarify: str) -> dict[str, Any]:
    status = None
    if isinstance(brief, dict):
        raw = brief.get("status") or brief.get("brief")
        status = raw if isinstance(raw, str) else None
    return {
        "text": message or "",
        "mode": "personal",
        "clarify": clarify or "auto",
        "verify": "auto",
        "has_brief": status == "clarified",
        "brief_status": status,
        "scope": scope_text(brief),
        "constraints": _constraint_text(brief),
        "kb_name": kb_name,
        "sources": None,
    }


def _constraint_text(brief: dict[str, Any] | None) -> str:
    if not isinstance(brief, dict):
        return ""
    value = brief.get("constraints")
    if not isinstance(value, str):
        return ""
    return value.strip()


def _deltas(text: str) -> list[str]:
    if not text:
        return [""]
    size = 48
    return [text[index : index + size] for index in range(0, len(text), size)]

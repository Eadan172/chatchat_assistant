"""Dedupe, rank, and cite evidence from web, ima, and the local library.

URL duplicates collapse after tracking parameters, a trailing slash, and the
fragment are removed. Near-duplicate excerpts use the existing embedder
(BGE-M3 when EMBED_PROVIDER=bge, otherwise the hashing embedder) and merge
when cosine similarity is above ASK_DEDUP_COSINE (default 0.9). Cross-source
order is reciprocal rank fusion, score = sum 1/(60+rank). Raw provider scores
are not compared.

DeepSeek sees only [n] and the excerpt. Citation numbers that were not given
are removed after generation, then the remaining numbers are rewritten to
1..k in the order they appear. The citation list keeps only those numbers.
Title-only ima rows are appended as 「相关条目」 and are not excerpts. When a
knowledge-base claim and a web claim disagree, the answer states that the two
sources differ and keeps both, with the M4 freshness label.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.ask.select import append_related, prepare_ima, strip_empty_points
from app.index.tokens import tokens
from app.retrieval.hybrid import RRF_K, rrf_fuse

CONFLICT_PHRASE = "两处来源说法不同"
NO_HITS = "没有检索到可引用的内容。"
GROUPED_NO_KEY = "未配置 DeepSeek，已按来源列出检索结果。"
GROUPED_FAILED = "DeepSeek 暂时不可用，已按来源列出检索结果。"

_CITE = re.compile(r"\[(\d+)\]")
_NUMBER = re.compile(r"\d{2,}")
_NEGATION = ("不是", "并非", "错误", "相反", "不能", "无法", "没有")
_SIDED = ("应以", "更可信", "正确的是", "采用网页", "采用知识库", "以网页为准", "以知识库为准")
_TRACKING = {
    "fbclid",
    "gclid",
    "gclsrc",
    "dclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "spm",
    "scm",
    "_ga",
    "yclid",
    "si",
    "ref",
    "ref_src",
}
_SOURCE_ORDER = ("local", "ima", "web")
_LABELS = {"web": "联网", "ima": "ima 知识库", "local": "本地库"}
_STATE_LABELS = {
    "fresh": "已核对",
    "stale": "可能过期",
    "unknown": "无法核实",
    "verifying": "查证中",
}

Complete = Callable[[list[dict[str, str]]], tuple[str, int, int]]


@dataclass
class Origin:
    source: str
    title: str
    url: str
    text: str
    filename: str
    freshness_state: str
    platform: str
    source_kind: str
    item_id: int | None = None
    chunk_id: int | None = None
    external_id: str = ""
    provider: str = ""


@dataclass
class Evidence:
    n: int
    text: str
    title: str
    url: str
    filename: str
    source: str
    source_kind: str
    platform: str
    freshness_state: str
    score: float
    item_id: int | None = None
    chunk_id: int | None = None
    origins: list[Origin] = field(default_factory=list)


@dataclass
class Synthesis:
    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    citations: list[dict[str, Any]]
    conflict: bool


def normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    port = parts.port
    netloc = host
    if port and not ((parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = parts.path or ""
    if path == "/":
        path = ""
    else:
        path = path.rstrip("/")
    query_pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING:
            continue
        query_pairs.append((key, value))
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query_pairs), ""))


def dedup_threshold() -> float:
    raw = (os.getenv("ASK_DEDUP_COSINE") or "0.9").strip()
    try:
        value = float(raw)
    except ValueError:
        return 0.9
    if value < 0.0 or value > 1.0:
        return 0.9
    return value


def fuse_hits(groups: dict[str, list[dict[str, Any]]], *, registry=None) -> list[Evidence]:
    flat: list[dict[str, Any]] = []
    for source in _SOURCE_ORDER:
        ordered = _order_within_source(list(groups.get(source) or []))
        for rank, hit in enumerate(ordered, start=1):
            copied = dict(hit)
            copied["source"] = source
            copied["rank"] = rank
            copied["url"] = _clean_url(str(hit.get("url") or ""))
            flat.append(copied)
    if not flat:
        return []
    vectors = _vectors(flat, registry)
    clusters = _clusters(flat, vectors)
    rankings: list[list[int]] = []
    for source in _SOURCE_ORDER:
        best: dict[int, int] = {}
        for cluster_id, members in enumerate(clusters):
            ranks = [int(flat[index]["rank"]) for index in members if flat[index]["source"] == source]
            if ranks:
                best[cluster_id] = min(ranks)
        if best:
            rankings.append(sorted(best, key=lambda cluster_id: (best[cluster_id], cluster_id)))
    fused = rrf_fuse(rankings, k=RRF_K)
    evidences: list[Evidence] = []
    for number, (cluster_id, score) in enumerate(fused, start=1):
        members = [flat[index] for index in clusters[cluster_id]]
        evidences.append(_evidence(number, members, score))
    return evidences


def claims_conflict(left: str, right: str) -> bool:
    a = (left or "").strip()
    b = (right or "").strip()
    if len(a) < 4 or len(b) < 4:
        return False
    compact_a = re.sub(r"\s+", "", a)
    compact_b = re.sub(r"\s+", "", b)
    if compact_a == compact_b or compact_a in compact_b or compact_b in compact_a:
        return False
    overlap = {token for token in set(tokens(a)) & set(tokens(b)) if len(token) >= 2}
    if not overlap:
        return False
    numbers_a = set(_NUMBER.findall(a))
    numbers_b = set(_NUMBER.findall(b))
    if numbers_a and numbers_b and numbers_a != numbers_b and not numbers_a <= numbers_b and not numbers_b <= numbers_a:
        return True
    neg_a = any(word in a for word in _NEGATION)
    neg_b = any(word in b for word in _NEGATION)
    return neg_a != neg_b


def find_conflicts(items: list[Evidence]) -> list[tuple[Evidence, Evidence]]:
    knowledge = [item for item in items if item.source in {"local", "ima"}]
    web = [item for item in items if item.source == "web"]
    pairs: list[tuple[Evidence, Evidence]] = []
    for left in knowledge:
        for right in web:
            if claims_conflict(left.text, right.text):
                pairs.append((left, right))
    return pairs


def guard_citations(text: str, valid: set[int]) -> str:
    def replace(match: re.Match[str]) -> str:
        number = int(match.group(1))
        return match.group(0) if number in valid else ""

    cleaned = _CITE.sub(replace, text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def align_citations(text: str, citations: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Keep numbers that appear in the answer, in that order, rewritten from 1."""
    by_n: dict[int, dict[str, Any]] = {}
    for item in citations:
        number = item.get("n")
        if isinstance(number, int) and number not in by_n:
            by_n[number] = item
    order: list[int] = []
    seen: set[int] = set()
    for match in _CITE.finditer(text or ""):
        number = int(match.group(1))
        if number in by_n and number not in seen:
            seen.add(number)
            order.append(number)
    mapping = {old: new for new, old in enumerate(order, start=1)}

    def replace(match: re.Match[str]) -> str:
        new = mapping.get(int(match.group(1)))
        return f"[{new}]" if new else ""

    cleaned = _CITE.sub(replace, text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    used: list[dict[str, Any]] = []
    for old in order:
        item = dict(by_n[old])
        item["n"] = mapping[old]
        used.append(item)
    return cleaned.strip(), used


def synthesize(
    query: str,
    groups: dict[str, list[dict[str, Any]]],
    *,
    registry=None,
    complete: Complete | None = None,
    missing_key: bool = False,
) -> Synthesis:
    ima_hits, related = prepare_ima(query, list(groups.get("ima") or []))
    prepared = dict(groups)
    prepared["ima"] = ima_hits
    extra = list(groups.get("related") or [])
    evidences = fuse_hits(prepared, registry=registry)
    citations = [public_citation(item) for item in evidences]
    conflicts = find_conflicts(evidences)
    tail = [*related, *extra]

    def finish(text: str, provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> Synthesis:
        limited = ensure_external_limits(query, text, evidences)
        cleaned, used = align_citations(strip_empty_points(limited), citations)
        return Synthesis(
            append_related(cleaned, tail),
            provider,
            model,
            prompt_tokens,
            completion_tokens,
            used,
            bool(conflicts),
        )

    if not evidences:
        return finish(NO_HITS, "offline", "grouped", 0, 0)
    if missing_key or complete is None:
        return finish(grouped_text(query, evidences, conflicts, reason=GROUPED_NO_KEY), "offline", "grouped", 0, 0)
    try:
        raw, prompt_tokens, completion_tokens = complete(_messages(query, evidences, bool(conflicts)))
    except Exception:
        return finish(grouped_text(query, evidences, conflicts, reason=GROUPED_FAILED), "offline", "grouped", 0, 0)
    text = _finalize(raw, evidences, conflicts)
    text = drop_unsupported_citations(text, citations)
    extra_prompt, extra_completion = 0, 0
    if not conflicts:
        text, extra_prompt, extra_completion = _regenerate_thin(
            query,
            text,
            evidences,
            citations,
            complete,
        )
    text = _join(text, format_rank_block(query, evidences))
    text = ensure_score_notice(query, text, evidences)
    return finish(
        text,
        "deepseek",
        "deepseek-chat",
        prompt_tokens + extra_prompt,
        completion_tokens + extra_completion,
    )


def grouped_text(query: str, items: list[Evidence], conflicts: list[tuple[Evidence, Evidence]], *, reason: str) -> str:
    del query
    lines: list[str] = []
    if conflicts:
        lines.append(conflict_text(conflicts, items))
    lines.append(reason)
    for source in _SOURCE_ORDER:
        rows = [item for item in items if item.source == source]
        if not rows:
            continue
        lines.append(_LABELS[source])
        for item in rows:
            lines.append(f"[{item.n}] {item.title}")
            excerpt = _excerpt(item.text, 300)
            if excerpt:
                lines.append(excerpt)
    return "\n".join(lines).strip()


def conflict_text(pairs: list[tuple[Evidence, Evidence]], items: list[Evidence]) -> str:
    lines = [f"{CONFLICT_PHRASE}。"]
    seen: set[int] = set()
    for knowledge, web in pairs:
        for item, label in ((knowledge, "知识库"), (web, "网页")):
            if item.n in seen:
                continue
            seen.add(item.n)
            lines.append(f"{label} [{item.n}] {_excerpt(item.text, 300)}")
    lines.append(freshness_clause(items))
    return "\n".join(lines)


def freshness_clause(items: list[Evidence]) -> str:
    parts: list[str] = []
    for item in items:
        label = _LABELS.get(item.source, "来源")
        state = _STATE_LABELS.get(item.freshness_state or "unknown", "无法核实")
        piece = f"{label}{state}"
        if piece not in parts:
            parts.append(piece)
    if not parts:
        return "（时效：无法核实）"
    return "（时效：" + "；".join(parts) + "）"


def public_citation(item: Evidence) -> dict[str, Any]:
    return {
        "n": item.n,
        "title": item.title,
        "filename": item.filename,
        "url": item.url,
        "text": item.text[:citation_excerpt_limit()],
        "source_kind": item.source_kind,
        "platform": item.platform,
        "freshness_state": item.freshness_state,
        "score": round(float(item.score), 6),
        "item_id": item.item_id,
        "chunk_id": item.chunk_id,
        "origins": [
            {
                "source": origin.source,
                "source_kind": origin.source_kind,
                "platform": origin.platform,
                "title": origin.title,
                "filename": origin.filename,
                "url": origin.url,
                "text": origin.text[:citation_excerpt_limit()],
                "freshness_state": origin.freshness_state,
                "external_id": origin.external_id,
            }
            for origin in item.origins
        ],
    }


_RANK_QUERY = re.compile(r"排名|榜单|排行|(?<![A-Za-z])top(?![A-Za-z])", re.IGNORECASE)
_LIST_TITLE = re.compile(r"TOP|排行榜|榜单|排名", re.IGNORECASE)
_EXPLICIT_RANK = re.compile(
    r"(?i)(?:top\s*([0-9]{1,2})|(?<!\d)([0-9]{1,2})\s*[.、．)、]|第\s*([0-9]{1,2}|[一二三四五六七八九十])\s*名)"
)
_SCORE = re.compile(r"评分|口碑|星级|\d+(?:\.\d+)?\s*分")
_BUDGET = re.compile(r"(?:人均|预算)\s*(\d+)\s*(?:元)?\s*(?:以?内|以下)")
_PRICE = re.compile(r"人均\s*(\d+)")
_KEEP_LINE = ("两处来源说法不同", "时效：", "超出预算", "来源未标名次", "价格未知", "没有实时评分", "没有评分", "没有口碑", "相关条目")
_CN_RANK = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
NO_LIVE_SCORE = "摘录里没有实时评分或口碑分数。"
DIANPING_NOTICE = "评分以大众点评 App 为准。"
DISTANCE_GAP = "来源没有写到地铁站的步行时间或距离，精确距离无法确认。"
DEFAULT_CITATION_CHARS = 4000
_CITE_ONLY = re.compile(r"^(?:\s*\[\d+\])+\s*$")
_DECIMAL = re.compile(r"\d+\.\d+")
_DISTANCE = re.compile(r"距[^。\n]{0,30}步行\s*\d+\s*分钟|步行\s*\d+\s*分钟")


def ranking_requested(query: str) -> bool:
    return bool(_RANK_QUERY.search(query or ""))


def citation_excerpt_limit() -> int:
    raw = os.getenv("ASK_CITATION_CHARS") or str(DEFAULT_CITATION_CHARS)
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_CITATION_CHARS
    return min(max(value, 800), 8000)


def citation_supports(claim: str, source: str) -> bool:
    """True when this sentence's facts, not just its topic, are in the excerpt."""
    body = _CITE.sub(" ", claim or "")
    claimed = explicit_rank_entries(body)
    if claimed:
        stated = explicit_rank_entries(source or "")
        if not stated:
            return False
        by_rank = {rank: name for rank, name, _price in stated}
        for rank, name, _price in claimed:
            source_name = by_rank.get(rank, "")
            if not source_name or (name not in source_name and source_name not in name):
                return False
        return True
    claim_tokens = {token for token in tokens(body) if len(token) >= 2}
    if not claim_tokens:
        return False
    source_tokens = {token for token in tokens(source or "") if len(token) >= 2}
    numbers = set(_NUMBER.findall(body))
    if numbers and not numbers <= set(_NUMBER.findall(source or "")):
        return False
    decimals = set(_DECIMAL.findall(body))
    if decimals and not decimals <= set(_DECIMAL.findall(source or "")):
        return False
    if _DISTANCE.search(body):
        claimed_digits = set(re.findall(r"\d+", body))
        if claimed_digits and not claimed_digits <= set(re.findall(r"\d+", source or "")):
            return False
    return len(claim_tokens & source_tokens) / float(len(claim_tokens)) >= 0.5


def drop_unsupported_citations(text: str, citations: list[dict[str, Any]]) -> str:
    """Drop [n] that the excerpt does not support, and drop the sentence if none remain."""
    by_n = {int(item["n"]): item for item in citations if isinstance(item.get("n"), int)}
    cleaned_lines: list[str] = []
    for line in (text or "").split("\n"):
        if any(mark in line for mark in _KEEP_LINE):
            cleaned_lines.append(line)
            continue
        kept: list[str] = []
        for sentence in _sentence_pieces(line):
            if not sentence.strip():
                continue
            had_cite = bool(_CITE.search(sentence))

            def replace(match: re.Match[str], claim: str = sentence) -> str:
                number = int(match.group(1))
                item = by_n.get(number)
                if item is None:
                    return match.group(0)
                if citation_supports(claim, str(item.get("text") or "")):
                    return match.group(0)
                return ""

            cleaned = _CITE.sub(replace, sentence)
            if had_cite and not _CITE.search(cleaned):
                continue
            kept.append(cleaned)
        if kept:
            cleaned_lines.append("".join(kept))
    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def ensure_score_notice(query: str, text: str, items: list[Evidence]) -> str:
    if not ranking_requested(query):
        return text
    if any(_SCORE.search(item.text or "") for item in items):
        return text
    if any(phrase in (text or "") for phrase in ("没有实时评分", "没有评分", "没有口碑")):
        return text
    base = (text or "").strip()
    if not base:
        return NO_LIVE_SCORE
    return f"{base}\n{NO_LIVE_SCORE}"


def ensure_external_limits(query: str, text: str, items: list[Evidence]) -> str:
    """Dianping scores and metro distances come only from excerpts. No map API."""
    cleaned = _drop_invented_scores(text, items) if wants_dianping(query) else text
    if wants_dianping(query) and DIANPING_NOTICE not in cleaned:
        cleaned = f"{cleaned}\n{DIANPING_NOTICE}".strip() if cleaned.strip() else DIANPING_NOTICE
    if wants_distance(query):
        sourced = False
        additions: list[str] = []
        for item in items:
            match = _DISTANCE.search(item.text or "")
            if match is None:
                continue
            sourced = True
            snippet = match.group(0).strip()
            if snippet and snippet not in cleaned:
                additions.append(f"{snippet}。[{item.n}]")
        if additions:
            cleaned = _join(cleaned, "\n".join(additions))
        if not sourced and "精确距离" not in cleaned:
            cleaned = f"{cleaned}\n{DISTANCE_GAP}".strip() if cleaned.strip() else DISTANCE_GAP
    return cleaned


def wants_dianping(query: str) -> bool:
    blob = query or ""
    return "大众点评" in blob or "点评评分" in blob or "打分" in blob


def wants_distance(query: str) -> bool:
    blob = query or ""
    return any(mark in blob for mark in ("地铁", "轨交", "步行", "距离"))


def _drop_invented_scores(text: str, items: list[Evidence]) -> str:
    known = set()
    for item in items:
        known.update(_DECIMAL.findall(item.text or ""))
    kept: list[str] = []
    for line in (text or "").split("\n"):
        if DIANPING_NOTICE in line or DISTANCE_GAP in line:
            kept.append(line)
            continue
        decimals = set(_DECIMAL.findall(line))
        if decimals and not decimals <= known:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _sentence_pieces(line: str) -> list[str]:
    parts = [part for part in re.split(r"(?<=[。！？])", line or "") if part]
    merged: list[str] = []
    for part in parts:
        if merged and _CITE_ONLY.match(part):
            merged[-1] += part
            continue
        merged.append(part)
    return merged


def explicit_rank_entries(text: str) -> list[tuple[int, str, int | None]]:
    """Ranks the excerpt actually writes, such as 「1.」「TOP1」 or 「第一名」."""
    matches = list(_EXPLICIT_RANK.finditer(text or ""))
    entries: list[tuple[int, str, int | None]] = []
    seen: set[int] = set()
    for index, match in enumerate(matches):
        rank = _rank_number(match.group(1) or match.group(2) or match.group(3) or "")
        if rank <= 0 or rank in seen:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text or "")
        span = (text or "")[match.end() : end]
        name = _entry_name(span)
        if len(name) < 2:
            continue
        seen.add(rank)
        entries.append((rank, name, _entry_price(span)))
    return entries


def format_rank_block(query: str, items: list[Evidence]) -> str:
    """Render list articles without turning excerpt order into a rank."""
    cap = budget_limit(query)
    lines: list[str] = []
    for item in items:
        if not _is_list_article(item, query):
            continue
        label = _list_label(item.title)
        ranked = explicit_rank_entries(item.text)
        if ranked:
            for rank, name, price in ranked:
                if cap is not None and price is not None and price > cap:
                    lines.append(f"超出预算未列入：{name}（人均{price}）。")
                    continue
                shown = _with_price_flag(name, price, cap)
                lines.append(f"据《{label}》榜单第{rank}名：{shown}。[{item.n}]")
            continue
        entries = _unranked_entries(item.text)
        if len(entries) < 2:
            continue
        kept: list[str] = []
        for name, price in entries:
            if cap is not None and price is not None and price > cap:
                lines.append(f"超出预算未列入：{name}（人均{price}）。")
                continue
            kept.append(_with_price_flag(name, price, cap))
        if kept:
            lines.append(f"{label}榜单收录了：{'、'.join(kept)}（来源未标名次）。[{item.n}]")
    return "\n".join(lines)


def budget_limit(text: str) -> int | None:
    match = _BUDGET.search(text or "")
    if match is None:
        return None
    return int(match.group(1))


def _messages(query: str, items: list[Evidence], conflict: bool) -> list[dict[str, str]]:
    system = (
        "你是问答助手。只根据给出的编号和摘录用中文回答。"
        "每条要点用 [n] 引用，只能使用下面出现过的编号，不要编造编号或摘录之外的事实。"
        "每一条要点的 [n] 只能标在直接写出该句事实的摘录上，不能标在仅仅话题相关的来源上。"
        "没有摘录支撑的句子不要写。"
    )
    if ranking_requested(query) or any(_is_list_article(item, query) for item in items):
        system += (
            "用户要的是排名、榜单或 top。"
            "只有摘录里出现「1.」「TOP1」「第一名」这类名次标记时，才写成「据X榜单第N名」，并引用那篇文章。"
            "标题像榜单但摘录没有名次时，写成「X榜单收录了：A、B、C（来源未标名次）」，不要把原文顺序当成名次。"
            "如果摘录里没有实时评分或口碑分数，要明确写出没有。"
            "用户给了人均预算时，超过预算的不要列入，摘录没写价格的要标价格未知。"
        )
    if wants_dianping(query):
        system += (
            "用户要大众点评分数。不能抓取大众点评，也不要编造分数。"
            "摘录里没有的分数不要写。必须写「评分以大众点评 App 为准」。"
        )
    if wants_distance(query):
        system += (
            "用户关心到地铁站的距离。不要调用地图。"
            "只有摘录写了「距某站步行X分钟」这类原句才能写距离并引用；"
            "摘录没有时要写明精确距离无法确认。"
        )
    if conflict:
        system += (
            "知识库和网页的说法不一致。回答必须写明「两处来源说法不同」，"
            "并列双方摘录，不要选择其中一方，不要写应以哪边为准。"
        )
    blocks = [f"[{item.n}] {_excerpt(item.text, prompt_excerpt_limit())}" for item in items]
    user = "【摘录】\n" + "\n\n".join(blocks) + f"\n\n【问题】{query}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _finalize(raw: str, items: list[Evidence], conflicts: list[tuple[Evidence, Evidence]]) -> str:
    valid = {item.n for item in items}
    cleaned = guard_citations(raw, valid)
    if not conflicts:
        return cleaned or grouped_text("", items, [], reason=GROUPED_FAILED)
    if CONFLICT_PHRASE in cleaned and not any(mark in cleaned for mark in _SIDED):
        if "时效：" not in cleaned:
            cleaned = f"{cleaned}\n{freshness_clause(items)}"
        return cleaned
    return conflict_text(conflicts, items)


def _evidence(number: int, members: list[dict[str, Any]], score: float) -> Evidence:
    ranked = sorted(members, key=lambda item: (int(item.get("rank") or 99), -len(str(item.get("text") or ""))))
    primary = max(members, key=lambda item: len(str(item.get("text") or "")))
    display = ranked[0]
    origins = [_origin(item) for item in members]
    return Evidence(
        n=number,
        text=str(primary.get("text") or ""),
        title=str(display.get("title") or primary.get("title") or "未命名"),
        url=str(display.get("url") or ""),
        filename=str(display.get("filename") or ""),
        source=str(display.get("source") or "local"),
        source_kind=str(display.get("source_kind") or _kind(str(display.get("source") or "local"))),
        platform=str(display.get("platform") or _platform(str(display.get("source") or "local"))),
        freshness_state=_rollup_state(members),
        score=float(score),
        item_id=_optional_int(display.get("item_id")),
        chunk_id=_optional_int(display.get("chunk_id")),
        origins=origins,
    )


def _origin(item: dict[str, Any]) -> Origin:
    source = str(item.get("source") or "local")
    return Origin(
        source=source,
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        text=str(item.get("text") or ""),
        filename=str(item.get("filename") or ""),
        freshness_state=str(item.get("freshness_state") or "unknown"),
        platform=str(item.get("platform") or _platform(source)),
        source_kind=str(item.get("source_kind") or _kind(source)),
        item_id=_optional_int(item.get("item_id")),
        chunk_id=_optional_int(item.get("chunk_id")),
        external_id=str(item.get("external_id") or ""),
        provider=str(item.get("provider") or ""),
    )


def _clusters(flat: list[dict[str, Any]], vectors: list[list[float] | None]) -> list[list[int]]:
    assigned = [-1] * len(flat)
    clusters: list[list[int]] = []
    threshold = dedup_threshold()
    for index in range(len(flat)):
        if assigned[index] >= 0:
            continue
        cluster_id = len(clusters)
        members = [index]
        assigned[index] = cluster_id
        cursor = 0
        while cursor < len(members):
            current = members[cursor]
            for other in range(len(flat)):
                if assigned[other] >= 0:
                    continue
                if _duplicate(flat[current], flat[other], vectors[current], vectors[other], threshold):
                    assigned[other] = cluster_id
                    members.append(other)
            cursor += 1
        clusters.append(members)
    return clusters


def _duplicate(
    left: dict[str, Any],
    right: dict[str, Any],
    left_vector: list[float] | None,
    right_vector: list[float] | None,
    threshold: float,
) -> bool:
    left_url = str(left.get("url") or "")
    right_url = str(right.get("url") or "")
    if left_url and right_url and left_url == right_url:
        return True
    if left_vector is None or right_vector is None:
        return False
    return _cosine(left_vector, right_vector) > threshold


def _vectors(flat: list[dict[str, Any]], registry) -> list[list[float] | None]:
    from app.index.embedder import embed_texts

    vectors: list[list[float] | None] = [None] * len(flat)
    pending: list[str] = []
    indexes: list[int] = []
    for index, hit in enumerate(flat):
        text = str(hit.get("text") or "").strip()
        if len(text) < 8:
            continue
        pending.append(text)
        indexes.append(index)
    if not pending:
        return vectors
    try:
        embedded = embed_texts(pending, registry)
    except Exception:
        return vectors
    for index, vector in zip(indexes, embedded):
        vectors[index] = vector
    return vectors


def _order_within_source(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = list(enumerate(hits))

    def sort_key(pair: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
        position = pair[1].get("position")
        if isinstance(position, int):
            return (0, position, pair[0])
        return (1, pair[0], pair[0])

    indexed.sort(key=sort_key)
    return [hit for _index, hit in indexed]


def _clean_url(url: str) -> str:
    normalized = normalize_url(url)
    return normalized or url.strip()


def _rollup_state(members: list[dict[str, Any]]) -> str:
    states = {str(item.get("freshness_state") or "unknown") for item in members}
    if "stale" in states:
        return "stale"
    if "unknown" in states or "" in states:
        return "unknown"
    if "verifying" in states:
        return "verifying"
    if "fresh" in states:
        return "fresh"
    return "unknown"


def prompt_excerpt_limit() -> int:
    raw = (os.getenv("ASK_EXCERPT_CHARS") or "1200").strip()
    try:
        value = int(raw)
    except ValueError:
        return 1200
    return min(max(value, 200), 4000)


def _regenerate_thin(
    query: str,
    text: str,
    items: list[Evidence],
    citations: list[dict[str, Any]],
    complete: Complete,
) -> tuple[str, int, int]:
    if not items or not _needs_regen(text):
        return text, 0, 0
    cited = {int(match) for match in _CITE.findall(text or "")}
    chosen = [item for item in items if item.n in cited] or list(items)
    try:
        raw, prompt_tokens, completion_tokens = complete(_regen_messages(query, chosen))
    except Exception:
        return text, 0, 0
    rewritten = drop_unsupported_citations(_finalize(raw, chosen, False), citations)
    if _CITE.search(rewritten):
        return rewritten, prompt_tokens, completion_tokens
    return text, prompt_tokens, completion_tokens


def _regen_messages(query: str, items: list[Evidence]) -> list[dict[str, str]]:
    messages = _messages(query, items, False)
    messages[0]["content"] += "上一段删掉没有出处的句子后只剩一两句。请只根据这些摘录重写成连贯的短段，每句都要有对得上的 [n]。不要编造名次。"
    return messages


def _needs_regen(text: str) -> bool:
    return len(_cited_sentences(text)) <= 2


def _cited_sentences(text: str) -> list[str]:
    found: list[str] = []
    for line in (text or "").split("\n"):
        if any(mark in line for mark in _KEEP_LINE):
            continue
        for part in re.split(r"[。！？]", line):
            part = part.strip()
            if part and _CITE.search(part):
                found.append(part)
    return found


def _is_list_article(item: Evidence, query: str) -> bool:
    if _LIST_TITLE.search(item.title or ""):
        return True
    if ranking_requested(query) and (explicit_rank_entries(item.text) or "·" in (item.text or "")):
        return True
    return False


def _list_label(title: str) -> str:
    label = re.sub(r"\s+", "", title or "").strip("《》")
    return label or "该来源"


def _rank_number(raw: str) -> int:
    text = (raw or "").strip()
    if text.isdigit():
        return int(text)
    return _CN_RANK.get(text, 0)


def _unranked_entries(text: str) -> list[tuple[str, int | None]]:
    parts = [part.strip() for part in re.split(r"\s*[·•|]\s*", text or "") if part.strip()]
    if len(parts) < 2:
        return []
    entries: list[tuple[str, int | None]] = []
    seen: set[str] = set()
    for part in parts:
        if explicit_rank_entries(part):
            continue
        name = _entry_name(part)
        if len(name) < 2 or name in seen:
            continue
        seen.add(name)
        entries.append((name, _entry_price(part)))
    return entries


def _entry_name(span: str) -> str:
    piece = re.split(r"[。；;\n]", span or "")[0]
    if "：" in piece:
        piece = piece.split("：")[-1]
    if ":" in piece:
        piece = piece.split(":")[-1]
    piece = _PRICE.sub("", piece)
    piece = re.sub(r"\d+\s*元", "", piece)
    piece = re.sub(r"\s+", "", piece).strip(" ，,、:：·•-—")
    return piece[:24]


def _entry_price(span: str) -> int | None:
    match = _PRICE.search(span or "")
    if match is None:
        return None
    return int(match.group(1))


def _with_price_flag(name: str, price: int | None, cap: int | None) -> str:
    if cap is not None and price is None:
        return f"{name}（价格未知）"
    return name


def _join(left: str, right: str) -> str:
    parts = [part.strip() for part in (left, right) if part and part.strip()]
    return "\n".join(parts)


def _excerpt(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "…"


def _cosine(left: list[float], right: list[float]) -> float:
    size = min(len(left), len(right))
    if size == 0:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for index in range(size):
        dot += left[index] * right[index]
        left_norm += left[index] * left[index]
        right_norm += right[index] * right[index]
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm ** 0.5 * right_norm ** 0.5)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _kind(source: str) -> str:
    if source == "web":
        return "web"
    if source == "ima":
        return "connector"
    return "local_file"


def _platform(source: str) -> str:
    if source == "web":
        return "web"
    if source == "ima":
        return "ima"
    return "local_file"

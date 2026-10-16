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
        cleaned, used = align_citations(strip_empty_points(text), citations)
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
    text = ensure_score_notice(query, text, evidences)
    return finish(text, "deepseek", "deepseek-chat", prompt_tokens, completion_tokens)


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
        "text": item.text[:800],
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
                "text": origin.text[:400],
                "freshness_state": origin.freshness_state,
                "external_id": origin.external_id,
            }
            for origin in item.origins
        ],
    }


_RANK_QUERY = re.compile(r"排名|榜单|排行|(?<![A-Za-z])top(?![A-Za-z])", re.IGNORECASE)
_RANK_ITEM = re.compile(
    r"(?:(?<!\d)\d{1,2}\s*[.、．]|第[一二三四五六七八九十]+[名位]?)\s*([^\d。\n;；.]{2,40})"
)
_SCORE = re.compile(r"评分|口碑|星级|\d+(?:\.\d+)?\s*分")
NO_LIVE_SCORE = "摘录里没有实时评分或口碑分数。"


def ranking_requested(query: str) -> bool:
    return bool(_RANK_QUERY.search(query or ""))


def citation_supports(claim: str, source: str) -> bool:
    """True when this sentence's facts, not just its topic, are in the excerpt."""
    body = _CITE.sub(" ", claim or "")
    ranked = _ranked_items(body)
    if len(ranked) >= 2:
        return _order_preserved(ranked, source)
    claim_tokens = {token for token in tokens(body) if len(token) >= 2}
    if not claim_tokens:
        return False
    source_tokens = {token for token in tokens(source or "") if len(token) >= 2}
    numbers = set(_NUMBER.findall(body))
    if numbers and not numbers <= set(_NUMBER.findall(source or "")):
        return False
    return len(claim_tokens & source_tokens) / float(len(claim_tokens)) >= 0.5


def drop_unsupported_citations(text: str, citations: list[dict[str, Any]]) -> str:
    """Remove [n] from a line when that source does not state the line."""
    by_n = {int(item["n"]): item for item in citations if isinstance(item.get("n"), int)}
    cleaned_lines: list[str] = []
    for line in (text or "").split("\n"):
        def replace(match: re.Match[str], claim: str = line) -> str:
            number = int(match.group(1))
            item = by_n.get(number)
            if item is None:
                return match.group(0)
            if citation_supports(claim, str(item.get("text") or "")):
                return match.group(0)
            return ""

        cleaned_lines.append(_CITE.sub(replace, line))
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


def _ranked_items(text: str) -> list[str]:
    found: list[str] = []
    for match in _RANK_ITEM.finditer(text or ""):
        name = re.sub(r"\s+", "", match.group(1)).strip("，,、 ")
        if len(name) >= 2 and name not in found:
            found.append(name)
    return found


def _order_preserved(items: list[str], source: str) -> bool:
    compact = re.sub(r"\s+", "", source or "")
    cursor = 0
    for item in items:
        pos = compact.find(item, cursor)
        if pos < 0:
            return False
        cursor = pos + len(item)
    return True


def _messages(query: str, items: list[Evidence], conflict: bool) -> list[dict[str, str]]:
    system = (
        "你是问答助手。只根据给出的编号和摘录用中文回答。"
        "每条要点用 [n] 引用，只能使用下面出现过的编号，不要编造编号或摘录之外的事实。"
        "每一条要点的 [n] 只能标在直接写出该句事实的摘录上，不能标在仅仅话题相关的来源上。"
    )
    if ranking_requested(query):
        system += (
            "用户要的是排名、榜单或 top。"
            "摘录里如果已经有榜单，按原顺序原样写出，不要自己重排，也不要编造名次或名称。"
            "每一份榜单只引用它出自的那篇文章的编号。"
            "如果摘录里没有实时评分或口碑分数，要明确写出没有。"
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

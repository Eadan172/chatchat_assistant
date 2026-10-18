"""Content-type detection and writing-style catalog.

A style is picked from the user's locale plus a rule-based content classifier:
life, knowledge, technical, design, or general. The directive is injected into
the answer prompt; the offline extractive path only applies light formatting
when a style is requested explicitly.
"""

from __future__ import annotations

import re
from typing import Iterable

from app.i18n import normalize_locale

DOMAINS = ("life", "knowledge", "technical", "design", "general")
AUTO = "auto"

STYLES: list[dict] = [
    {
        "id": "life-xiaohongshu",
        "domain": "life",
        "locale": "zh",
        "label_zh": "小红书笔记体",
        "label_en": "Xiaohongshu note",
        "summary_zh": "短句、要点化、有情绪但不浮夸",
        "summary_en": "Short lines, scannable bullets, warm but not salesy",
        "directive": (
            "用中文小红书笔记口吻写作：开头一句给出结论或亮点，正文 3-5 个短段落或要点，"
            "可用一两个 emoji 作小标题，句子短、语气自然，不要夸张营销词，保留 [n] 引用。"
        ),
    },
    {
        "id": "life-social",
        "domain": "life",
        "locale": "en",
        "label_zh": "海外社媒短文",
        "label_en": "Social caption",
        "summary_zh": "Hook + bullets，适合社媒/流媒体",
        "summary_en": "Hook line plus short bullets for social feeds",
        "directive": (
            "Write in English like a short social or streaming caption: one hook line, "
            "3-5 short bullets, conversational tone, no hype, keep every [n] citation."
        ),
    },
    {
        "id": "knowledge-zhihu",
        "domain": "knowledge",
        "locale": "zh",
        "label_zh": "知乎回答体",
        "label_en": "Zhihu answer",
        "summary_zh": "先结论、再分点论证",
        "summary_en": "Conclusion first, then structured reasoning",
        "directive": (
            "用中文知乎回答口吻：先给结论，再分点论证，适度口语但保持准确，"
            "可用「先说结论」这类结构，保留 [n] 引用。"
        ),
    },
    {
        "id": "knowledge-textbook",
        "domain": "knowledge",
        "locale": "zh",
        "label_zh": "教科书体",
        "label_en": "Textbook",
        "summary_zh": "定义—原理—例子—小结",
        "summary_en": "Definition, principle, example, recap",
        "directive": (
            "用中文教科书文风：按「定义—原理—例子—小结」组织，术语准确、表述客观，"
            "避免口语化感叹，保留 [n] 引用。"
        ),
    },
    {
        "id": "knowledge-report",
        "domain": "knowledge",
        "locale": "zh",
        "label_zh": "工作调研报告体",
        "label_en": "Research report",
        "summary_zh": "背景、发现、证据、结论、建议",
        "summary_en": "Background, findings, evidence, conclusion, action",
        "directive": (
            "用中文工作调研报告文风：分「背景 / 主要发现 / 证据 / 结论 / 建议」小节，"
            "条目清晰、措辞克制，保留 [n] 引用。"
        ),
    },
    {
        "id": "knowledge-explainer",
        "domain": "knowledge",
        "locale": "en",
        "label_zh": "英文科普",
        "label_en": "Explainer",
        "summary_zh": "结论先行、类比清晰",
        "summary_en": "Answer first, plain-language analogies",
        "directive": (
            "Answer in English as a clear explainer: state the conclusion first, then "
            "explain with plain-language analogies and short paragraphs, keep [n] citations."
        ),
    },
    {
        "id": "knowledge-textbook-en",
        "domain": "knowledge",
        "locale": "en",
        "label_zh": "英文教科书",
        "label_en": "Textbook (EN)",
        "summary_zh": "定义、原理、示例、小结",
        "summary_en": "Definition, principle, examples, recap",
        "directive": (
            "Answer in English in textbook style: definition, principle, worked example, "
            "short recap. Precise terminology, neutral tone, keep [n] citations."
        ),
    },
    {
        "id": "technical-manual",
        "domain": "technical",
        "locale": "zh",
        "label_zh": "操作说明书体",
        "label_en": "Operating manual",
        "summary_zh": "前置条件、编号步骤、预期结果",
        "summary_en": "Prerequisites, numbered steps, expected result",
        "directive": (
            "用中文操作说明书文风：写清前置条件，步骤用 1./2./3. 编号，每步给出预期结果，"
            "命令、参数、文件名用行内代码，末尾列常见问题，保留 [n] 引用。"
        ),
    },
    {
        "id": "technical-design",
        "domain": "technical",
        "locale": "zh",
        "label_zh": "开发技术方案体",
        "label_en": "Technical design doc",
        "summary_zh": "目标、方案、接口、权衡、风险",
        "summary_en": "Goals, design, interfaces, trade-offs, risks",
        "directive": (
            "用中文开发技术方案文风：分「目标 / 方案 / 接口与数据 / 权衡 / 风险」小节，"
            "给出可执行的实现要点，保留 [n] 引用。"
        ),
    },
    {
        "id": "technical-manual-en",
        "domain": "technical",
        "locale": "en",
        "label_zh": "英文操作手册",
        "label_en": "Operating manual (EN)",
        "summary_zh": "Prerequisites, steps, verification",
        "summary_en": "Prerequisites, numbered steps, verification",
        "directive": (
            "Answer in English as an operating manual: prerequisites, numbered steps with "
            "expected results, inline code for commands and paths, troubleshooting at the end. "
            "Keep [n] citations."
        ),
    },
    {
        "id": "technical-design-en",
        "domain": "technical",
        "locale": "en",
        "label_zh": "英文技术方案",
        "label_en": "Technical design doc (EN)",
        "summary_zh": "Goals, design, interfaces, risks",
        "summary_en": "Goals, design, interfaces, risks",
        "directive": (
            "Answer in English as a technical design document: goals, proposed design, "
            "interfaces and data, trade-offs, risks. Keep [n] citations."
        ),
    },
    {
        "id": "design-professional",
        "domain": "design",
        "locale": "zh",
        "label_zh": "专业设计说明体",
        "label_en": "Design rationale",
        "summary_zh": "目标受众、策略、视觉与交互要点",
        "summary_en": "Audience, strategy, visual and interaction notes",
        "directive": (
            "用中文专业设计说明文风：说明目标与受众、设计策略、视觉与交互要点、"
            "可用性考量，术语专业但不堆砌，保留 [n] 引用。"
        ),
    },
    {
        "id": "design-professional-en",
        "domain": "design",
        "locale": "en",
        "label_zh": "英文设计说明",
        "label_en": "Design rationale (EN)",
        "summary_zh": "Audience, strategy, craft notes",
        "summary_en": "Audience, strategy, craft notes",
        "directive": (
            "Answer in English as a professional design rationale: audience and goals, "
            "design strategy, visual and interaction details, usability considerations. "
            "Keep [n] citations."
        ),
    },
    {
        "id": "general-plain",
        "domain": "general",
        "locale": "*",
        "label_zh": "通用简洁体",
        "label_en": "Plain answer",
        "summary_zh": "直接回答，重点先行",
        "summary_en": "Direct answer, key points first",
        "directive": "直接回答问题，重点先行，段落简短，保留 [n] 引用。",
    },
]

_BY_ID = {style["id"]: style for style in STYLES}

_KEYWORDS: dict[str, dict[str, Iterable[str]]] = {
    "life": {
        "zh": (
            "餐厅", "美食", "好吃", "探店", "附近", "推荐", "旅游", "出游", "攻略", "景点",
            "民宿", "穿搭", "护肤", "菜谱", "做法", "减肥", "咖啡", "周末", "露营", "宠物",
        ),
        "en": (
            "restaurant", "food", "nearby", "travel", "trip", "recipe", "outfit", "skincare",
            "weekend", "cafe", "hotel", "visit", "eat",
        ),
    },
    "technical": {
        "zh": (
            "代码", "接口", "api", "部署", "安装", "报错", "异常", "配置", "依赖", "数据库",
            "函数", "脚本", "git", "docker", "性能", "调试", "版本", "框架", "日志", "编译",
            "服务器", "命令行", "sdk",
        ),
        "en": (
            "api", "deploy", "install", "error", "exception", "config", "dependency",
            "database", "function", "script", "git", "docker", "performance", "debug",
            "compile", "server", "cli", "sdk",
        ),
    },
    "design": {
        "zh": ("设计", "配色", "版式", "字体", "交互", "视觉", "ui", "ux", "logo", "海报", "品牌", "原型", "组件", "间距"),
        "en": ("design", "palette", "layout", "typography", "interaction", "visual", "ui", "ux", "logo", "poster", "brand", "prototype"),
    },
    "knowledge": {
        "zh": ("是什么", "为什么", "原理", "区别", "历史", "概念", "定义", "影响", "原因", "如何理解", "科普", "机制", "对比"),
        "en": ("what is", "why", "principle", "difference", "history", "concept", "definition", "impact", "mechanism", "compare"),
    },
}


def list_styles(locale: str = "zh") -> list[dict]:
    code = normalize_locale(locale)
    return [public(style, code) for style in STYLES]


def public(style: dict, locale: str = "zh") -> dict:
    code = normalize_locale(locale)
    return {
        "id": style["id"],
        "domain": style["domain"],
        "locale": style["locale"],
        "label": style["label_en"] if code == "en" else style["label_zh"],
        "summary": style["summary_en"] if code == "en" else style["summary_zh"],
    }


def classify(text: str, locale: str = "zh") -> str:
    """Rule-based domain detection. Returns one of DOMAINS."""
    source = (text or "").strip().lower()
    if not source:
        return "general"
    code = normalize_locale(locale)
    scores: dict[str, int] = {domain: 0 for domain in DOMAINS}
    for domain, words in _KEYWORDS.items():
        for word in words.get(code, ()) + tuple(words.get("en", ()) if code == "zh" else ()):
            if word and word.lower() in source:
                scores[domain] += 1
    best = max(scores.values())
    if best <= 0:
        return "general"
    # Ambiguous tie: prefer the domain whose matched keyword appears first.
    winners = [domain for domain, score in scores.items() if score == best]
    if len(winners) == 1:
        return winners[0]
    earliest = min(
        (source.find(word), domain)
        for domain in winners
        for word in _KEYWORDS[domain].get(code, ())
        if word and word.lower() in source
    )
    return earliest[1]


def resolve(style_id: str | None, text: str, locale: str = "zh") -> dict:
    """Resolve the effective style. Never raises for unknown ids; they fall back to auto."""
    code = normalize_locale(locale)
    wanted = (style_id or AUTO).strip()
    if wanted and wanted != AUTO and wanted in _BY_ID:
        return _BY_ID[wanted]
    domain = classify(text, code)
    for style in STYLES:
        if style["domain"] == domain and style["locale"] in (code, "*"):
            return style
    return _BY_ID["general-plain"]


def directive(style: dict) -> str:
    return str(style.get("directive") or "")


def format_offline(text: str, style: dict, *, requested: bool) -> str:
    """Light structure for the extractive path.

    Auto-detected styles leave the text untouched (no model rewrites happen
    offline); an explicitly requested manual style gets numbered steps.
    """
    if not text or not requested:
        return text
    if style["id"] not in ("technical-manual", "technical-manual-en"):
        return text
    parts = [piece.strip() for piece in re.split(r"(?<=[。！？.!?])\s*", text) if piece.strip()]
    if len(parts) < 3:
        return text
    return "\n".join(f"{index}. {piece}" for index, piece in enumerate(parts, start=1))

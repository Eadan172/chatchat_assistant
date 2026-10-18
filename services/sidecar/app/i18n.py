"""Locale helpers and the backend message catalog.

The desktop UI owns its own dictionary; this module covers strings the backend
generates itself (stub answers, not-found text, stream errors) plus the
language switch used by prompts.
"""

from __future__ import annotations

from typing import Mapping

LOCALES = ("zh", "en")
DEFAULT_LOCALE = "zh"


def normalize_locale(value: str | None) -> str:
    raw = (value or "").strip().lower().replace("_", "-")
    if raw.startswith("en"):
        return "en"
    return DEFAULT_LOCALE


def locale_from(explicit: str | None, headers: Mapping[str, str] | None = None) -> str:
    if explicit and explicit.strip():
        return normalize_locale(explicit)
    if headers:
        header = headers.get("x-locale") or headers.get("accept-language") or ""
        if header:
            return normalize_locale(header)
    return DEFAULT_LOCALE


MESSAGES: dict[str, dict[str, str]] = {
    "chat.stub": {
        "zh": "（M0 stub）sidecar 已连通，问答主路径尚未实现。",
        "en": "(M0 stub) The sidecar is connected; the main Q&A path is not wired yet.",
    },
    "chat.not_found": {
        "zh": "当前知识库中没有找到相关内容。",
        "en": "No relevant content was found in the current knowledge base.",
    },
    "chat.scoped_missing": {
        "zh": "按澄清范围「{scope}」检索后，{message}",
        "en": "After searching within the clarified scope “{scope}”: {message}",
    },
    "chat.scoped_prefix": {
        "zh": "按澄清范围「{scope}」。",
        "en": "Within the clarified scope “{scope}”.",
    },
    "chat.extractive_tail": {
        "zh": "以上内容来自知识库原文。",
        "en": "The content above comes from the knowledge base source text.",
    },
    "chat.brief_ack": {
        "zh": "已接收澄清结果（{status}）。检索与生成仍是占位，brief 已写入会话。",
        "en": "Clarification result received ({status}). Retrieval and generation are still placeholders; the brief was saved.",
    },
    "ask.empty_question": {"zh": "请填写问题", "en": "Please enter a question"},
    "ask.failed": {
        "zh": "问答没有完成，请稍后再试。",
        "en": "The answer could not be completed. Please try again.",
    },
    "import.not_implemented": {
        "zh": "「{name}」接入通道本期仅提供接口占位，尚未实现。",
        "en": "The “{name}” channel is an interface placeholder only in this release.",
    },
    "import.missing_config": {
        "zh": "「{name}」尚未配置，缺少：{fields}。",
        "en": "“{name}” is not configured yet. Missing: {fields}.",
    },
    "memory.not_found": {"zh": "未找到该对话", "en": "Conversation not found"},
    "memory.folder_not_found": {"zh": "未找到该文件夹", "en": "Folder not found"},
    "memory.project_not_found": {"zh": "未找到该项目", "en": "Project not found"},
    "memory.bad_import": {
        "zh": "无法识别该对话文件格式。",
        "en": "Unrecognized conversation file format.",
    },
    "memory.empty_import": {"zh": "文件里没有可导入的消息。", "en": "The file has no importable messages."},
    "memory.title": {"zh": "新对话", "en": "New conversation"},
}


def t(key: str, locale: str = DEFAULT_LOCALE, **values: object) -> str:
    table = MESSAGES.get(key)
    if not table:
        return key
    text = table.get(normalize_locale(locale)) or table[DEFAULT_LOCALE]
    try:
        return text.format(**values)
    except (KeyError, IndexError):
        return text

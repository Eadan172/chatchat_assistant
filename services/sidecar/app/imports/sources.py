"""Concrete import channels: local upload, the existing ima panel, and placeholders."""

from __future__ import annotations

import os
from pathlib import Path

from app.imports.protocol import ContainerRef, ImportDocument, ImportSource, SourceDescriptor


class LocalUploadSource(ImportSource):
    """The built-in upload / by-path channel. Execution stays on /library/import."""

    descriptor = SourceDescriptor(
        id="local",
        name_zh="本地上传",
        name_en="Local upload",
        kind="local",
        implemented=True,
        configured=True,
        capabilities=["upload", "path", "md", "txt", "pdf", "docx"],
        ui="unified",
        note_zh="支持 md、txt、pdf、docx，可上传或按路径导入。",
        note_en="Supports md, txt, pdf, docx — upload a file or import by path.",
    )

    def browse(self, parent_id: str | None = None, locale: str = "zh") -> list[ContainerRef]:
        return []


class ImaPanelSource(ImportSource):
    """Already implemented, but driven by the dedicated ima panel in the desktop UI."""

    descriptor = SourceDescriptor(
        id="ima",
        name_zh="腾讯 ima 知识库",
        name_en="Tencent ima",
        kind="connector",
        implemented=True,
        configured=False,
        capabilities=["list", "search", "import"],
        config_fields=[
            {"key": "IMA_CLIENT_ID", "label_zh": "Client ID", "label_en": "Client ID", "secret": False},
            {"key": "IMA_API_KEY", "label_zh": "API Key", "label_en": "API Key", "secret": True},
        ],
        docs_url="https://ima.qq.com/agent-interface",
        ui="panel",
        note_zh="使用 ima 面板连接；密钥在设置里集中管理。",
        note_en="Connect from the ima panel; credentials live in Settings.",
    )

    def configured_now(self) -> bool:
        if (os.getenv("IMA_CLIENT_ID") or "").strip() and (os.getenv("IMA_API_KEY") or "").strip():
            return True
        try:
            from app.connectors.ima_secrets import configured

            return bool(configured())
        except Exception:
            return False


class PlaceholderSource(ImportSource):
    """Declared channel. Contract is live, transport is not."""

    def __init__(
        self,
        *,
        source_id: str,
        name_zh: str,
        name_en: str,
        config_fields: list[dict],
        docs_url: str,
        capabilities: list[str] | None = None,
        note_zh: str,
        note_en: str,
        watcher,
    ) -> None:
        self._watcher = watcher
        self.descriptor = SourceDescriptor(
            id=source_id,
            name_zh=name_zh,
            name_en=name_en,
            kind="placeholder",
            implemented=False,
            configured=False,
            capabilities=capabilities or ["list", "import"],
            config_fields=config_fields,
            docs_url=docs_url,
            ui="unified",
            note_zh=note_zh,
            note_en=note_en,
        )

    def refresh(self) -> None:
        # Credentials may be present, but the transport is still not wired.
        self.descriptor.configured = bool(self._watcher()) and self.descriptor.implemented


def _env_present(names: list[str]) -> bool:
    return all((os.getenv(name) or "").strip() for name in names)


def obsidian_source() -> PlaceholderSource:
    return PlaceholderSource(
        source_id="obsidian",
        name_zh="Obsidian 仓库",
        name_en="Obsidian vault",
        config_fields=[
            {"key": "OBSIDIAN_VAULT_PATH", "label_zh": "Vault 路径", "label_en": "Vault path", "secret": False},
        ],
        docs_url="https://help.obsidian.md/",
        capabilities=["folder-scan", "markdown", "wiki-links"],
        note_zh="接口已就绪：按 Vault 目录扫描 Markdown、解析双链。本期未接入。",
        note_en="Contract ready: scan a vault folder and parse Markdown/wiki-links. Not wired yet.",
        watcher=lambda: _env_present(["OBSIDIAN_VAULT_PATH"]),
    )


def feishu_source() -> PlaceholderSource:
    return PlaceholderSource(
        source_id="feishu",
        name_zh="飞书云文档",
        name_en="Feishu Docs",
        config_fields=[
            {"key": "FEISHU_APP_ID", "label_zh": "App ID", "label_en": "App ID", "secret": False},
            {"key": "FEISHU_APP_SECRET", "label_zh": "App Secret", "label_en": "App Secret", "secret": True},
        ],
        docs_url="https://open.feishu.cn/document",
        capabilities=["list", "import", "docx", "wiki"],
        note_zh="接口已就绪：tenant_access_token 鉴权后列出知识空间与文档。本期未接入。",
        note_en="Contract ready: list wiki spaces and docs after tenant_access_token auth. Not wired yet.",
        watcher=lambda: _env_present(["FEISHU_APP_ID", "FEISHU_APP_SECRET"]),
    )


def tencent_docs_source() -> PlaceholderSource:
    return PlaceholderSource(
        source_id="tencent_docs",
        name_zh="腾讯文档",
        name_en="Tencent Docs",
        config_fields=[
            {"key": "TENCENT_DOCS_CLIENT_ID", "label_zh": "Client ID", "label_en": "Client ID", "secret": False},
            {"key": "TENCENT_DOCS_ACCESS_TOKEN", "label_zh": "Access Token", "label_en": "Access Token", "secret": True},
        ],
        docs_url="https://docs.qq.com/open/document/",
        capabilities=["list", "import", "doc", "sheet"],
        note_zh="接口已就绪：OAuth 后按文件夹列出文档。本期未接入。",
        note_en="Contract ready: list documents per folder after OAuth. Not wired yet.",
        watcher=lambda: _env_present(["TENCENT_DOCS_CLIENT_ID", "TENCENT_DOCS_ACCESS_TOKEN"]),
    )


def notion_source() -> PlaceholderSource:
    return PlaceholderSource(
        source_id="notion",
        name_zh="Notion",
        name_en="Notion",
        config_fields=[
            {"key": "NOTION_TOKEN", "label_zh": "Integration Token", "label_en": "Integration Token", "secret": True},
        ],
        docs_url="https://developers.notion.com/",
        capabilities=["list", "import", "database", "page"],
        note_zh="接口已就绪：按 database/page 递归拉取区块。本期未接入。",
        note_en="Contract ready: walk databases and pages block by block. Not wired yet.",
        watcher=lambda: _env_present(["NOTION_TOKEN"]),
    )


def local_folder_paths() -> list[str]:
    raw = (os.getenv("LOCAL_FOLDER_PATHS") or "").strip()
    return [part.strip() for part in raw.split(os.pathsep) if part.strip()]


def existing_local_paths() -> list[str]:
    return [path for path in local_folder_paths() if Path(path).is_dir()]

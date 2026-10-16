"""Resolve OpenAI-compatible chat and embedding endpoints from env or YAML.

A missing key returns None. Callers then use the offline fallback. Secrets are
read from the environment or keyring and are not returned by public settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.llm.registry import Provider, ProviderRegistry


@dataclass(frozen=True, slots=True)
class Endpoint:
    base_url: str
    api_key: str
    model: str
    provider_id: str
    timeout_s: float = 60.0

    def join(self, suffix: str) -> str:
        """Build ``.../chat/completions`` whether or not base_url already has /v1."""
        base = self.base_url.rstrip("/")
        tail = suffix.lstrip("/")
        if base.endswith("/v1"):
            return f"{base}/{tail}"
        return f"{base}/v1/{tail}"


def chat_endpoint(
    registry: ProviderRegistry | None,
    *,
    provider_id: str | None = None,
    model: str | None = None,
) -> Endpoint | None:
    env_url = _env("LLM_BASE_URL", "OPENAI_BASE_URL")
    env_key = _env("LLM_API_KEY", "OPENAI_API_KEY")
    env_model = _env("LLM_MODEL", "OPENAI_MODEL")
    if env_url and env_key:
        # Last resort when a base URL is set without a model id. Prefer DeepSeek;
        # do not fall back to a Gemini or OpenAI model name.
        chosen = model or env_model or _routed_model(registry, "chat") or "deepseek-v4-flash"
        return Endpoint(
            base_url=env_url,
            api_key=env_key,
            model=chosen,
            provider_id=provider_id or "env",
            timeout_s=_timeout(registry),
        )
    return _from_registry(registry, task="chat", provider_id=provider_id, model=model or env_model, env_key=env_key)


def embed_endpoint(registry: ProviderRegistry | None) -> Endpoint | None:
    env_url = _env("EMBED_BASE_URL")
    env_key = _env("EMBED_API_KEY")
    env_model = _env("EMBED_MODEL")
    if env_url and env_key and env_model:
        return Endpoint(base_url=env_url, api_key=env_key, model=env_model, provider_id="env-embed", timeout_s=_timeout(registry))
    if _env("EMBED_PROVIDER") == "openai" and env_key:
        url = env_url or _env("LLM_BASE_URL", "OPENAI_BASE_URL")
        model_name = env_model or "text-embedding-3-small"
        if url:
            return Endpoint(base_url=url, api_key=env_key, model=model_name, provider_id="env-embed", timeout_s=_timeout(registry))
    return _from_registry(registry, task="embed", provider_id=None, model=env_model, env_key=env_key or _env("LLM_API_KEY", "OPENAI_API_KEY"))


def _from_registry(
    registry: ProviderRegistry | None,
    *,
    task: str,
    provider_id: str | None,
    model: str | None,
    env_key: str | None,
) -> Endpoint | None:
    if registry is None:
        return None
    route = registry.routing.get(task) if isinstance(registry.routing.get(task), dict) else {}
    routed = route or {}
    chosen_id = provider_id or str(routed.get("provider") or "")
    if not chosen_id or chosen_id == "local":
        return None
    provider = next((item for item in registry.providers if item.id == chosen_id and item.enabled), None)
    if provider is None or provider.protocol not in {"openai", "litellm"}:
        return None
    key = env_key or _key_for(provider)
    if not key:
        return None
    chosen_model = model or str(routed.get("model") or "") or (provider.models[0] if provider.models else "")
    if not chosen_model or not provider.base_url:
        return None
    return Endpoint(
        base_url=provider.base_url,
        api_key=key,
        model=chosen_model,
        provider_id=provider.id,
        timeout_s=provider.timeout_s or _timeout(registry),
    )


def _key_for(provider: Provider) -> str | None:
    ref = provider.api_key_ref.strip()
    if not ref:
        return None
    env_name = ref.upper().replace(".", "_").replace("-", "_")
    from_env = os.getenv(env_name) or os.getenv(f"LLM_KEY_{env_name}")
    if from_env:
        return from_env
    try:
        from app.llm.keys import get_api_key

        return get_api_key(ref)
    except Exception:
        return None


def _routed_model(registry: ProviderRegistry | None, task: str) -> str | None:
    if registry is None:
        return None
    route = registry.routing.get(task)
    if isinstance(route, dict) and route.get("model"):
        return str(route["model"])
    return None


def _timeout(registry: ProviderRegistry | None) -> float:
    if registry is None:
        return 60.0
    raw = registry.defaults.get("timeout_s", 60)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 60.0


def _env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None

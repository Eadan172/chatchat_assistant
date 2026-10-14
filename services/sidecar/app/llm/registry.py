"""Load the provider registry from YAML. No network calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Provider:
    id: str
    label: str
    protocol: str
    base_url: str
    api_key_ref: str
    enabled: bool
    models: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    timeout_s: float = 60

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "enabled": self.enabled,
            "models": list(self.models),
            "capabilities": list(self.capabilities),
        }


class ProviderRegistry:
    def __init__(self, raw: dict | None = None) -> None:
        data = raw or {}
        self.version = data.get("version")
        self.defaults = dict(data.get("defaults") or {})
        self.routing = dict(data.get("routing") or {})
        self.providers = [_provider(item) for item in data.get("providers") or []]

    @classmethod
    def load(cls, config_dir: str) -> ProviderRegistry:
        path = Path(config_dir) / "providers.yaml"
        if not path.is_file():
            return cls({})
        import yaml

        with path.open(encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        return cls(loaded)

    def enabled(self) -> list[Provider]:
        return [provider for provider in self.providers if provider.enabled]

    def resolve(self, task: str, provider_id: str | None = None, model: str | None = None):
        raise NotImplementedError("provider routing is not implemented in M0")


def _provider(raw: dict) -> Provider:
    return Provider(
        id=str(raw["id"]),
        label=str(raw.get("label") or raw["id"]),
        protocol=str(raw.get("protocol") or "openai"),
        base_url=str(raw.get("base_url") or ""),
        api_key_ref=str(raw.get("api_key_ref") or ""),
        enabled=bool(raw.get("enabled", False)),
        models=list(raw.get("models") or []),
        capabilities=list(raw.get("capabilities") or []),
        timeout_s=float(raw.get("timeout_s") or 60),
    )

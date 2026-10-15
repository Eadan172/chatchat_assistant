"""Search providers for a later web milestone.

The chain is Google first, then a fallback provider. Neither implementation
calls a network. Personal-library chat does not import this module.
"""

from __future__ import annotations

import os
from typing import Protocol, Sequence


class SearchUnavailable(RuntimeError):
    """The provider is not configured. The chain tries the next one."""


class SearchProvider(Protocol):
    id: str

    def search(self, query: str, *, limit: int = 5) -> list[dict]:
        """Return normalized hits. Raise SearchUnavailable when this provider cannot run."""


class GoogleSearchProvider:
    """First choice for M6. Not implemented in M3."""

    id = "google"

    def search(self, query: str, *, limit: int = 5) -> list[dict]:
        del query, limit
        raise SearchUnavailable("google search is not configured")


class FallbackSearchProvider:
    """Second choice after Google. The concrete engine stays unselected."""

    def __init__(self, provider_id: str = "fallback") -> None:
        self.id = provider_id or "fallback"

    def search(self, query: str, *, limit: int = 5) -> list[dict]:
        del query, limit
        raise SearchUnavailable(f"{self.id} search is not configured")


class SearchProviderChain:
    def __init__(self, providers: Sequence[SearchProvider]) -> None:
        self.providers = list(providers)

    def search(self, query: str, *, limit: int = 5) -> list[dict]:
        errors: list[str] = []
        for provider in self.providers:
            try:
                return provider.search(query, limit=limit)
            except SearchUnavailable as exc:
                errors.append(f"{provider.id}: {exc}")
        raise SearchUnavailable("; ".join(errors) or "no search provider")


def build_search_chain() -> SearchProviderChain:
    """Google, then the fallback named by WEB_SEARCH_FALLBACK (default ``fallback``)."""
    fallback_id = (os.getenv("WEB_SEARCH_FALLBACK") or "fallback").strip() or "fallback"
    providers: list[SearchProvider] = [GoogleSearchProvider()]
    if fallback_id != "google":
        providers.append(FallbackSearchProvider(fallback_id))
    return SearchProviderChain(providers)


def provider_order() -> list[str]:
    return [provider.id for provider in build_search_chain().providers]

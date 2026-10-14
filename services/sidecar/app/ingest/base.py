"""Collect adapter interface. Platform adapters are later milestones."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(slots=True)
class RawItem:
    platform: str
    source_id: str
    url: str
    title: str | None = None
    author: str | None = None
    published_at: str | None = None
    body_html: str | None = None
    media: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


class CollectAdapter(ABC):
    platform: str

    @abstractmethod
    async def collect(
        self,
        *,
        keyword: str | None,
        limit: int,
        since: str | None = None,
    ) -> list[RawItem]:
        raise NotImplementedError

    async def health(self) -> dict:
        return {"platform": self.platform, "ok": True}

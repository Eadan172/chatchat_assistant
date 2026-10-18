"""SearXNG / Tavily providers: parsing, key resolution, unified result shape."""

from __future__ import annotations

from app.web_search import live

HTML = """
<html><body>
<article class="result">
  <h3><a href="https://example.com/a">Dify 介绍</a></h3>
  <p class="content">Dify 是开源 LLM 应用平台</p>
</article>
<article class="result">
  <h3><a href="https://example.com/b">无关条目</a></h3>
  <p class="content">其他内容</p>
</article>
</body></html>
"""


def test_parse_searxng_html_returns_unified_hits() -> None:
    hits = live.parse_searxng_html(HTML)
    assert len(hits) == 2
    assert hits[0]["url"] == "https://example.com/a"
    assert hits[0]["provider"] == "searxng"
    assert hits[0]["source"] == "web"
    assert hits[0]["source_kind"] == "web"


def test_searxng_provider_prefers_json_api(monkeypatch) -> None:
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://127.0.0.1:8080")

    def fake_get(url, params):
        assert params["format"] == "json"
        return 200, {
            "results": [
                {"title": "Dify 是什么", "url": "https://example.com/d", "content": "Dify 平台", "score": 0.8}
            ]
        }

    monkeypatch.setattr(live, "_get_json", fake_get)
    hits, status = live.SearxngProvider().fetch("Dify 是什么", limit=5)
    assert status == 200
    assert hits and hits[0]["provider"] == "searxng"
    assert hits[0]["url"] == "https://example.com/d"


def test_searxng_provider_falls_back_to_html(monkeypatch) -> None:
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://127.0.0.1:8080")

    def failed(url, params):
        raise live.ProviderFailed(403)

    monkeypatch.setattr(live, "_get_json", failed)
    monkeypatch.setattr(live, "_get_text", lambda url, params, headers=None: (200, HTML))
    hits, status = live.SearxngProvider().fetch("Dify 介绍", limit=5)
    assert status == 200
    assert hits and hits[0]["url"] == "https://example.com/a"


def test_tavily_provider_reads_key_from_env(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    monkeypatch.setenv("TAVILY_BASE_URL", "http://127.0.0.1:9")
    seen: dict = {}

    def fake_post(url, body, headers=None):
        seen.update({"url": url, "body": body})
        return 200, {
            "results": [{"title": "T", "url": "https://example.com/t", "content": "c", "score": 0.9}]
        }

    monkeypatch.setattr(live, "_post_json", fake_post)
    hits, status = live.TavilyProvider().fetch("任意查询", limit=3)
    assert status == 200
    assert seen["body"]["api_key"] == "tvly-test-key"
    assert hits[0]["provider"] == "tavily"
    assert hits[0]["source_kind"] == "web"

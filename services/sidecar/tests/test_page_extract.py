from app.web_search import live, page

NAV = "<nav><a>首页</a> <a>法学</a> <a>经济学</a> <a>登录</a> <a>新用户注册</a></nav>"
ARTICLE = "".join(f"<p>示例概念要求把问题拆到最基本的事实，再从这些事实出发推理，第{i}段说明它在 AI 产品设计中的用法。</p>" for i in range(8))
HTML = f"<html><head><title>t</title></head><body>{NAV}<article><h1>标题</h1>{ARTICLE}</article><footer>版权所有 联系我们</footer></body></html>"


def test_extract_drops_nav_and_footer():
    text = page._html_to_text(HTML)
    assert "示例概念要求" in text
    assert "新用户注册" not in text
    assert "联系我们" not in text


def test_readability_fallback_when_trafilatura_empty(monkeypatch):
    monkeypatch.setattr(page, "_trafilatura", lambda raw: "")
    text = page._html_to_text(HTML)
    assert "示例概念要求" in text
    assert "新用户注册" not in text


def test_no_article_keeps_snippet():
    shell = f"<html><body>{NAV}<div id='root'></div><footer>版权所有</footer></body></html>"
    assert page._html_to_text(shell) == ""
    hits = [{"url": "https://www.zhihu.com/q/1", "text": "原 snippet"}]
    out = page.enrich_web_hits(hits, fetch=lambda url: page._html_to_text(shell), limit=4)
    assert out[0]["text"] == "原 snippet"
    assert not out[0].get("page_text")


class _P:
    def __init__(self, pid):
        self.id = pid


def test_failure_notice_does_not_claim_knowledge_base_answer():
    text = live._failure_notice([{"provider": "serper", "http_status": 403, "count": 0}], [_P("serper")])
    assert "Serper（HTTP 403）" in text
    assert "仅基于知识库" not in text
    assert "本次没有网页结果" in text

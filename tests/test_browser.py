from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from goofish_collector.browser import (
    apply_search_filters,
    extract_chat_target,
    extract_card_payloads,
    looks_like_verification,
)
from goofish_collector.models import SearchFilters


@pytest.mark.browser
def test_extract_card_payloads_from_local_html() -> None:
    fixture = Path(__file__).parent / "fixtures" / "search_sample.html"
    html = fixture.read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_content(html)
        payloads = extract_card_payloads(page)
        browser.close()

    assert len(payloads) == 2
    assert payloads[0].href.endswith("id=1001&categoryId=9&spm=test")
    assert payloads[0].anchor_text == "第一件商品"
    assert "¥88" in payloads[0].card_text
    assert payloads[1].href.endswith("id=1002")
    assert "¥199" in payloads[1].card_text


@pytest.mark.parametrize(
    ("url", "text", "expected"),
    [
        ("https://passport.goofish.com/login", "扫码登录", True),
        ("https://www.goofish.com/search?q=耳机", "请完成安全验证", True),
        ("https://www.goofish.com/search?q=耳机", "商品列表", False),
    ],
)
def test_verification_detection(url: str, text: str, expected: bool) -> None:
    assert looks_like_verification(url, text) is expected


def test_extract_chat_target_requires_real_seller_id() -> None:
    seller_id, chat_url = extract_chat_target(
        [
            "https://www.goofish.com/item?id=123",
            "https://www.goofish.com/im?itemId=123&peerUserId=abc_456&foo=ignored",
        ],
        "123",
    )

    assert seller_id == "abc_456"
    assert chat_url == "https://www.goofish.com/im?itemId=123&peerUserId=abc_456"
    assert extract_chat_target(["https://www.goofish.com/item?id=123"], "123") == ("", "")


@pytest.mark.browser
def test_apply_search_filters_uses_visible_search_controls() -> None:
    html = """
    <html><body>
      <div><span>价格</span><input placeholder="¥"><span>-</span><input placeholder="¥"><button id="confirm">确定</button></div>
      <button id="publish">新发布</button><button id="within" onclick="document.body.dataset.published='3天内'">3天内</button>
      <button id="region">区域</button><button id="guangdong" onclick="document.body.dataset.region='广东'">广东</button>
      <label><input type="checkbox" aria-label="个人闲置">个人闲置</label>
      <label><input type="checkbox" aria-label="验货宝">验货宝</label>
      <label><input type="checkbox" aria-label="包邮">包邮</label>
      <label><input type="checkbox" aria-label="全新">全新</label>
      <article><a href="https://www.goofish.com/item?id=1">商品</a><span>¥200</span></article>
    </body></html>
    """
    filters = SearchFilters(
        min_price=100,
        max_price=800,
        region="广东",
        published_within="3天内",
        personal_only=True,
        inspection_only=True,
        free_shipping=True,
        brand_new=True,
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_content(html)
        apply_search_filters(page, filters)
        state = page.evaluate(
            """() => ({
              prices: Array.from(document.querySelectorAll('input[placeholder="¥"]')).map(x => x.value),
              checked: Array.from(document.querySelectorAll('input[type="checkbox"]')).map(x => x.checked),
              region: document.body.dataset.region,
              published: document.body.dataset.published,
            })"""
        )
        browser.close()

    assert state["prices"] == ["100", "800"]
    assert state["checked"] == [True, True, True, True]
    assert state["region"] == "广东"
    assert state["published"] == "3天内"

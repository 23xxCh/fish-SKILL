from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from goofish_collector.browser import GoofishBrowserSession


@pytest.mark.browser
def test_go_to_next_page_supports_current_arrow_only_button(tmp_path: Path) -> None:
    html = """
    <html><body>
      <a id="item" href="https://www.goofish.com/item?id=1001">第一页商品</a>
      <div class="search-page-tiny-container--hash">
        <button class="search-page-tiny-arrow-container--hash" disabled>
          <div class="search-page-tiny-arrow-left--hash"></div>
        </button>
        <span class="search-page-tiny-page--hash">1/50</span>
        <button class="search-page-tiny-arrow-container--hash" onclick="nextPage()">
          <div class="search-page-tiny-arrow-right--hash"></div>
        </button>
      </div>
      <script>
        function nextPage() {
          document.querySelector('#item').href = 'https://www.goofish.com/item?id=2001';
          document.querySelector('#item').textContent = '第二页商品';
          document.querySelector('.search-page-tiny-page--hash').textContent = '2/50';
        }
      </script>
    </body></html>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_content(html)
        session = GoofishBrowserSession(tmp_path / "profile", headless=True)
        session._page = page

        assert session.goto_next_page() is True
        assert "id=2001" in page.locator("#item").get_attribute("href")
        assert page.locator(".search-page-tiny-page--hash").inner_text() == "2/50"
        browser.close()


@pytest.mark.browser
def test_missing_next_button_is_not_silently_treated_as_last_page(tmp_path: Path) -> None:
    html = """
    <html><body>
      <a href="https://www.goofish.com/item?id=1001">第一页商品</a>
      <span class="search-page-tiny-page--hash">1/50</span>
    </body></html>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_content(html)
        session = GoofishBrowserSession(tmp_path / "profile", headless=True)
        session._page = page

        with pytest.raises(RuntimeError, match="页面显示还有下一页"):
            session.goto_next_page()
        browser.close()


@pytest.mark.browser
def test_missing_next_button_is_valid_on_the_last_page(tmp_path: Path) -> None:
    html = """
    <html><body>
      <a href="https://www.goofish.com/item?id=5001">末页商品</a>
      <span class="search-page-tiny-page--hash">50/50</span>
    </body></html>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_content(html)
        session = GoofishBrowserSession(tmp_path / "profile", headless=True)
        session._page = page

        assert session.goto_next_page() is False
        browser.close()

from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, urlencode, urlparse

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from .crawler import ManualVerificationRequired
from .models import SearchFilters
from .parser import CardPayload


SEARCH_URL = "https://www.goofish.com/search?q={}"
LOGIN_URL = "https://www.goofish.com/"
ITEM_LINK_SELECTOR = 'a[href*="/item?id="], a[href*="goofish.com/item?id="]'
NEXT_SELECTORS = (
    'button:has([class*="search-page-tiny-arrow-right"])',
    'button:has([class*="search-pagination-arrow-right"])',
    'button:has-text("下一页")',
    'a:has-text("下一页")',
    '[aria-label*="下一页"]',
    '[title="下一页"]',
    'li[class*="next"] button',
    '[class*="pagination-next"] button',
)


def default_profile_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root / "GoofishLinkCollector" / "browser-profile"


def profile_has_saved_login(profile_dir: Path | None = None) -> bool:
    """Checks for an unexpired Goofish cookie without reading cookie values."""
    root = (profile_dir or default_profile_dir()).resolve()
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    now = int((datetime.now(timezone.utc) - epoch).total_seconds() * 1_000_000)
    for database in (
        root / "Default" / "Network" / "Cookies",
        root / "Default" / "Cookies",
    ):
        if not database.is_file():
            continue
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                database.as_uri() + "?mode=ro",
                uri=True,
                timeout=0.2,
            )
            row = connection.execute(
                "SELECT 1 FROM cookies "
                "WHERE (host_key = 'goofish.com' OR host_key LIKE '%.goofish.com') "
                "AND length(encrypted_value) > 0 AND expires_utc > ? LIMIT 1",
                (now,),
            ).fetchone()
            if row is not None:
                return True
        except (OSError, sqlite3.Error):
            continue
        finally:
            if connection is not None:
                connection.close()
    return False


def looks_like_verification(url: str, body_text: str) -> bool:
    normalized_url = url.lower()
    if any(part in normalized_url for part in ("passport.goofish.com", "/login", "verify")):
        return True
    signals = ("请完成安全验证", "安全验证", "扫码登录", "滑块验证", "验证码")
    return any(signal in body_text for signal in signals)


def extract_chat_target(candidates: list[str], item_id: str) -> tuple[str, str]:
    """Builds a chat URL only when a real peerUserId is present in page data."""
    for candidate in candidates:
        try:
            parsed = urlparse(candidate)
            query = parse_qs(parsed.query)
        except ValueError:
            continue
        peer_id = (query.get("peerUserId") or query.get("peer_user_id") or [""])[0]
        candidate_item = (query.get("itemId") or query.get("item_id") or [""])[0]
        if not peer_id or not candidate_item:
            continue
        if item_id and candidate_item != item_id:
            continue
        chat_url = "https://www.goofish.com/im?" + urlencode(
            {"itemId": candidate_item, "peerUserId": peer_id}
        )
        return peer_id, chat_url
    return "", ""


def extract_card_payloads(page: Page) -> list[CardPayload]:
    rows = page.evaluate(
        r"""
        (selector) => {
          const anchors = Array.from(document.querySelectorAll(selector));
          const seen = new Set();
          const rows = [];
          for (const anchor of anchors) {
            let itemId = "";
            try {
              const parsed = new URL(anchor.href, window.location.href);
              itemId = parsed.searchParams.get("id") || "";
            } catch (_) {}
            const key = itemId || anchor.href;
            if (!key || seen.has(key)) continue;
            seen.add(key);

            const anchorText = (anchor.innerText || anchor.textContent || "").trim();
            let node = anchor;
            let bestText = anchorText;
            for (let depth = 0; depth < 8 && node; depth += 1, node = node.parentElement) {
              const text = (node.innerText || node.textContent || "").trim();
              if (!text || text.length > 3000) continue;
              const itemLinks = node.querySelectorAll
                ? node.querySelectorAll(selector).length
                : 0;
              if (text.length >= bestText.length) bestText = text;
              if (/[¥￥]\s*[\d,.]+/.test(text) && itemLinks <= 2) {
                bestText = text;
                break;
              }
            }
            rows.push({ href: anchor.href, anchorText, cardText: bestText });
          }
          return rows;
        }
        """,
        ITEM_LINK_SELECTOR,
    )
    return [
        CardPayload(
            href=str(row.get("href", "")),
            anchor_text=str(row.get("anchorText", "")),
            card_text=str(row.get("cardText", "")),
        )
        for row in rows
    ]


def _display_price(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if value.is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def _visible_locator(locator):
    for index in range(locator.count()):
        candidate = locator.nth(index)
        try:
            if candidate.is_visible():
                return candidate
        except Exception:
            continue
    return None


def _click_named_control(page: Page, text: str, *, option: bool = False) -> None:
    roles = ("option", "menuitem", "button") if option else ("button", "combobox")
    for role in roles:
        candidate = _visible_locator(page.get_by_role(role, name=text, exact=True))
        if candidate is not None:
            candidate.click()
            return
    matches = page.get_by_text(text, exact=True)
    visible = []
    for index in range(matches.count()):
        candidate = matches.nth(index)
        try:
            if candidate.is_visible():
                visible.append(candidate)
        except Exception:
            continue
    if visible:
        (visible[-1] if option else visible[0]).click()
        return
    raise RuntimeError(f"网页上没有找到筛选控件：{text}")


def _ensure_checkbox(page: Page, label: str) -> None:
    for locator in (
        page.get_by_role("checkbox", name=label, exact=True),
        page.get_by_label(label, exact=True),
    ):
        candidate = _visible_locator(locator)
        if candidate is not None:
            if not candidate.is_checked():
                candidate.check()
            return

    text_locator = _visible_locator(page.get_by_text(label, exact=True))
    if text_locator is None:
        raise RuntimeError(f"网页上没有找到复选条件：{label}")
    handled = text_locator.evaluate(
        """
        (element) => {
          const root = element.closest('label') || element.parentElement;
          const input = root && root.querySelector('input[type="checkbox"]');
          if (input) {
            if (!input.checked) input.click();
            return true;
          }
          const roleBox = root && root.querySelector('[role="checkbox"]');
          if (roleBox) {
            if (roleBox.getAttribute('aria-checked') !== 'true') roleBox.click();
            return true;
          }
          return false;
        }
        """
    )
    if not handled:
        text_locator.click()


def apply_search_filters(page: Page, filters: SearchFilters) -> None:
    filters.validate()
    if not filters.is_active:
        return

    if filters.min_price is not None or filters.max_price is not None:
        price_inputs = page.locator(
            'input[placeholder="¥"], input[placeholder="￥"], '
            'input[placeholder*="最低"], input[placeholder*="最高"]'
        )
        if price_inputs.count() < 2:
            raise RuntimeError("网页上没有找到最低价和最高价输入框")
        price_inputs.nth(0).fill(_display_price(filters.min_price))
        price_inputs.nth(1).fill(_display_price(filters.max_price))
        confirm = _visible_locator(page.get_by_role("button", name="确定", exact=True))
        if confirm is not None:
            confirm.click()
        else:
            price_inputs.nth(1).press("Enter")
        page.wait_for_timeout(500)

    if filters.sort_mode != "综合" and not (
        filters.sort_mode == "新发布" and filters.published_within
    ):
        _click_named_control(page, filters.sort_mode)
        page.wait_for_timeout(500)

    if filters.published_within:
        _click_named_control(page, "新发布")
        page.wait_for_timeout(200)
        _click_named_control(page, filters.published_within, option=True)
        page.wait_for_timeout(500)

    if filters.region:
        _click_named_control(page, "区域")
        page.wait_for_timeout(200)
        _click_named_control(page, filters.region, option=True)
        page.wait_for_timeout(500)

    for enabled, label in (
        (filters.personal_only, "个人闲置"),
        (filters.inspection_only, "验货宝"),
        (filters.free_shipping, "包邮"),
        (filters.brand_new, "全新"),
    ):
        if enabled:
            _ensure_checkbox(page, label)
            page.wait_for_timeout(350)


def _launch_context(
    playwright: Playwright,
    profile_dir: Path,
    *,
    headless: bool,
    minimized: bool = False,
) -> BrowserContext:
    profile_dir.mkdir(parents=True, exist_ok=True)
    common = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "locale": "zh-CN",
        "viewport": None,
        "args": ["--start-minimized" if minimized else "--start-maximized"],
    }
    launch_errors: list[str] = []
    for channel in ("msedge", "chrome"):
        try:
            return playwright.chromium.launch_persistent_context(channel=channel, **common)
        except Exception as exc:
            launch_errors.append(f"{channel}: {exc}")

    candidates = (
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    )
    for executable in candidates:
        if not executable.is_file():
            continue
        try:
            return playwright.chromium.launch_persistent_context(
                executable_path=str(executable), **common
            )
        except Exception as exc:
            launch_errors.append(f"{executable}: {exc}")
    details = "\n".join(launch_errors[-2:])
    raise RuntimeError(f"无法启动 Edge 或 Chrome。请确认浏览器未占用专用资料目录。\n{details}")


class GoofishBrowserSession:
    def __init__(
        self,
        profile_dir: Path | None = None,
        *,
        headless: bool = False,
        minimized: bool = False,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        self.profile_dir = (profile_dir or default_profile_dir()).resolve()
        self.headless = headless
        self.minimized = minimized
        self.on_log = on_log or (lambda _: None)
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def __enter__(self) -> GoofishBrowserSession:
        self._playwright = sync_playwright().start()
        try:
            self._context = _launch_context(
                self._playwright,
                self.profile_dir,
                headless=self.headless,
                minimized=self.minimized,
            )
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.set_default_timeout(30_000)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._context = None
        self._playwright = None
        self._page = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("浏览器会话尚未启动")
        return self._page

    def _body_text(self) -> str:
        try:
            return self.page.locator("body").inner_text(timeout=5_000)[:20_000]
        except Exception:
            return ""

    def _raise_if_verification(self) -> None:
        if looks_like_verification(self.page.url, self._body_text()):
            raise ManualVerificationRequired("闲鱼要求登录或完成安全验证")

    def open_login(self, stop_requested: Callable[[], bool] | None = None) -> None:
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        self.on_log("请在浏览器中扫码登录；登录完成后关闭整个浏览器窗口。")
        while self._context is not None and self._context.pages:
            if stop_requested is not None and stop_requested():
                break
            try:
                self._context.pages[0].wait_for_timeout(500)
            except Exception:
                break

    def open_search(self, keyword: str) -> None:
        url = SEARCH_URL.format(quote(keyword))
        self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        try:
            self.page.locator(ITEM_LINK_SELECTOR).first.wait_for(state="attached", timeout=30_000)
        except Exception:
            self._raise_if_verification()
            raise RuntimeError("搜索页在 30 秒内没有出现商品卡片")
        self._raise_if_verification()

    def apply_filters(self, filters: SearchFilters) -> None:
        self._raise_if_verification()
        apply_search_filters(self.page, filters)
        self._raise_if_verification()

    def _settle_cards(self) -> None:
        stable_rounds = 0
        previous_count = -1
        for _ in range(8):
            self._raise_if_verification()
            count = self.page.locator(ITEM_LINK_SELECTOR).count()
            if count == previous_count and count > 0:
                stable_rounds += 1
            else:
                stable_rounds = 0
            if stable_rounds >= 2:
                break
            previous_count = count
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self.page.wait_for_timeout(700)

    def extract_cards(self) -> list[CardPayload]:
        self._raise_if_verification()
        self._settle_cards()
        payloads = extract_card_payloads(self.page)
        if not payloads:
            self._raise_if_verification()
            raise RuntimeError("当前页面没有找到有效商品链接")
        return payloads

    def _signature(self) -> str:
        payloads = extract_card_payloads(self.page)
        return "|".join(payload.href for payload in payloads[:10])

    def _next_button(self):
        for selector in NEXT_SELECTORS:
            locator = self.page.locator(selector)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                try:
                    if candidate.is_visible():
                        return candidate
                except Exception:
                    continue
        return None

    def _page_position(self) -> tuple[int, int] | None:
        for selector in ('[class*="search-page-tiny-page"]', "span"):
            locator = self.page.locator(selector)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                try:
                    if not candidate.is_visible():
                        continue
                    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", candidate.inner_text())
                except Exception:
                    continue
                if match:
                    return int(match.group(1)), int(match.group(2))
        return None

    def goto_next_page(self) -> bool:
        self._raise_if_verification()
        before = self._signature()
        position = self._page_position()
        button = self._next_button()
        if button is None:
            if position is not None and position[0] < position[1]:
                raise RuntimeError(
                    f"页面显示还有下一页（{position[0]}/{position[1]}），但未找到下一页按钮"
                )
            return False
        disabled = button.get_attribute("disabled") is not None
        aria_disabled = (button.get_attribute("aria-disabled") or "").lower() == "true"
        class_name = (button.get_attribute("class") or "").lower()
        if disabled or aria_disabled or "disabled" in class_name:
            if position is not None and position[0] < position[1]:
                raise RuntimeError(
                    f"页面显示还有下一页（{position[0]}/{position[1]}），但下一页按钮暂不可用"
                )
            return False

        button.scroll_into_view_if_needed()
        button.click(timeout=15_000)
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(500)
            self._raise_if_verification()
            after = self._signature()
            if after and after != before:
                self.page.evaluate("window.scrollTo(0, 0)")
                return True
        raise RuntimeError("点击下一页后商品列表未发生变化")

    def resolve_chat_link(self, record) -> tuple[str, str]:
        """Visits one new item and extracts a real peer id without sending a message."""
        if self._context is None:
            raise RuntimeError("浏览器会话尚未启动")
        detail = self._context.new_page()
        detail.set_default_timeout(20_000)
        try:
            detail.goto(record.url, wait_until="domcontentloaded", timeout=45_000)
            try:
                body_text = detail.locator("body").inner_text(timeout=5_000)[:20_000]
            except Exception:
                body_text = ""
            if looks_like_verification(detail.url, body_text):
                raise ManualVerificationRequired("闲鱼要求登录或完成安全验证")
            detail.wait_for_timeout(1_000)
            hrefs = detail.locator('a[href*="peerUserId"], a[href*="peer_user_id"]').evaluate_all(
                "elements => elements.map(element => element.href || element.getAttribute('href') || '')"
            )
            seller_id, chat_url = extract_chat_target(
                [str(value) for value in hrefs], record.item_id
            )
            return seller_id, chat_url
        finally:
            detail.close()


def run_login_browser(
    profile_dir: Path | None = None,
    *,
    on_log: Callable[[str], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    actual_profile = (profile_dir or default_profile_dir()).resolve()
    saved_login = profile_has_saved_login(actual_profile)
    with GoofishBrowserSession(actual_profile, on_log=on_log) as session:
        if saved_login and on_log is not None:
            on_log("已载入本机保存的登录状态；如需切换账号，可在浏览器中操作。")
        session.open_login(stop_requested=stop_requested)

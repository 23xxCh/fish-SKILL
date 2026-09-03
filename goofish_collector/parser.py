from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

from .models import ProductRecord


ITEM_HOST = "www.goofish.com"
ITEM_PATH = "/item"


@dataclass(frozen=True)
class CardPayload:
    href: str
    anchor_text: str
    card_text: str
    image_url: str = ""


def parse_item_id(url: str) -> str:
    try:
        values = parse_qs(urlparse(url).query).get("id", [])
    except ValueError:
        return ""
    item_id = values[0].strip() if values else ""
    return item_id if item_id.isdigit() else ""


def normalize_item_url(url: str) -> str:
    item_id = parse_item_id(url)
    if not item_id:
        return ""
    query = parse_qs(urlparse(url).query)
    stable = {"id": item_id}
    category_id = next(iter(query.get("categoryId", [])), "").strip()
    if category_id:
        stable["categoryId"] = category_id
    return f"https://{ITEM_HOST}{ITEM_PATH}?{urlencode(stable)}"


def normalize_image_url(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
    except (AttributeError, ValueError):
        return ""
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return parsed.geturl()


def _parse_number(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _extract_prices(text: str) -> tuple[float | None, float | None]:
    original_match = re.search(r"原价\s*[¥￥]?\s*([\d,.]+)", text, re.IGNORECASE)
    original_price = _parse_number(original_match.group(1)) if original_match else None
    currency_matches = list(re.finditer(r"[¥￥]\s*([\d,.]+)", text))
    price = None
    for match in currency_matches:
        if original_match and original_match.start() <= match.start() < original_match.end():
            continue
        price = _parse_number(match.group(1))
        if price is not None:
            break
    return price, original_price


_REGIONS = (
    "北京|上海|天津|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|"
    "山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|内蒙古|广西|"
    "西藏|宁夏|新疆|香港|澳门|台湾"
)


def _extract_region(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    region_pattern = re.compile(rf"^(?:{_REGIONS})(?:省|市|自治区)?$")
    for line in reversed(lines):
        if region_pattern.fullmatch(line):
            return line
    matches = re.findall(rf"(?:^|\s)({_REGIONS})(?=\s|$)", text)
    return matches[-1] if matches else ""


def _extract_first(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(0).strip() if match else ""


def parse_card(
    payload: CardPayload,
    *,
    keyword: str,
    page: int,
    captured_at: str,
) -> ProductRecord | None:
    normalized_url = normalize_item_url(payload.href)
    if not normalized_url:
        return None
    raw_text = re.sub(r"[ \t]+", " ", payload.card_text).strip()
    title_lines = [line.strip() for line in payload.anchor_text.splitlines() if line.strip()]
    title = title_lines[0] if title_lines else ""
    if not title:
        title = next((line.strip() for line in raw_text.splitlines() if line.strip()), "")
    price, original_price = _extract_prices(raw_text)
    wants_match = re.search(r"(\d+)\s*人(?:想要|想)", raw_text)
    condition = _extract_first(
        r"(?:全新未拆|全新|几乎全新|\d{1,2}(?:\.\d)?\s*成新|\d{1,2}\s*新|成色[^\s，。]{0,8})",
        raw_text,
    )
    reputation = _extract_first(
        r"(?:百分百好评|\d{1,3}%\s*好评|信用极好|芝麻信用[^\s，。]{0,8})",
        raw_text,
    )
    publish_or_change = _extract_first(
        r"(?:刚刚|今天|\d+\s*(?:分钟|小时|天|月)内?)\s*(?:发布|降价)",
        raw_text,
    )
    discount = _extract_first(r"累计降价\s*[\d,.]+\s*元", raw_text)
    return ProductRecord(
        keyword=keyword.strip(),
        item_id=parse_item_id(normalized_url),
        title=title,
        url=normalized_url,
        price=price,
        original_price=original_price,
        region=_extract_region(raw_text),
        condition=condition,
        wants=int(wants_match.group(1)) if wants_match else None,
        reputation=reputation,
        publish_or_change=publish_or_change,
        discount=discount,
        first_page=page,
        pages_seen=[page],
        captured_at=captured_at,
        raw_text=raw_text,
        image_url=normalize_image_url(payload.image_url),
    )

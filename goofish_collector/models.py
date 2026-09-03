from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


PUBLISH_WINDOWS = ("", "最新", "1天内", "3天内", "7天内", "14天内")
SORT_MODES = ("综合", "新降价", "新发布")


@dataclass(frozen=True)
class SearchFilters:
    min_price: float | None = None
    max_price: float | None = None
    region: str = ""
    published_within: str = ""
    personal_only: bool = False
    inspection_only: bool = False
    free_shipping: bool = False
    brand_new: bool = False
    sort_mode: str = "综合"

    def __post_init__(self) -> None:
        object.__setattr__(self, "region", self.region.strip())
        object.__setattr__(self, "published_within", self.published_within.strip())
        object.__setattr__(self, "sort_mode", self.sort_mode.strip() or "综合")
        if self.min_price is not None:
            object.__setattr__(self, "min_price", float(self.min_price))
        if self.max_price is not None:
            object.__setattr__(self, "max_price", float(self.max_price))

    def validate(self) -> None:
        if self.min_price is not None and self.min_price < 0:
            raise ValueError("最低价不能小于 0")
        if self.max_price is not None and self.max_price < 0:
            raise ValueError("最高价不能小于 0")
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("最低价不能高于最高价")
        if self.published_within not in PUBLISH_WINDOWS:
            raise ValueError("不支持的发布时间筛选")
        if self.sort_mode not in SORT_MODES:
            raise ValueError("不支持的排序方式")

    @property
    def is_active(self) -> bool:
        return any(
            (
                self.min_price is not None,
                self.max_price is not None,
                bool(self.region),
                bool(self.published_within),
                self.personal_only,
                self.inspection_only,
                self.free_shipping,
                self.brand_new,
                self.sort_mode != "综合",
            )
        )

    def active_labels(self) -> list[str]:
        labels: list[str] = []
        for enabled, label in (
            (self.personal_only, "个人闲置"),
            (self.inspection_only, "验货宝"),
            (self.free_shipping, "包邮"),
            (self.brand_new, "全新"),
        ):
            if enabled:
                labels.append(label)
        return labels

    def price_label(self) -> str:
        if self.min_price is None and self.max_price is None:
            return "不限"
        minimum = f"¥{self.min_price:.2f}" if self.min_price is not None else "不限"
        maximum = f"¥{self.max_price:.2f}" if self.max_price is not None else "不限"
        return f"{minimum} - {maximum}"

    def other_label(self) -> str:
        parts = [self.published_within or "不限"]
        if self.sort_mode != "综合":
            parts.append(f"排序：{self.sort_mode}")
        labels = self.active_labels()
        if labels:
            parts.append("、".join(labels))
        return "｜".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SearchFilters:
        return cls(**(data or {}))


@dataclass(frozen=True)
class CrawlConfig:
    keyword: str
    max_pages: int = 50
    max_items: int = 0
    output_dir: Path = Path("outputs")
    filters: SearchFilters = field(default_factory=SearchFilters)

    def __post_init__(self) -> None:
        keyword = self.keyword.strip()
        if not keyword:
            raise ValueError("关键词不能为空")
        if not 1 <= self.max_pages <= 200:
            raise ValueError("最大页数必须在 1 到 200 之间")
        if self.max_items < 0:
            raise ValueError("最大商品数不能小于 0")
        self.filters.validate()
        object.__setattr__(self, "keyword", keyword)
        object.__setattr__(self, "output_dir", Path(self.output_dir).expanduser().resolve())

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "max_pages": self.max_pages,
            "max_items": self.max_items,
            "output_dir": str(self.output_dir),
            "filters": self.filters.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrawlConfig:
        return cls(
            keyword=str(data["keyword"]),
            max_pages=int(data.get("max_pages", 50)),
            max_items=int(data.get("max_items", 0)),
            output_dir=Path(data["output_dir"]),
            filters=SearchFilters.from_dict(data.get("filters")),
        )


@dataclass
class ProductRecord:
    keyword: str
    item_id: str
    title: str
    url: str
    price: float | None = None
    original_price: float | None = None
    region: str = ""
    condition: str = ""
    wants: int | None = None
    reputation: str = ""
    publish_or_change: str = ""
    discount: str = ""
    first_page: int = 1
    appearances: int = 1
    pages_seen: list[int] = field(default_factory=list)
    captured_at: str = ""
    raw_text: str = ""
    seller_id: str = ""
    chat_url: str = ""
    first_seen_at: str = ""
    notified_at: str = ""

    def __post_init__(self) -> None:
        if not self.pages_seen:
            self.pages_seen = [self.first_page]
        self.pages_seen = sorted(set(int(page) for page in self.pages_seen))
        self.appearances = max(1, int(self.appearances))

    @property
    def key(self) -> str:
        return f"id:{self.item_id}" if self.item_id else f"url:{self.url.strip().lower()}"

    def merge(self, other: ProductRecord) -> None:
        if self.key != other.key:
            raise ValueError("只能合并相同商品")
        self.appearances += other.appearances
        self.pages_seen = sorted(set(self.pages_seen + other.pages_seen))
        for name in (
            "title",
            "price",
            "original_price",
            "region",
            "condition",
            "wants",
            "reputation",
            "publish_or_change",
            "discount",
            "captured_at",
            "raw_text",
            "seller_id",
            "chat_url",
            "first_seen_at",
            "notified_at",
        ):
            value = getattr(other, name)
            if value not in (None, ""):
                setattr(self, name, value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProductRecord:
        return cls(**data)


class RecordCollection:
    def __init__(self, records: Iterable[ProductRecord] = ()) -> None:
        self._records: dict[str, ProductRecord] = {}
        for record in records:
            self.add(record)

    @property
    def records(self) -> list[ProductRecord]:
        return list(self._records.values())

    def __len__(self) -> int:
        return len(self._records)

    def get(self, record: ProductRecord) -> ProductRecord:
        return self._records[record.key]

    def add(self, record: ProductRecord) -> bool:
        existing = self._records.get(record.key)
        if existing is None:
            self._records[record.key] = record
            return True
        existing.merge(record)
        return False


@dataclass(frozen=True)
class CrawlProgress:
    current_page: int
    raw_records: int
    unique_records: int
    status: str
    message: str = ""

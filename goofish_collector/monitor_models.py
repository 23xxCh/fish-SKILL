from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from uuid import uuid4

from .models import ProductRecord, SearchFilters


MONITOR_INTERVALS = (5, 10, 15, 30)


def _parse_clock(value: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("免打扰时间必须使用 HH:MM 格式") from exc


def quiet_until(now: datetime, start: str, end: str) -> datetime | None:
    """Returns the end of the active quiet period, including overnight ranges."""
    start_time = _parse_clock(start)
    end_time = _parse_clock(end)
    if start_time == end_time:
        return None
    current = now.time().replace(second=0, microsecond=0)
    if start_time < end_time:
        if start_time <= current < end_time:
            return datetime.combine(now.date(), end_time)
        return None
    if current >= start_time:
        return datetime.combine(now.date() + timedelta(days=1), end_time)
    if current < end_time:
        return datetime.combine(now.date(), end_time)
    return None


@dataclass(frozen=True)
class MonitorTaskConfig:
    name: str
    keyword: str
    task_id: str = field(default_factory=lambda: uuid4().hex)
    filters: SearchFilters = field(default_factory=SearchFilters)
    pages: int = 1
    interval_minutes: int = 10
    quiet_enabled: bool = False
    quiet_start: str = "22:00"
    quiet_end: str = "07:00"
    enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "keyword", self.keyword.strip())
        if not self.name:
            raise ValueError("任务名称不能为空")
        if not self.keyword:
            raise ValueError("关键词不能为空")
        if self.pages not in (1, 2, 3):
            raise ValueError("扫描页数只能选择 1 到 3 页")
        if self.interval_minutes not in MONITOR_INTERVALS:
            raise ValueError("监控间隔只能选择 5、10、15 或 30 分钟")
        self.filters.validate()
        _parse_clock(self.quiet_start)
        _parse_clock(self.quiet_end)

    @property
    def rule_fingerprint(self) -> str:
        payload = {
            "keyword": self.keyword,
            "filters": self.filters.to_dict(),
            "pages": self.pages,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["filters"] = self.filters.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MonitorTaskConfig:
        values = dict(data)
        values["filters"] = SearchFilters.from_dict(values.get("filters"))
        return cls(**values)


@dataclass(frozen=True)
class MonitorTaskState:
    config: MonitorTaskConfig
    generation: int = 1
    baseline_ready: bool = False
    status: str = "paused"
    last_run_at: str = ""
    next_run_at: str = ""
    last_error: str = ""


@dataclass(frozen=True)
class NotificationProviderConfig:
    provider_id: str = "feishu"

    def __post_init__(self) -> None:
        if self.provider_id not in ("feishu", "wxpusher"):
            raise ValueError("不支持的通知通道")


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str = ""
    app_secret: str = ""
    open_id: str = ""


@dataclass(frozen=True)
class WxPusherConfig:
    spt: str = ""


@dataclass
class NotificationBatch:
    task_id: str
    task_name: str
    provider_id: str
    items: list[ProductRecord]
    total_count: int
    batch_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    available_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    attempts: int = 0
    status: str = "pending"
    last_error: str = ""
    merge_key: str = ""
    item_label: str = "新品"

    def __post_init__(self) -> None:
        if self.provider_id not in ("feishu", "wxpusher"):
            raise ValueError("不支持的通知通道")
        if self.total_count < len(self.items):
            raise ValueError("新品总数不能小于消息商品数")
        self.items = list(self.items[:10])
        self.item_label = self.item_label.strip() or "商品"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["items"] = [item.to_dict() for item in self.items]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NotificationBatch:
        values = dict(data)
        values["items"] = [ProductRecord.from_dict(row) for row in values.get("items", [])]
        return cls(**values)


@dataclass(frozen=True)
class DeliveryResult:
    provider_id: str
    success: bool
    message: str = ""
    status_code: int | None = None
    retryable: bool = True


@dataclass(frozen=True)
class ScanApplyResult:
    baseline_created: bool
    new_records: list[ProductRecord]
    seen_count: int


@dataclass(frozen=True)
class MonitorRunResult:
    task_id: str
    baseline_created: bool
    new_count: int
    scanned_count: int
    queued_batch_id: str = ""

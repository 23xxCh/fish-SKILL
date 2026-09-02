from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Callable, Protocol

from .models import ProductRecord, RecordCollection
from .monitor_models import MonitorRunResult, MonitorTaskConfig, NotificationBatch, quiet_until
from .monitor_store import MonitorStore
from .notifications import FeishuProvider, NotificationProvider, WxPusherProvider
from .parser import parse_card


def next_run_after(previous_due: datetime | None, now: datetime, interval_minutes: int) -> datetime:
    """Schedules one future run; missed intervals are deliberately not replayed."""
    if previous_due is None or previous_due <= now:
        return now + timedelta(minutes=interval_minutes)
    return previous_due + timedelta(minutes=interval_minutes)


class Scanner(Protocol):
    def scan(self, task: MonitorTaskConfig) -> list[ProductRecord]: ...

    def enrich_chat_links(self, records: list[ProductRecord]) -> None: ...


class MonitorSearchSession(Protocol):
    def open_search(self, keyword: str) -> None: ...

    def apply_filters(self, filters) -> None: ...

    def extract_cards(self): ...

    def goto_next_page(self) -> bool: ...

    def resolve_chat_link(self, record: ProductRecord) -> tuple[str, str]: ...


class BrowserMonitorScanner:
    def __init__(
        self,
        session: MonitorSearchSession,
        *,
        on_log: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session
        self.on_log = on_log or (lambda _: None)
        self.sleep = sleep

    def _retry(self, operation: Callable, label: str):
        for attempt in range(3):
            try:
                return operation()
            except Exception as exc:
                if attempt == 2:
                    raise
                delay = (2, 5)[attempt]
                self.on_log(f"{label}失败，{delay}秒后重试（{attempt + 1}/2）：{exc}")
                self.sleep(delay)

    def scan(self, task: MonitorTaskConfig) -> list[ProductRecord]:
        self._retry(lambda: self.session.open_search(task.keyword), "打开搜索页")
        self._retry(lambda: self.session.apply_filters(task.filters), "应用监控筛选")
        collection = RecordCollection()
        for page_number in range(1, task.pages + 1):
            payloads = self._retry(self.session.extract_cards, f"读取第{page_number}页")
            captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for payload in payloads:
                record = parse_card(
                    payload,
                    keyword=task.keyword,
                    page=page_number,
                    captured_at=captured_at,
                )
                if record is not None:
                    collection.add(record)
            self.on_log(
                f"监控任务“{task.name}”第 {page_number} 页读取 {len(payloads)} 条。"
            )
            if page_number < task.pages:
                if not self._retry(self.session.goto_next_page, "翻到下一页"):
                    break
                self.sleep(1)
        return collection.records

    def enrich_chat_links(self, records: list[ProductRecord]) -> None:
        for record in records:
            try:
                seller_id, chat_url = self._retry(
                    lambda current=record: self.session.resolve_chat_link(current),
                    "读取真实聊天地址",
                )
            except Exception as exc:
                self.on_log(f"商品 {record.item_id or record.url} 未取到聊天地址：{exc}")
                continue
            record.seller_id = seller_id
            record.chat_url = chat_url


class MonitorCoordinator:
    def __init__(
        self,
        store: MonitorStore,
        scanner: Scanner,
        *,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.scanner = scanner
        self.on_log = on_log or (lambda _: None)

    def run_task(self, task_id: str, *, now: datetime | None = None) -> MonitorRunResult:
        current = now or datetime.now()
        state = self.store.get_task_state(task_id)
        task = state.config
        self.store.update_task_runtime(task_id, status="running", last_error="")
        try:
            records = self.scanner.scan(task)
            applied = self.store.record_scan(task_id, records, scanned_at=current)
            batch_id = ""
            if applied.new_records:
                visible = applied.new_records[:10]
                self.scanner.enrich_chat_links(visible)
                for record in visible:
                    self.store.update_product(task_id, record)
                quiet_end = (
                    quiet_until(current, task.quiet_start, task.quiet_end)
                    if task.quiet_enabled
                    else None
                )
                available = quiet_end or current
                merge_key = f"quiet:{available.isoformat(timespec='seconds')}" if quiet_end else ""
                batch = NotificationBatch(
                    task_id=task_id,
                    task_name=task.name,
                    provider_id=self.store.get_active_provider(),
                    items=visible,
                    total_count=len(applied.new_records),
                    created_at=current.isoformat(timespec="seconds"),
                    available_at=available.isoformat(timespec="seconds"),
                    merge_key=merge_key,
                )
                batch_id = self.store.enqueue_batch(batch).batch_id
                self.on_log(f"任务“{task.name}”发现 {len(applied.new_records)} 件新品，已进入通知队列。")
            elif applied.baseline_created:
                self.on_log(f"任务“{task.name}”首次扫描完成，已静默建立基线。")
            next_due = next_run_after(
                datetime.fromisoformat(state.next_run_at) if state.next_run_at else None,
                current,
                task.interval_minutes,
            )
            self.store.update_task_runtime(
                task_id,
                status="waiting" if task.enabled else "paused",
                last_run_at=current.isoformat(timespec="seconds"),
                next_run_at=next_due.isoformat(timespec="seconds"),
            )
            return MonitorRunResult(
                task_id,
                applied.baseline_created,
                len(applied.new_records),
                applied.seen_count,
                batch_id,
            )
        except Exception as exc:
            next_due = current + timedelta(minutes=task.interval_minutes)
            self.store.update_task_runtime(
                task_id,
                status="error",
                last_run_at=current.isoformat(timespec="seconds"),
                next_run_at=next_due.isoformat(timespec="seconds"),
                last_error=str(exc),
            )
            raise


class NotificationDispatcher:
    def __init__(self, store: MonitorStore) -> None:
        self.store = store
        self._providers: dict[str, NotificationProvider] = {}

    def provider(self, provider_id: str) -> NotificationProvider:
        provider = self._providers.get(provider_id)
        if provider is not None:
            return provider
        if provider_id == "feishu":
            provider = FeishuProvider(self.store.load_feishu_config())
        elif provider_id == "wxpusher":
            provider = WxPusherProvider(self.store.load_wxpusher_config())
        else:
            raise ValueError("不支持的通知通道")
        self._providers[provider_id] = provider
        return provider

    def invalidate(self, provider_id: str | None = None) -> None:
        if provider_id is None:
            self._providers.clear()
        else:
            self._providers.pop(provider_id, None)

    def deliver_due(self, *, now: datetime | None = None) -> list[tuple[NotificationBatch, object]]:
        current = now or datetime.now()
        outcomes = []
        for batch in self.store.due_batches(current):
            result = self.provider(batch.provider_id).send_batch(batch)
            outcomes.append((batch, result))
            if result.success:
                self.store.mark_batch_sent(batch.batch_id, sent_at=current)
            else:
                self.store.record_delivery_failure(batch.batch_id, result.message, now=current)
        return outcomes

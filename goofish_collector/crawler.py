from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol, TypeVar

from .checkpoint import Checkpoint, CheckpointStore
from .models import CrawlConfig, CrawlProgress, ProductRecord, RecordCollection, SearchFilters
from .parser import CardPayload, parse_card


class ManualVerificationRequired(RuntimeError):
    """页面要求用户手动登录或完成安全验证。"""


class SearchSession(Protocol):
    def open_search(self, keyword: str) -> None: ...

    def apply_filters(self, filters: SearchFilters) -> None: ...

    def extract_cards(self) -> list[CardPayload]: ...

    def goto_next_page(self) -> bool: ...


class RunControl:
    def __init__(self) -> None:
        self._running = threading.Event()
        self._running.set()
        self._stopped = threading.Event()

    @property
    def is_stopped(self) -> bool:
        return self._stopped.is_set()

    @property
    def is_paused(self) -> bool:
        return not self._running.is_set() and not self.is_stopped

    def pause(self) -> None:
        if not self.is_stopped:
            self._running.clear()

    def resume(self) -> None:
        if not self.is_stopped:
            self._running.set()

    def stop(self) -> None:
        self._stopped.set()
        self._running.set()

    def wait_until_running(self) -> bool:
        while not self._running.wait(timeout=0.2):
            if self.is_stopped:
                return False
        return not self.is_stopped


@dataclass(frozen=True)
class CrawlResult:
    records: list[ProductRecord]
    raw_records: int
    searched_pages: int
    stop_reason: str
    started_at: datetime
    finished_at: datetime


T = TypeVar("T")


def brief_error(error: Exception) -> str:
    for line in str(error).splitlines():
        if line.strip():
            return line.strip()
    return error.__class__.__name__


class CrawlEngine:
    def __init__(
        self,
        *,
        checkpoint_store: CheckpointStore,
        control: RunControl,
        on_progress: Callable[[CrawlProgress], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_verification: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.checkpoint_store = checkpoint_store
        self.control = control
        self.on_progress = on_progress or (lambda _: None)
        self.on_log = on_log or (lambda _: None)
        self.on_verification = on_verification or (lambda _: None)
        self.sleep = sleep

    def _call_with_retries(self, operation: Callable[[], T], label: str) -> T:
        failures = 0
        while True:
            if not self.control.wait_until_running():
                raise InterruptedError("用户停止")
            try:
                return operation()
            except ManualVerificationRequired as exc:
                self.control.pause()
                self.on_verification(str(exc))
                self.on_log("检测到登录或安全验证，已暂停，处理完成后点击“继续”。")
                if not self.control.wait_until_running():
                    raise InterruptedError("用户停止") from exc
            except Exception as exc:
                if failures >= 2:
                    raise
                delay = (2.0, 5.0)[failures]
                failures += 1
                self.on_log(
                    f"{label}失败，{delay:g} 秒后重试（{failures}/2）："
                    f"{brief_error(exc)}"
                )
                self.sleep(delay)

    def _save(
        self,
        config: CrawlConfig,
        current_page: int,
        raw_records: int,
        status: str,
        collection: RecordCollection,
    ) -> None:
        self.checkpoint_store.save(
            Checkpoint(
                config=config,
                current_page=current_page,
                raw_records=raw_records,
                status=status,
                records=collection.records,
            )
        )

    def _result(
        self,
        collection: RecordCollection,
        raw_records: int,
        searched_pages: int,
        reason: str,
        started_at: datetime,
    ) -> CrawlResult:
        return CrawlResult(
            records=collection.records,
            raw_records=raw_records,
            searched_pages=searched_pages,
            stop_reason=reason,
            started_at=started_at,
            finished_at=datetime.now(),
        )

    def run(
        self,
        config: CrawlConfig,
        session: SearchSession,
        *,
        resume: Checkpoint | None = None,
    ) -> CrawlResult:
        started_at = datetime.now()
        if self.control.is_stopped:
            return self._result(RecordCollection(), 0, 0, "用户停止", started_at)
        if resume is not None and resume.config != config:
            raise ValueError("检查点配置与当前任务不一致")

        collection = RecordCollection(resume.records if resume else ())
        raw_records = resume.raw_records if resume else 0
        completed_pages = resume.current_page if resume else 0
        current_page = completed_pages + 1

        try:
            self.on_log(f"正在打开关键词“{config.keyword}”的搜索页。")
            self._call_with_retries(lambda: session.open_search(config.keyword), "打开搜索页")
            if config.filters.is_active:
                self.on_log(
                    "正在应用搜索筛选："
                    f"价格 {config.filters.price_label()}，"
                    f"地区 {config.filters.region or '全国'}，"
                    f"其他 {config.filters.other_label()}。"
                )
            self._call_with_retries(
                lambda: session.apply_filters(config.filters), "应用搜索筛选"
            )

            if completed_pages:
                self.on_log(f"正在定位到检查点后的第 {current_page} 页。")
                for skipped_page in range(1, completed_pages + 1):
                    has_next = self._call_with_retries(session.goto_next_page, "定位检查点")
                    if not has_next:
                        reason = "检查点已超过当前搜索末页"
                        self._save(config, completed_pages, raw_records, "stopped", collection)
                        return self._result(collection, raw_records, completed_pages, reason, started_at)
                    self.on_progress(
                        CrawlProgress(
                            current_page=skipped_page,
                            raw_records=raw_records,
                            unique_records=len(collection),
                            status="resuming",
                            message="正在跳过已完成页面",
                        )
                    )

            while current_page <= config.max_pages:
                if not self.control.wait_until_running():
                    reason = "用户停止"
                    self._save(config, completed_pages, raw_records, "stopped", collection)
                    return self._result(collection, raw_records, completed_pages, reason, started_at)

                payloads = self._call_with_retries(session.extract_cards, f"读取第 {current_page} 页")
                raw_records += len(payloads)
                captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                reached_item_limit = False
                for payload in payloads:
                    record = parse_card(
                        payload,
                        keyword=config.keyword,
                        page=current_page,
                        captured_at=captured_at,
                    )
                    if record is None:
                        continue
                    is_new = collection.add(record)
                    if is_new and config.max_items and len(collection) >= config.max_items:
                        reached_item_limit = True
                        break

                completed_pages = current_page
                self._save(config, completed_pages, raw_records, "running", collection)
                self.on_progress(
                    CrawlProgress(
                        current_page=current_page,
                        raw_records=raw_records,
                        unique_records=len(collection),
                        status="running",
                        message=f"第 {current_page} 页采集完成",
                    )
                )
                self.on_log(
                    f"第 {current_page} 页：读取 {len(payloads)} 条，累计唯一商品 {len(collection)} 条。"
                )

                if reached_item_limit:
                    reason = f"已达到最大商品数 {config.max_items}"
                    self._save(config, completed_pages, raw_records, "completed", collection)
                    return self._result(collection, raw_records, completed_pages, reason, started_at)
                if self.control.is_stopped:
                    reason = "用户停止"
                    self._save(config, completed_pages, raw_records, "stopped", collection)
                    return self._result(collection, raw_records, completed_pages, reason, started_at)
                if current_page >= config.max_pages:
                    reason = f"已达到最大页数 {config.max_pages}"
                    self._save(config, completed_pages, raw_records, "completed", collection)
                    return self._result(collection, raw_records, completed_pages, reason, started_at)

                has_next = self._call_with_retries(session.goto_next_page, "翻到下一页")
                if not has_next:
                    reason = "已到末页"
                    self._save(config, completed_pages, raw_records, "completed", collection)
                    return self._result(collection, raw_records, completed_pages, reason, started_at)
                current_page += 1
                self.sleep(2.0)

        except InterruptedError:
            reason = "用户停止"
        except Exception as exc:
            reason = f"运行错误：{exc}"
            self.on_log(reason)

        self._save(config, completed_pages, raw_records, "error" if reason.startswith("运行错误") else "stopped", collection)
        return self._result(collection, raw_records, completed_pages, reason, started_at)

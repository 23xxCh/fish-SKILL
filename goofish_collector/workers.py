from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .browser import LOGIN_URL, GoofishBrowserSession, default_profile_dir, run_login_browser
from .checkpoint import CheckpointStore
from .crawler import CrawlEngine, RunControl
from .exporter import export_workbook
from .models import CrawlConfig, CrawlProgress, ProductRecord
from .monitor_models import NotificationBatch
from .monitor_store import MonitorStore
from .monitoring import BrowserMonitorScanner, MonitorCoordinator, NotificationDispatcher
from .notifications import FeishuProvider, WxPusherProvider


class LoginWorker(QThread):
    log = Signal(str)
    failed = Signal(str)

    def __init__(self, profile_dir: Path | None = None) -> None:
        super().__init__()
        self.profile_dir = profile_dir or default_profile_dir()

    def run(self) -> None:
        try:
            run_login_browser(
                self.profile_dir,
                on_log=self.log.emit,
                stop_requested=self.isInterruptionRequested,
            )
        except Exception as exc:
            self.failed.emit(str(exc))

    def stop(self) -> None:
        self.requestInterruption()


class CrawlWorker(QThread):
    progress = Signal(object)
    page_records = Signal(object)
    log = Signal(str)
    verification = Signal(str)
    succeeded = Signal(str, str, int)
    failed = Signal(str)

    def __init__(
        self,
        config: CrawlConfig,
        *,
        resume: bool,
        profile_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.resume_requested = resume
        self.profile_dir = profile_dir or default_profile_dir()
        self.control = RunControl()

    def run(self) -> None:
        try:
            store = CheckpointStore.for_config(self.config)
            checkpoint = store.load() if self.resume_requested else None
            engine = CrawlEngine(
                checkpoint_store=store,
                control=self.control,
                on_progress=self._emit_progress,
                on_page_records=self.page_records.emit,
                on_log=self.log.emit,
                on_verification=self.verification.emit,
            )
            with GoofishBrowserSession(self.profile_dir, on_log=self.log.emit) as session:
                result = engine.run(self.config, session, resume=checkpoint)

            output = export_workbook(
                config=self.config,
                records=result.records,
                raw_records=result.raw_records,
                searched_pages=result.searched_pages,
                stop_reason=result.stop_reason,
                started_at=result.started_at,
                finished_at=result.finished_at,
            )
            if not result.stop_reason.startswith(("用户停止", "运行错误")):
                store.delete()
            self.succeeded.emit(str(output), result.stop_reason, len(result.records))
        except Exception as exc:
            self.failed.emit(str(exc))

    def _emit_progress(self, progress: CrawlProgress) -> None:
        self.progress.emit(progress)

    def pause(self) -> None:
        self.control.pause()

    def resume(self) -> None:
        self.control.resume()

    def stop(self) -> None:
        self.control.stop()


class NotificationTestWorker(QThread):
    completed = Signal(bool, str)

    def __init__(self, provider) -> None:
        super().__init__()
        self.provider = provider

    def run(self) -> None:
        result = self.provider.send_test()
        self.completed.emit(result.success, result.message)


class MonitorSchedulerWorker(QThread):
    log = Signal(str)
    task_updated = Signal(str)
    verification = Signal(str)
    delivery = Signal(str, bool, str)

    def __init__(self, store: MonitorStore, *, profile_dir: Path | None = None) -> None:
        super().__init__()
        self.store = store
        self.profile_dir = profile_dir or default_profile_dir()
        self._wake = threading.Event()
        self._immediate: list[str] = []
        self._lock = threading.Lock()
        self._verification_alerted: set[str] = set()

    def request_scan(self, task_id: str) -> None:
        with self._lock:
            if task_id not in self._immediate:
                self._immediate.append(task_id)
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self.requestInterruption()
        self._wake.set()

    def _take_immediate(self) -> list[str]:
        with self._lock:
            task_ids = list(self._immediate)
            self._immediate.clear()
        return task_ids

    def run(self) -> None:
        session = None
        try:
            while not self.isInterruptionRequested():
                now = datetime.now()
                immediate = self._take_immediate()
                due = [state.config.task_id for state in self.store.due_task_states(now)]
                task_ids = list(dict.fromkeys(immediate + due))
                if task_ids and session is None:
                    session = GoofishBrowserSession(
                        self.profile_dir, minimized=True, on_log=self.log.emit
                    )
                    session.__enter__()
                    self.log.emit("新品监控已启动共用的闲鱼专用 Edge 会话。")
                if session is not None:
                    scanner = BrowserMonitorScanner(session, on_log=self.log.emit)
                    coordinator = MonitorCoordinator(self.store, scanner, on_log=self.log.emit)
                    for task_id in task_ids:
                        if self.isInterruptionRequested():
                            break
                        try:
                            coordinator.run_task(task_id, now=datetime.now())
                            self._verification_alerted.discard(task_id)
                            self.task_updated.emit(task_id)
                        except Exception as exc:
                            message = str(exc)
                            if "验证" in message or "登录" in message:
                                try:
                                    session.page.bring_to_front()
                                except Exception:
                                    pass
                                if task_id not in self._verification_alerted:
                                    try:
                                        task = self.store.get_task_state(task_id).config
                                        alert = ProductRecord(
                                            keyword=task.keyword,
                                            item_id="",
                                            title="闲鱼登录已失效，请回到电脑完成人工登录或验证",
                                            url=LOGIN_URL,
                                        )
                                        self.store.enqueue_batch(
                                            NotificationBatch(
                                                task_id=task_id,
                                                task_name=f"{task.name} · 需要人工处理",
                                                provider_id=self.store.get_active_provider(),
                                                items=[alert],
                                                total_count=1,
                                            )
                                        )
                                        self._verification_alerted.add(task_id)
                                    except Exception as alert_exc:
                                        self.log.emit(f"登录失效手机提醒入队失败：{alert_exc}")
                                self.verification.emit(task_id)
                            self.log.emit(f"监控任务运行失败：{message}")
                            self.task_updated.emit(task_id)
                try:
                    outcomes = NotificationDispatcher(self.store).deliver_due(now=datetime.now())
                    for batch, result in outcomes:
                        self.delivery.emit(batch.batch_id, result.success, result.message)
                        if result.success:
                            self.log.emit(f"任务“{batch.task_name}”新品通知发送成功。")
                        else:
                            self.log.emit(f"任务“{batch.task_name}”通知失败：{result.message}")
                except Exception as exc:
                    self.log.emit(f"处理通知队列失败：{exc}")
                self._wake.wait(timeout=1.0)
                self._wake.clear()
        except Exception as exc:
            self.log.emit(f"新品监控线程停止：{exc}")
        finally:
            if session is not None:
                session.__exit__(None, None, None)

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .browser import GoofishBrowserSession, default_profile_dir, run_login_browser
from .checkpoint import CheckpointStore
from .crawler import CrawlEngine, RunControl
from .exporter import export_workbook
from .models import CrawlConfig, CrawlProgress, ProductRecord
from .monitor_models import NotificationBatch
from .notifications import FeishuProvider
from .updater import PreparedUpdate, UpdateInfo, UpdateService


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
    succeeded = Signal(str, str, int, object)
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
            records = [ProductRecord.from_dict(record.to_dict()) for record in result.records]
            self.succeeded.emit(str(output), result.stop_reason, len(records), records)
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


class NotificationDeliveryWorker(QThread):
    completed = Signal(bool, str)

    def __init__(self, provider: FeishuProvider, batch: NotificationBatch) -> None:
        super().__init__()
        self.provider = provider
        self.batch = batch

    def run(self) -> None:
        result = self.provider.send_batch(self.batch)
        self.completed.emit(result.success, result.message)


class NotificationTextWorker(QThread):
    completed = Signal(bool, str)

    def __init__(self, provider: FeishuProvider, text: str) -> None:
        super().__init__()
        self.provider = provider
        self.text = text

    def run(self) -> None:
        result = self.provider.send_text(self.provider.config.open_id, self.text)
        self.completed.emit(result.success, result.message)


class UpdateCheckWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, service: UpdateService) -> None:
        super().__init__()
        self.service = service

    def run(self) -> None:
        try:
            self.completed.emit(self.service.check_for_update())
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdatePreparationWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, service: UpdateService, update: UpdateInfo, update_root: Path) -> None:
        super().__init__()
        self.service = service
        self.update = update
        self.update_root = update_root

    def run(self) -> None:
        try:
            self.completed.emit(self.service.prepare_update(self.update, self.update_root))
        except Exception as exc:
            self.failed.emit(str(exc))

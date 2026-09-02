from __future__ import annotations

import json
import multiprocessing
import queue
import time
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QThread, Signal

from .monitor_models import FeishuConfig
from .monitor_store import MonitorStore
from .notifications import FeishuProvider


def _value(root: Any, *names: str) -> Any:
    current = root
    for name in names:
        if isinstance(current, dict):
            current = current.get(name)
        else:
            current = getattr(current, name, None)
        if current is None:
            return None
    return current


def parse_binding_event(data: Any) -> tuple[str, str]:
    open_id = _value(data, "event", "sender", "sender_id", "open_id") or ""
    content = _value(data, "event", "message", "content") or ""
    try:
        decoded = json.loads(content) if isinstance(content, str) else content
        text = str((decoded or {}).get("text") or "").strip()
    except (TypeError, ValueError, AttributeError):
        text = ""
    return str(open_id), text


def _binding_process(app_id: str, app_secret: str, result_queue) -> None:
    import lark_oapi as lark

    def on_message(data) -> None:
        open_id, text = parse_binding_event(data)
        if open_id and text == "绑定":
            result_queue.put(("bound", open_id))

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    client = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.WARNING,
    )
    try:
        client.start()
    except Exception as exc:
        result_queue.put(("error", str(exc)))


class FeishuBindingWorker(QThread):
    bound = Signal(str)
    failed = Signal(str)
    expired = Signal()

    def __init__(
        self,
        store: MonitorStore,
        config: FeishuConfig,
        *,
        timeout_seconds: int = 300,
    ) -> None:
        super().__init__()
        self.store = store
        self.config = config
        self.timeout_seconds = timeout_seconds
        self._process = None

    def run(self) -> None:
        if not self.config.app_id.strip() or not self.config.app_secret.strip():
            self.failed.emit("请先填写并保存飞书 App ID 和 App Secret")
            return
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        self._process = context.Process(
            target=_binding_process,
            args=(self.config.app_id, self.config.app_secret, result_queue),
            daemon=True,
        )
        self._process.start()
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while time.monotonic() < deadline and not self.isInterruptionRequested():
                try:
                    kind, value = result_queue.get(timeout=0.2)
                except queue.Empty:
                    if not self._process.is_alive():
                        self.failed.emit("飞书长连接已结束，请检查应用版本、权限和事件订阅配置")
                        return
                    continue
                if kind == "error":
                    self.failed.emit(value)
                    return
                latest = self.store.load_feishu_config()
                if latest.open_id and latest.open_id != value:
                    self.failed.emit("这台电脑已绑定其他用户，必须先在设置页解绑")
                    return
                saved = replace(latest, open_id=value)
                self.store.save_feishu_config(saved)
                result = FeishuProvider(saved).send_text(value, "绑定成功")
                if not result.success:
                    self.failed.emit(f"已收到绑定消息，但回复失败：{result.message}")
                    return
                self.bound.emit(value)
                return
            if not self.isInterruptionRequested():
                self.expired.emit()
        finally:
            if self._process is not None and self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=3)
            self._process = None

    def stop(self) -> None:
        self.requestInterruption()
        if self._process is not None and self._process.is_alive():
            self._process.terminate()

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from .models import ProductRecord, ScheduledCollectionConfig
from .monitor_models import (
    FeishuConfig,
    MonitorTaskConfig,
    MonitorTaskState,
    NotificationBatch,
    ScanApplyResult,
    WxPusherConfig,
)
from .secure_data import protect_text, unprotect_text


def default_monitor_db_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path.home() / "AppData" / "Local"
    return root / "GoofishLinkCollector" / "monitor.db"


class MonitorStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or default_monitor_db_path()).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    rule_fingerprint TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 1,
                    baseline_ready INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'paused',
                    last_run_at TEXT NOT NULL DEFAULT '',
                    next_run_at TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS products (
                    task_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    item_key TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    notified_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (task_id, generation, item_key),
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS scans (
                    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    scanned_at TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    new_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    batch_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    batch_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    last_error TEXT NOT NULL DEFAULT '',
                    merge_key TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_due
                    ON outbox(status, available_at);
                """
            )

    @staticmethod
    def _task_state(row: sqlite3.Row) -> MonitorTaskState:
        return MonitorTaskState(
            config=MonitorTaskConfig.from_dict(json.loads(row["config_json"])),
            generation=int(row["generation"]),
            baseline_ready=bool(row["baseline_ready"]),
            status=row["status"],
            last_run_at=row["last_run_at"],
            next_run_at=row["next_run_at"],
            last_error=row["last_error"],
        )

    def save_task(self, config: MonitorTaskConfig) -> MonitorTaskState:
        payload = json.dumps(config.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (config.task_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO tasks(task_id, config_json, rule_fingerprint, status) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        config.task_id,
                        payload,
                        config.rule_fingerprint,
                        "waiting" if config.enabled else "paused",
                    ),
                )
            else:
                rule_changed = row["rule_fingerprint"] != config.rule_fingerprint
                generation = int(row["generation"]) + (1 if rule_changed else 0)
                baseline_ready = 0 if rule_changed else int(row["baseline_ready"])
                status = "waiting" if config.enabled else "paused"
                connection.execute(
                    "UPDATE tasks SET config_json=?, rule_fingerprint=?, generation=?, "
                    "baseline_ready=?, status=?, last_error='' WHERE task_id=?",
                    (
                        payload,
                        config.rule_fingerprint,
                        generation,
                        baseline_ready,
                        status,
                        config.task_id,
                    ),
                )
            saved = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (config.task_id,)
            ).fetchone()
        return self._task_state(saved)

    def get_task_state(self, task_id: str) -> MonitorTaskState:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"监控任务不存在：{task_id}")
        return self._task_state(row)

    def list_task_states(self) -> list[MonitorTaskState]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM tasks ORDER BY rowid").fetchall()
        return [self._task_state(row) for row in rows]

    def delete_task(self, task_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM outbox WHERE task_id=?", (task_id,))
            connection.execute("DELETE FROM scans WHERE task_id=?", (task_id,))
            connection.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))

    def set_task_enabled(self, task_id: str, enabled: bool) -> MonitorTaskState:
        state = self.get_task_state(task_id)
        return self.save_task(replace(state.config, enabled=enabled))

    def update_task_runtime(
        self,
        task_id: str,
        *,
        status: str,
        last_run_at: str | None = None,
        next_run_at: str | None = None,
        last_error: str = "",
    ) -> None:
        assignments = ["status=?", "last_error=?"]
        values: list[object] = [status, last_error]
        if last_run_at is not None:
            assignments.append("last_run_at=?")
            values.append(last_run_at)
        if next_run_at is not None:
            assignments.append("next_run_at=?")
            values.append(next_run_at)
        values.append(task_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE task_id=?", values
            )

    def due_task_states(self, now: datetime) -> list[MonitorTaskState]:
        now_text = now.isoformat(timespec="seconds")
        return [
            state
            for state in self.list_task_states()
            if state.config.enabled and (not state.next_run_at or state.next_run_at <= now_text)
        ]

    def record_scan(
        self,
        task_id: str,
        records: list[ProductRecord],
        *,
        scanned_at: datetime | None = None,
    ) -> ScanApplyResult:
        now = scanned_at or datetime.now()
        now_text = now.isoformat(timespec="seconds")
        with self._connect() as connection:
            task = connection.execute(
                "SELECT generation, baseline_ready FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if task is None:
                raise KeyError(f"监控任务不存在：{task_id}")
            generation = int(task["generation"])
            baseline_created = not bool(task["baseline_ready"])
            new_records: list[ProductRecord] = []
            for record in records:
                existing = connection.execute(
                    "SELECT record_json, first_seen_at, notified_at FROM products "
                    "WHERE task_id=? AND generation=? AND item_key=?",
                    (task_id, generation, record.key),
                ).fetchone()
                if existing is None:
                    if not record.first_seen_at:
                        record.first_seen_at = now_text.replace("T", " ")
                    connection.execute(
                        "INSERT INTO products(task_id,generation,item_key,record_json,first_seen_at,notified_at) "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            task_id,
                            generation,
                            record.key,
                            json.dumps(record.to_dict(), ensure_ascii=False),
                            record.first_seen_at,
                            record.notified_at,
                        ),
                    )
                    if not baseline_created:
                        new_records.append(record)
                else:
                    old = ProductRecord.from_dict(json.loads(existing["record_json"]))
                    old.merge(record)
                    old.first_seen_at = existing["first_seen_at"]
                    old.notified_at = existing["notified_at"]
                    connection.execute(
                        "UPDATE products SET record_json=? WHERE task_id=? AND generation=? AND item_key=?",
                        (
                            json.dumps(old.to_dict(), ensure_ascii=False),
                            task_id,
                            generation,
                            record.key,
                        ),
                    )
            if baseline_created:
                connection.execute(
                    "UPDATE tasks SET baseline_ready=1 WHERE task_id=?", (task_id,)
                )
            connection.execute(
                "INSERT INTO scans(task_id,scanned_at,item_count,new_count,status) VALUES(?,?,?,?,?)",
                (task_id, now_text, len(records), len(new_records), "success"),
            )
        return ScanApplyResult(baseline_created, new_records, len(records))

    def update_product(self, task_id: str, record: ProductRecord) -> None:
        state = self.get_task_state(task_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE products SET record_json=? WHERE task_id=? AND generation=? AND item_key=?",
                (
                    json.dumps(record.to_dict(), ensure_ascii=False),
                    task_id,
                    state.generation,
                    record.key,
                ),
            )

    def list_products(self, task_id: str, *, all_generations: bool = False) -> list[ProductRecord]:
        state = self.get_task_state(task_id)
        with self._connect() as connection:
            if all_generations:
                rows = connection.execute(
                    "SELECT record_json FROM products WHERE task_id=? ORDER BY rowid", (task_id,)
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT record_json FROM products WHERE task_id=? AND generation=? ORDER BY rowid",
                    (task_id, state.generation),
                ).fetchall()
        records = [ProductRecord.from_dict(json.loads(row["record_json"])) for row in rows]
        if not all_generations:
            return records
        unique: dict[str, ProductRecord] = {}
        for record in records:
            existing = unique.get(record.key)
            if existing is None:
                unique[record.key] = record
            else:
                original_first_seen = existing.first_seen_at
                existing.merge(record)
                existing.first_seen_at = original_first_seen or record.first_seen_at
        return list(unique.values())

    def _set_setting(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO settings(setting_key,setting_value) VALUES(?,?) "
                "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
                (key, value),
            )

    def _get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT setting_value FROM settings WHERE setting_key=?", (key,)
            ).fetchone()
        return row[0] if row else default

    def set_active_provider(self, provider_id: str) -> None:
        if provider_id not in ("feishu", "wxpusher"):
            raise ValueError("不支持的通知通道")
        self._set_setting("active_provider", provider_id)

    def get_active_provider(self) -> str:
        return self._get_setting("active_provider", "feishu")

    def save_feishu_config(self, config: FeishuConfig) -> None:
        self._set_setting("feishu_app_id", config.app_id.strip())
        self._set_setting("feishu_app_secret", protect_text(config.app_secret.strip()))
        self._set_setting("feishu_open_id", protect_text(config.open_id.strip()))

    def load_feishu_config(self) -> FeishuConfig:
        return FeishuConfig(
            app_id=self._get_setting("feishu_app_id"),
            app_secret=unprotect_text(self._get_setting("feishu_app_secret")),
            open_id=unprotect_text(self._get_setting("feishu_open_id")),
        )

    def save_scheduled_collection(self, config: ScheduledCollectionConfig) -> None:
        self._set_setting(
            "scheduled_collection",
            json.dumps(config.to_dict(), ensure_ascii=False, sort_keys=True),
        )

    def load_scheduled_collection(self) -> ScheduledCollectionConfig | None:
        raw = self._get_setting("scheduled_collection")
        if not raw:
            return None
        try:
            return ScheduledCollectionConfig.from_dict(json.loads(raw))
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            return None

    def save_update_check_at(self, checked_at: datetime) -> None:
        self._set_setting("update_check_at", checked_at.isoformat(timespec="seconds"))

    def load_update_check_at(self) -> datetime | None:
        value = self._get_setting("update_check_at")
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def save_wxpusher_config(self, config: WxPusherConfig) -> None:
        self._set_setting("wxpusher_spt", protect_text(config.spt.strip()))

    def load_wxpusher_config(self) -> WxPusherConfig:
        return WxPusherConfig(spt=unprotect_text(self._get_setting("wxpusher_spt")))

    @staticmethod
    def _batch(row: sqlite3.Row) -> NotificationBatch:
        batch = NotificationBatch.from_dict(json.loads(row["batch_json"]))
        batch.status = row["status"]
        batch.attempts = int(row["attempts"])
        batch.available_at = row["available_at"]
        batch.last_error = row["last_error"]
        return batch

    def enqueue_batch(self, batch: NotificationBatch) -> NotificationBatch:
        with self._connect() as connection:
            existing = None
            if batch.merge_key:
                existing = connection.execute(
                    "SELECT * FROM outbox WHERE task_id=? AND provider_id=? AND merge_key=? "
                    "AND status='pending' ORDER BY rowid DESC LIMIT 1",
                    (batch.task_id, batch.provider_id, batch.merge_key),
                ).fetchone()
            if existing is not None:
                current = self._batch(existing)
                known = {item.key for item in current.items}
                current.items.extend(item for item in batch.items if item.key not in known)
                current.items = current.items[:10]
                current.total_count += batch.total_count
                connection.execute(
                    "UPDATE outbox SET batch_json=? WHERE batch_id=?",
                    (json.dumps(current.to_dict(), ensure_ascii=False), current.batch_id),
                )
                return current
            connection.execute(
                "INSERT INTO outbox(batch_id,task_id,provider_id,batch_json,status,attempts,"
                "available_at,last_error,merge_key) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    batch.batch_id,
                    batch.task_id,
                    batch.provider_id,
                    json.dumps(batch.to_dict(), ensure_ascii=False),
                    batch.status,
                    batch.attempts,
                    batch.available_at,
                    batch.last_error,
                    batch.merge_key,
                ),
            )
        return batch

    def get_batch(self, batch_id: str) -> NotificationBatch:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM outbox WHERE batch_id=?", (batch_id,)).fetchone()
        if row is None:
            raise KeyError(f"通知批次不存在：{batch_id}")
        return self._batch(row)

    def list_batches(self, *, status: str | None = None) -> list[NotificationBatch]:
        with self._connect() as connection:
            if status is None:
                rows = connection.execute("SELECT * FROM outbox ORDER BY rowid").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM outbox WHERE status=? ORDER BY rowid", (status,)
                ).fetchall()
        return [self._batch(row) for row in rows]

    def due_batches(self, now: datetime) -> list[NotificationBatch]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM outbox WHERE status='pending' AND available_at<=? ORDER BY rowid",
                (now.isoformat(timespec="seconds"),),
            ).fetchall()
        return [self._batch(row) for row in rows]

    def record_delivery_failure(
        self, batch_id: str, error: str, *, now: datetime | None = None
    ) -> NotificationBatch:
        batch = self.get_batch(batch_id)
        attempts = batch.attempts + 1
        retry_delays = (5, 30, 120)
        if attempts <= len(retry_delays):
            available = (now or datetime.now()) + timedelta(seconds=retry_delays[attempts - 1])
            status = "pending"
        else:
            available = now or datetime.now()
            status = "failed"
        batch.attempts = attempts
        batch.available_at = available.isoformat(timespec="seconds")
        batch.status = status
        batch.last_error = error
        with self._connect() as connection:
            connection.execute(
                "UPDATE outbox SET status=?,attempts=?,available_at=?,last_error=?,batch_json=? "
                "WHERE batch_id=?",
                (
                    status,
                    attempts,
                    batch.available_at,
                    error,
                    json.dumps(batch.to_dict(), ensure_ascii=False),
                    batch_id,
                ),
            )
        return batch

    def mark_batch_sent(self, batch_id: str, *, sent_at: datetime | None = None) -> None:
        batch = self.get_batch(batch_id)
        stamp = (sent_at or datetime.now()).isoformat(timespec="seconds").replace("T", " ")
        state = self.get_task_state(batch.task_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE outbox SET status='sent',last_error='' WHERE batch_id=?", (batch_id,)
            )
            for item in batch.items:
                row = connection.execute(
                    "SELECT record_json FROM products WHERE task_id=? AND generation=? AND item_key=?",
                    (batch.task_id, state.generation, item.key),
                ).fetchone()
                if row is None:
                    continue
                record = ProductRecord.from_dict(json.loads(row["record_json"]))
                record.notified_at = stamp
                connection.execute(
                    "UPDATE products SET notified_at=?,record_json=? WHERE task_id=? AND generation=? AND item_key=?",
                    (
                        stamp,
                        json.dumps(record.to_dict(), ensure_ascii=False),
                        batch.task_id,
                        state.generation,
                        item.key,
                    ),
                )

    def retry_with_current_provider(self, batch_id: str) -> NotificationBatch:
        batch = self.get_batch(batch_id)
        batch.provider_id = self.get_active_provider()
        batch.status = "pending"
        batch.attempts = 0
        batch.last_error = ""
        batch.available_at = datetime.now().isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                "UPDATE outbox SET provider_id=?,status='pending',attempts=0,last_error='',"
                "available_at=?,batch_json=? WHERE batch_id=?",
                (
                    batch.provider_id,
                    batch.available_at,
                    json.dumps(batch.to_dict(), ensure_ascii=False),
                    batch.batch_id,
                ),
            )
        return batch

from datetime import datetime
from pathlib import Path

from goofish_collector.models import (
    ProductRecord,
    PushRules,
    ScheduledCollectionHealth,
    SearchFilters,
)
from goofish_collector.monitor_models import (
    FeishuConfig,
    MonitorTaskConfig,
    NotificationBatch,
    WxPusherConfig,
)
from goofish_collector.monitor_store import MonitorStore


def _record(item_id: str) -> ProductRecord:
    return ProductRecord(
        keyword="耳机",
        item_id=item_id,
        title=f"商品 {item_id}",
        url=f"https://www.goofish.com/item?id={item_id}",
        captured_at="2026-08-04 10:00:00",
    )


def test_first_scan_is_silent_and_later_scan_returns_only_new(tmp_path: Path) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    task = MonitorTaskConfig(name="耳机新品", keyword="耳机")
    store.save_task(task)

    first = store.record_scan(task.task_id, [_record("1"), _record("2")])
    second = store.record_scan(task.task_id, [_record("2"), _record("3")])
    third = store.record_scan(task.task_id, [_record("1"), _record("2"), _record("3")])

    assert first.baseline_created is True
    assert first.new_records == []
    assert [record.item_id for record in second.new_records] == ["3"]
    assert third.new_records == []


def test_rule_change_keeps_history_and_rebuilds_baseline(tmp_path: Path) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    original = MonitorTaskConfig(name="任务", keyword="耳机")
    store.save_task(original)
    store.record_scan(original.task_id, [_record("1")])

    changed = MonitorTaskConfig(
        task_id=original.task_id,
        name="任务",
        keyword="耳机",
        filters=SearchFilters(max_price=500),
    )
    state = store.save_task(changed)
    rebuilt = store.record_scan(changed.task_id, [_record("2")])

    assert state.generation == 2
    assert not state.baseline_ready
    assert rebuilt.baseline_created
    assert {row.item_id for row in store.list_products(changed.task_id, all_generations=True)} == {
        "1",
        "2",
    }


def test_provider_secrets_are_not_stored_as_plaintext(tmp_path: Path) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    store.save_feishu_config(FeishuConfig(app_id="cli_123", app_secret="secret-abc", open_id="ou_1"))
    store.save_wxpusher_config(WxPusherConfig(spt="SPT_private-token"))

    raw = (tmp_path / "monitor.db").read_bytes()
    assert b"secret-abc" not in raw
    assert b"SPT_private-token" not in raw
    assert b"ou_1" not in raw
    assert store.load_feishu_config() == FeishuConfig(
        app_id="cli_123", app_secret="secret-abc", open_id="ou_1"
    )
    assert store.load_wxpusher_config() == WxPusherConfig(spt="SPT_private-token")


def test_update_check_timestamp_is_local_state_and_tolerates_invalid_legacy_value(tmp_path: Path) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    checked_at = datetime(2026, 9, 3, 16, 30, 0)

    store.save_update_check_at(checked_at)

    assert store.load_update_check_at() == checked_at
    store._set_setting("update_check_at", "not-a-date")
    assert store.load_update_check_at() is None


def test_scheduled_delivery_changes_use_a_full_first_batch_then_only_new_or_repriced_items(
    tmp_path: Path,
) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    first = [_record("1"), _record("2")]
    first[1].price = 20

    assert [record.item_id for record in store.select_scheduled_delivery_records(first, changes_only=True)] == [
        "1",
        "2",
    ]

    later = [_record("1"), _record("2"), _record("3")]
    later[0].price = 99
    later[1].price = 20
    assert [record.item_id for record in store.select_scheduled_delivery_records(later, changes_only=True)] == [
        "1",
        "3",
    ]
    assert store.select_scheduled_delivery_records(later, changes_only=True) == []


def test_scheduled_delivery_full_mode_still_refreshes_the_change_baseline(tmp_path: Path) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    first = [_record("1")]
    first[0].price = 10

    assert store.select_scheduled_delivery_records(first, changes_only=False) == first
    assert store.select_scheduled_delivery_records(first, changes_only=True) == []


def test_clearing_scheduled_delivery_snapshot_makes_the_next_change_only_run_a_baseline(
    tmp_path: Path,
) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    records = [_record("1")]

    store.select_scheduled_delivery_records(records, changes_only=False)
    store.clear_scheduled_delivery_snapshot()

    assert store.select_scheduled_delivery_records(records, changes_only=True) == records


def test_scheduled_health_persists_without_notification_credentials(tmp_path: Path) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    health = ScheduledCollectionHealth(
        last_started_at="2026-09-03 17:00:00",
        last_succeeded_at="2026-09-03 17:01:00",
        last_item_count=12,
        last_output="C:/exports/result.xlsx",
        delivery_status="待重试（1）",
        pending_deliveries=1,
    )

    store.save_scheduled_health(health)

    assert store.load_scheduled_health() == health


def test_push_rules_persist_separately_from_feishu_credentials(tmp_path: Path) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    rules = PushRules(max_price=300, include_terms=("华为",), exclude_terms=("单耳",))

    store.save_push_rules(rules)

    assert store.load_push_rules() == rules


def test_scheduled_delivery_outbox_can_be_marked_sent_without_an_old_monitor_task(
    tmp_path: Path,
) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    batch = store.enqueue_batch(
        NotificationBatch(
            task_id="scheduled_collection",
            task_name="定时采集：耳机",
            provider_id="feishu",
            items=[_record("1")],
            total_count=1,
        )
    )

    store.mark_batch_sent(batch.batch_id)

    assert store.get_batch(batch.batch_id).status == "sent"


def test_outbox_preserves_provider_and_retries(tmp_path: Path) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    task = MonitorTaskConfig(name="任务", keyword="耳机")
    store.save_task(task)
    batch = NotificationBatch(
        task_id=task.task_id,
        task_name=task.name,
        provider_id="feishu",
        items=[_record("1")],
        total_count=1,
        created_at="2026-08-04T10:00:00",
        available_at="2026-08-04T10:00:00",
    )
    queued = store.enqueue_batch(batch)
    store.set_active_provider("wxpusher")

    assert store.get_batch(queued.batch_id).provider_id == "feishu"
    retry = store.record_delivery_failure(
        queued.batch_id, "temporary", now=datetime(2026, 8, 4, 10, 0, 0)
    )
    assert retry.attempts == 1
    assert retry.available_at == "2026-08-04T10:00:05"


def test_quiet_batches_for_same_task_are_coalesced(tmp_path: Path) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    task = MonitorTaskConfig(name="任务", keyword="耳机")
    store.save_task(task)
    first = NotificationBatch(
        task_id=task.task_id,
        task_name=task.name,
        provider_id="feishu",
        items=[_record("1")],
        total_count=1,
        available_at="2026-08-05T07:00:00",
        merge_key="quiet:2026-08-05T07:00:00",
    )
    second = NotificationBatch(
        task_id=task.task_id,
        task_name=task.name,
        provider_id="feishu",
        items=[_record("2")],
        total_count=1,
        available_at="2026-08-05T07:00:00",
        merge_key=first.merge_key,
    )

    batch_id = store.enqueue_batch(first).batch_id
    merged = store.enqueue_batch(second)

    assert merged.batch_id == batch_id
    assert [item.item_id for item in merged.items] == ["1", "2"]
    assert merged.total_count == 2


def test_restart_restores_tasks_products_and_outbox(tmp_path: Path) -> None:
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    task = MonitorTaskConfig(name="重启恢复", keyword="相机", enabled=True)
    store.save_task(task)
    store.record_scan(task.task_id, [_record("1")])
    queued = store.enqueue_batch(
        NotificationBatch(
            task_id=task.task_id,
            task_name=task.name,
            provider_id="wxpusher",
            items=[_record("2")],
            total_count=1,
        )
    )

    reopened = MonitorStore(path)

    assert reopened.get_task_state(task.task_id).config.enabled
    assert reopened.list_products(task.task_id)[0].item_id == "1"
    assert reopened.get_batch(queued.batch_id).provider_id == "wxpusher"


def test_failed_batch_can_be_requeued_with_current_provider(tmp_path: Path) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    task = MonitorTaskConfig(name="任务", keyword="耳机")
    store.save_task(task)
    batch = store.enqueue_batch(
        NotificationBatch(
            task_id=task.task_id,
            task_name=task.name,
            provider_id="feishu",
            items=[_record("1")],
            total_count=1,
        )
    )
    now = datetime(2026, 8, 4, 10, 0, 0)
    for _ in range(4):
        store.record_delivery_failure(batch.batch_id, "failed", now=now)
    assert store.get_batch(batch.batch_id).status == "failed"
    store.set_active_provider("wxpusher")

    retried = store.retry_with_current_provider(batch.batch_id)

    assert retried.status == "pending"
    assert retried.provider_id == "wxpusher"
    assert retried.attempts == 0

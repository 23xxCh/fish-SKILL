from datetime import datetime, timedelta
from pathlib import Path

from goofish_collector.models import ProductRecord
from goofish_collector.monitor_models import DeliveryResult, MonitorTaskConfig
from goofish_collector.monitor_store import MonitorStore
from goofish_collector.monitoring import MonitorCoordinator, NotificationDispatcher


def _records(ids: range) -> list[ProductRecord]:
    return [
        ProductRecord(
            keyword="相机",
            item_id=str(item_id),
            title=f"相机 {item_id}",
            url=f"https://www.goofish.com/item?id={item_id}",
        )
        for item_id in ids
    ]


class RoundScanner:
    def __init__(self, rounds: list[list[ProductRecord]]) -> None:
        self.rounds = rounds

    def scan(self, task: MonitorTaskConfig) -> list[ProductRecord]:
        return self.rounds.pop(0)

    def enrich_chat_links(self, records: list[ProductRecord]) -> None:
        return None


class SuccessfulProvider:
    provider_id = "feishu"
    capabilities = frozenset()

    def validate_config(self) -> None:
        return None

    def send_test(self) -> DeliveryResult:
        return DeliveryResult("feishu", True)

    def send_batch(self, batch) -> DeliveryResult:
        return DeliveryResult("feishu", True, "ok", 200)


def test_three_round_monitor_flow_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    task = MonitorTaskConfig(name="相机新品", keyword="相机", enabled=True)
    store.save_task(task)
    now = datetime(2026, 8, 4, 10, 0)
    scanner = RoundScanner(
        [
            _records(range(1, 4)),
            _records(range(1, 16)),
        ]
    )
    coordinator = MonitorCoordinator(store, scanner)

    first = coordinator.run_task(task.task_id, now=now)
    second = coordinator.run_task(task.task_id, now=now + timedelta(minutes=10))

    assert first.baseline_created and first.new_count == 0
    assert second.new_count == 12
    batch = store.list_batches(status="pending")[0]
    assert len(batch.items) == 10
    assert batch.total_count == 12

    dispatcher = NotificationDispatcher(store)
    dispatcher._providers["feishu"] = SuccessfulProvider()
    dispatcher.deliver_due(now=now + timedelta(minutes=10))
    assert store.get_batch(batch.batch_id).status == "sent"

    reopened = MonitorStore(path)
    third = MonitorCoordinator(
        reopened, RoundScanner([_records(range(1, 16))])
    ).run_task(task.task_id, now=now + timedelta(minutes=20))
    assert third.new_count == 0
    assert len(reopened.list_products(task.task_id)) == 15

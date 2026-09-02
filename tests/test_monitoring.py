from datetime import datetime, timedelta
from pathlib import Path

from goofish_collector.models import ProductRecord
from goofish_collector.monitor_models import MonitorTaskConfig
from goofish_collector.monitor_store import MonitorStore
from goofish_collector.monitoring import MonitorCoordinator, next_run_after


class FakeScanner:
    def __init__(self, rounds: list[list[ProductRecord]]) -> None:
        self.rounds = rounds
        self.calls = 0

    def scan(self, task: MonitorTaskConfig) -> list[ProductRecord]:
        result = self.rounds[self.calls]
        self.calls += 1
        return result

    def enrich_chat_links(self, records: list[ProductRecord]) -> None:
        for record in records:
            if record.item_id == "2":
                record.seller_id = "seller2"
                record.chat_url = (
                    "https://www.goofish.com/im?itemId=2&peerUserId=seller2"
                )


def _record(item_id: str) -> ProductRecord:
    return ProductRecord(
        keyword="相机",
        item_id=item_id,
        title=f"相机 {item_id}",
        url=f"https://www.goofish.com/item?id={item_id}",
    )


def test_coordinator_baseline_then_queues_only_new_items(tmp_path: Path) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    task = MonitorTaskConfig(name="相机新品", keyword="相机", interval_minutes=5)
    store.save_task(task)
    scanner = FakeScanner([[_record("1")], [_record("1"), _record("2")]])
    coordinator = MonitorCoordinator(store, scanner)
    now = datetime(2026, 8, 4, 10, 0)

    first = coordinator.run_task(task.task_id, now=now)
    second = coordinator.run_task(task.task_id, now=now + timedelta(minutes=5))

    assert first.baseline_created
    assert first.new_count == 0
    assert second.new_count == 1
    batches = store.list_batches()
    assert len(batches) == 1
    assert batches[0].items[0].item_id == "2"
    assert batches[0].items[0].chat_url.endswith("peerUserId=seller2")


def test_next_run_after_overdue_only_schedules_once() -> None:
    now = datetime(2026, 8, 4, 12, 0)
    overdue = datetime(2026, 8, 4, 10, 0)

    assert next_run_after(overdue, now, 10) == datetime(2026, 8, 4, 12, 10)

from datetime import datetime

import pytest

from goofish_collector.models import ProductRecord, SearchFilters
from goofish_collector.monitor_models import MonitorTaskConfig, quiet_until


def test_monitor_task_validates_interval_pages_and_sorting() -> None:
    task = MonitorTaskConfig(
        name="低价耳机",
        keyword="FreeClip",
        pages=3,
        interval_minutes=10,
        filters=SearchFilters(sort_mode="新发布", max_price=800),
    )

    assert task.keyword == "FreeClip"
    assert task.filters.sort_mode == "新发布"
    assert task.rule_fingerprint

    with pytest.raises(ValueError, match="扫描页数"):
        MonitorTaskConfig(name="x", keyword="x", pages=4)
    with pytest.raises(ValueError, match="监控间隔"):
        MonitorTaskConfig(name="x", keyword="x", interval_minutes=6)
    with pytest.raises(ValueError, match="排序"):
        SearchFilters(sort_mode="最低价").validate()


def test_product_record_monitor_fields_round_trip() -> None:
    record = ProductRecord(
        keyword="耳机",
        item_id="123",
        title="全新耳机",
        url="https://www.goofish.com/item?id=123",
        seller_id="seller-1",
        chat_url="https://www.goofish.com/im?itemId=123&peerUserId=seller-1",
        first_seen_at="2026-08-04 10:00:00",
        notified_at="2026-08-04 10:01:00",
    )

    restored = ProductRecord.from_dict(record.to_dict())

    assert restored.seller_id == "seller-1"
    assert restored.chat_url.endswith("peerUserId=seller-1")
    assert restored.notified_at == "2026-08-04 10:01:00"


def test_quiet_hours_support_daytime_and_overnight() -> None:
    daytime = datetime(2026, 8, 4, 13, 30)
    assert quiet_until(daytime, "12:00", "14:00") == datetime(2026, 8, 4, 14, 0)
    assert quiet_until(daytime, "22:00", "07:00") is None

    overnight = datetime(2026, 8, 4, 23, 30)
    assert quiet_until(overnight, "22:00", "07:00") == datetime(2026, 8, 5, 7, 0)
    early = datetime(2026, 8, 5, 6, 0)
    assert quiet_until(early, "22:00", "07:00") == datetime(2026, 8, 5, 7, 0)

from __future__ import annotations

import json
from pathlib import Path

import goofish_collector

from goofish_collector import cli
from goofish_collector.monitor_models import MonitorTaskConfig, NotificationBatch
from goofish_collector.monitor_store import MonitorStore
from goofish_collector.models import ProductRecord
from goofish_collector.parser import CardPayload


def test_package_exposes_agent_cli() -> None:
    assert callable(getattr(goofish_collector, "run_cli", None))


def test_collect_arguments_build_a_visible_browser_config(tmp_path: Path) -> None:
    build_parser = getattr(cli, "build_parser", None)
    parse_collect_config = getattr(cli, "parse_collect_config", None)
    assert callable(build_parser)
    assert callable(parse_collect_config)

    args = build_parser().parse_args(
        [
            "collect",
            "--keyword",
            "相机",
            "--pages",
            "2",
            "--max-items",
            "10",
            "--output-dir",
            str(tmp_path),
            "--min-price",
            "100",
            "--max-price",
            "800",
            "--region",
            "广东",
            "--published-within",
            "3天内",
            "--personal-only",
            "--free-shipping",
            "--sort-mode",
            "新发布",
        ]
    )

    config = parse_collect_config(args)

    assert config.keyword == "相机"
    assert config.max_pages == 2
    assert config.max_items == 10
    assert config.output_dir == tmp_path.resolve()
    assert config.filters.min_price == 100.0
    assert config.filters.max_price == 800.0
    assert config.filters.region == "广东"
    assert config.filters.published_within == "3天内"
    assert config.filters.personal_only
    assert config.filters.free_shipping
    assert config.filters.sort_mode == "新发布"


class FakeSession:
    def __init__(self, **_kwargs) -> None:
        self.page = 0

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def open_search(self, _keyword: str) -> None:
        return None

    def apply_filters(self, _filters) -> None:
        return None

    def extract_cards(self):
        return [
            CardPayload(
                href="https://www.goofish.com/item?id=1",
                anchor_text="相机",
                card_text="相机\n¥100",
            )
        ]

    def goto_next_page(self) -> bool:
        return False


def test_collect_writes_machine_readable_summary(tmp_path: Path) -> None:
    run_collect = getattr(cli, "run_collect", None)
    assert callable(run_collect)
    config = cli.parse_collect_config(
        cli.build_parser().parse_args(
            ["collect", "--keyword", "相机", "--pages", "1", "--output-dir", str(tmp_path)]
        )
    )

    summary = run_collect(config, session_factory=FakeSession)

    assert summary["status"] == "completed"
    assert summary["command"] == "collect"
    assert summary["searched_pages"] == 1
    assert summary["raw_records"] == 1
    assert summary["unique_records"] == 1
    assert Path(summary["output_xlsx"]).is_file()
    summary_path = Path(summary["summary_json"])
    assert summary_path.is_file()
    assert json.loads(summary_path.read_text(encoding="utf-8"))["output_xlsx"] == summary["output_xlsx"]


def _record(item_id: str) -> ProductRecord:
    return ProductRecord(
        keyword="相机",
        item_id=item_id,
        title=f"商品 {item_id}",
        url=f"https://www.goofish.com/item?id={item_id}",
    )


def test_monitor_status_is_read_only_and_excludes_notification_secrets(tmp_path: Path) -> None:
    read_monitor_status = getattr(cli, "read_monitor_status", None)
    assert callable(read_monitor_status)
    missing = tmp_path / "missing.db"
    assert read_monitor_status(missing)["status"] == "not_initialized"
    assert not missing.exists()

    database = tmp_path / "monitor.db"
    store = MonitorStore(database)
    task = MonitorTaskConfig(name="相机新品", keyword="相机", enabled=True)
    store.save_task(task)
    store.enqueue_batch(
        NotificationBatch(
            task_id=task.task_id,
            task_name=task.name,
            provider_id="feishu",
            items=[_record("1")],
            total_count=1,
        )
    )
    before = database.read_bytes()

    snapshot = read_monitor_status(database)

    assert database.read_bytes() == before
    assert snapshot["status"] == "ok"
    assert snapshot["task_count"] == 1
    assert snapshot["tasks"][0]["name"] == "相机新品"
    assert "last_error" not in snapshot["tasks"][0]
    assert snapshot["tasks"][0]["has_error"] is False
    assert snapshot["outbox"]["pending"] == 1
    assert "app_secret" not in json.dumps(snapshot, ensure_ascii=False)
    assert "batch_json" not in json.dumps(snapshot, ensure_ascii=False)


def test_monitor_status_command_emits_json(capsys, tmp_path: Path) -> None:
    exit_code = cli.run_cli(["monitor-status", "--database", str(tmp_path / "missing.db")])

    result = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert result["command"] == "monitor-status"
    assert result["status"] == "not_initialized"

from __future__ import annotations

import json
from pathlib import Path

import goofish_collector

from goofish_collector import cli
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

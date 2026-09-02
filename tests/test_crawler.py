from __future__ import annotations

from pathlib import Path

from goofish_collector.checkpoint import Checkpoint, CheckpointStore
from goofish_collector.crawler import CrawlEngine, RunControl
from goofish_collector.models import CrawlConfig, ProductRecord, SearchFilters
from goofish_collector.parser import CardPayload


def card(item_id: str, title: str = "商品", price: int = 10) -> CardPayload:
    return CardPayload(
        href=f"https://www.goofish.com/item?id={item_id}",
        anchor_text=title,
        card_text=f"{title}\n¥{price}",
    )


class FakeSession:
    def __init__(self, pages: list[list[CardPayload]], failures: int = 0) -> None:
        self.pages = pages
        self.index = 0
        self.failures = failures
        self.opened_keyword = ""
        self.applied_filters = SearchFilters()

    def open_search(self, keyword: str) -> None:
        self.opened_keyword = keyword

    def apply_filters(self, filters: SearchFilters) -> None:
        self.applied_filters = filters

    def extract_cards(self) -> list[CardPayload]:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("temporary")
        return self.pages[self.index]

    def goto_next_page(self) -> bool:
        if self.index + 1 >= len(self.pages):
            return False
        self.index += 1
        return True


def make_engine(tmp_path: Path, config: CrawlConfig | None = None) -> tuple[CrawlEngine, CheckpointStore]:
    actual_config = config or CrawlConfig("耳机", 50, 0, tmp_path)
    store = CheckpointStore.for_config(actual_config)
    engine = CrawlEngine(
        checkpoint_store=store,
        control=RunControl(),
        sleep=lambda _: None,
    )
    return engine, store


def test_engine_collects_pages_and_merges_duplicates(tmp_path: Path) -> None:
    filters = SearchFilters(min_price=100, region="广东", personal_only=True)
    config = CrawlConfig("耳机", 50, 0, tmp_path, filters)
    engine, store = make_engine(tmp_path, config)
    session = FakeSession([[card("1"), card("2")], [card("2", price=20), card("3")]])

    result = engine.run(config, session)

    assert session.opened_keyword == "耳机"
    assert session.applied_filters == filters
    assert result.stop_reason == "已到末页"
    assert result.searched_pages == 2
    assert result.raw_records == 4
    assert [record.item_id for record in result.records] == ["1", "2", "3"]
    duplicate = result.records[1]
    assert duplicate.appearances == 2
    assert duplicate.pages_seen == [1, 2]
    assert duplicate.price == 20.0
    assert store.load().current_page == 2


def test_engine_stops_at_unique_item_limit(tmp_path: Path) -> None:
    config = CrawlConfig("耳机", 50, 2, tmp_path)
    engine, _ = make_engine(tmp_path, config)
    session = FakeSession([[card("1"), card("2"), card("3")], [card("4")]])

    result = engine.run(config, session)

    assert result.stop_reason == "已达到最大商品数 2"
    assert len(result.records) == 2
    assert result.searched_pages == 1


def test_engine_resumes_after_last_completed_page_without_recounting(tmp_path: Path) -> None:
    config = CrawlConfig("耳机", 50, 0, tmp_path)
    engine, store = make_engine(tmp_path, config)
    existing = ProductRecord(
        keyword="耳机",
        item_id="1",
        title="旧商品",
        url="https://www.goofish.com/item?id=1",
        first_page=1,
        pages_seen=[1],
    )
    resume = Checkpoint(config, current_page=1, raw_records=1, status="stopped", records=[existing])
    store.save(resume)
    session = FakeSession([[card("1")], [card("2")]])

    result = engine.run(config, session, resume=resume)

    assert result.raw_records == 2
    assert [record.item_id for record in result.records] == ["1", "2"]
    assert result.records[0].appearances == 1
    assert result.searched_pages == 2


def test_engine_retries_transient_extract_failure_twice(tmp_path: Path) -> None:
    config = CrawlConfig("耳机", 1, 0, tmp_path)
    engine, _ = make_engine(tmp_path, config)
    session = FakeSession([[card("1")]], failures=2)

    result = engine.run(config, session)

    assert len(result.records) == 1
    assert result.stop_reason == "已达到最大页数 1"


def test_engine_stops_before_opening_browser_when_already_cancelled(tmp_path: Path) -> None:
    config = CrawlConfig("耳机", 50, 0, tmp_path)
    control = RunControl()
    control.stop()
    engine = CrawlEngine(
        checkpoint_store=CheckpointStore.for_config(config),
        control=control,
        sleep=lambda _: None,
    )
    session = FakeSession([[card("1")]])

    result = engine.run(config, session)

    assert result.stop_reason == "用户停止"
    assert result.records == []
    assert session.opened_keyword == ""

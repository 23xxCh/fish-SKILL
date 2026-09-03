from pathlib import Path

import pytest

from goofish_collector.models import (
    CrawlConfig,
    ProductRecord,
    PushRules,
    RecordCollection,
    SearchFilters,
)


def test_config_normalizes_keyword_and_output_directory(tmp_path: Path) -> None:
    config = CrawlConfig("  华为耳机  ", 50, 0, tmp_path)

    assert config.keyword == "华为耳机"
    assert config.output_dir == tmp_path.resolve()


def test_search_filters_validate_and_round_trip_with_config(tmp_path: Path) -> None:
    filters = SearchFilters(
        min_price=100,
        max_price=800,
        region=" 广东 ",
        published_within="3天内",
        personal_only=True,
        inspection_only=True,
        free_shipping=True,
        brand_new=True,
    )
    config = CrawlConfig("耳机", 50, 0, tmp_path, filters)

    restored = CrawlConfig.from_dict(config.to_dict())

    assert restored == config
    assert restored.filters.region == "广东"
    assert restored.filters.active_labels() == ["个人闲置", "验货宝", "包邮", "全新"]


@pytest.mark.parametrize(
    "filters",
    [
        SearchFilters(min_price=-1),
        SearchFilters(max_price=-1),
        SearchFilters(min_price=900, max_price=800),
        SearchFilters(published_within="30天内"),
    ],
)
def test_search_filters_reject_invalid_values(filters: SearchFilters) -> None:
    with pytest.raises(ValueError):
        filters.validate()


@pytest.mark.parametrize(
    ("keyword", "max_pages", "max_items"),
    [("", 50, 0), ("耳机", 0, 0), ("耳机", 201, 0), ("耳机", 50, -1)],
)
def test_config_rejects_invalid_values(
    tmp_path: Path, keyword: str, max_pages: int, max_items: int
) -> None:
    with pytest.raises(ValueError):
        CrawlConfig(keyword, max_pages, max_items, tmp_path)


def test_collection_merges_duplicate_product_across_pages() -> None:
    collection = RecordCollection()
    first = ProductRecord(
        keyword="耳机",
        item_id="123",
        title="商品",
        url="https://www.goofish.com/item?id=123",
        first_page=1,
        pages_seen=[1],
        image_url="https://img.goofish.example/old.jpg",
    )
    duplicate = ProductRecord(
        keyword="耳机",
        item_id="123",
        title="商品新标题",
        url="https://www.goofish.com/item?id=123",
        price=99.0,
        first_page=2,
        pages_seen=[2],
        image_url="https://img.goofish.example/new.jpg",
    )

    assert collection.add(first) is True
    assert collection.add(duplicate) is False
    merged = collection.records[0]
    assert merged.appearances == 2
    assert merged.pages_seen == [1, 2]
    assert merged.price == 99.0
    assert merged.title == "商品新标题"
    assert merged.image_url == "https://img.goofish.example/new.jpg"


def test_collection_uses_normalized_url_when_item_id_is_missing() -> None:
    collection = RecordCollection()
    record = ProductRecord(
        keyword="耳机",
        item_id="",
        title="商品",
        url="https://www.goofish.com/item?categoryId=1&id=123",
        first_page=1,
        pages_seen=[1],
    )

    assert collection.add(record) is True
    assert collection.add(record) is False
    assert len(collection.records) == 1


def test_push_rules_keep_only_low_price_matching_items() -> None:
    rules = PushRules(max_price=300, include_terms=("华为", "耳机"), exclude_terms=("单耳",))
    records = [
        ProductRecord(
            keyword="耳机",
            item_id="1",
            title="华为 FreeClip 耳机",
            price=299,
            url="https://www.goofish.com/item?id=1",
        ),
        ProductRecord(
            keyword="耳机",
            item_id="2",
            title="华为 单耳耳机",
            price=99,
            url="https://www.goofish.com/item?id=2",
        ),
        ProductRecord(
            keyword="耳机",
            item_id="3",
            title="华为 FreeClip 耳机",
            price=399,
            url="https://www.goofish.com/item?id=3",
        ),
        ProductRecord(
            keyword="耳机",
            item_id="4",
            title="华为 FreeClip 耳机",
            url="https://www.goofish.com/item?id=4",
        ),
    ]

    assert [record.item_id for record in rules.filter(records)] == ["1"]
    assert PushRules.from_dict(rules.to_dict()) == rules

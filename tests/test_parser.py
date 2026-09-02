from goofish_collector.parser import CardPayload, parse_card, parse_item_id, normalize_item_url


def test_normalize_item_url_keeps_only_stable_identifiers() -> None:
    url = (
        "https://www.goofish.com/item?spm=a21ybx.foo&categoryId=126864985"
        "&id=1048546694850&utm_source=test"
    )

    assert normalize_item_url(url) == (
        "https://www.goofish.com/item?id=1048546694850&categoryId=126864985"
    )
    assert parse_item_id(url) == "1048546694850"


def test_parse_card_extracts_visible_fields_conservatively() -> None:
    payload = CardPayload(
        href="https://www.goofish.com/item?id=1048546694850&categoryId=126864985",
        anchor_text="华为 FreeClip 耳机",
        card_text=(
            "华为 FreeClip 耳机\n95新\n原价 ¥699\n¥ 520\n"
            "3人想要\n1天内降价\n累计降价48.00元\n河南\n百分百好评"
        ),
    )

    record = parse_card(payload, keyword="耳机", page=3, captured_at="2026-08-04 10:00:00")

    assert record.item_id == "1048546694850"
    assert record.title == "华为 FreeClip 耳机"
    assert record.price == 520.0
    assert record.original_price == 699.0
    assert record.region == "河南"
    assert record.condition == "95新"
    assert record.wants == 3
    assert record.reputation == "百分百好评"
    assert record.publish_or_change == "1天内降价"
    assert record.discount == "累计降价48.00元"
    assert record.first_page == 3
    assert record.pages_seen == [3]


def test_parse_card_keeps_unavailable_fields_blank() -> None:
    payload = CardPayload(
        href="https://www.goofish.com/item?id=99",
        anchor_text="普通商品",
        card_text="普通商品\n¥12",
    )

    record = parse_card(payload, keyword="商品", page=1, captured_at="2026-08-04 10:00:00")

    assert record.price == 12.0
    assert record.original_price is None
    assert record.region == ""
    assert record.condition == ""
    assert record.wants is None
    assert record.reputation == ""


def test_parse_card_rejects_non_item_links() -> None:
    payload = CardPayload(
        href="https://www.goofish.com/search?q=耳机",
        anchor_text="搜索",
        card_text="搜索",
    )

    assert parse_card(payload, keyword="耳机", page=1, captured_at="now") is None


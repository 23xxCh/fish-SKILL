from pathlib import Path

from goofish_collector.checkpoint import Checkpoint, CheckpointStore
from goofish_collector.models import CrawlConfig, ProductRecord


def test_checkpoint_round_trip(tmp_path: Path) -> None:
    config = CrawlConfig("耳机", 50, 0, tmp_path)
    checkpoint = Checkpoint(
        config=config,
        current_page=4,
        raw_records=120,
        status="running",
        records=[
            ProductRecord(
                keyword="耳机",
                item_id="123",
                title="商品",
                url="https://www.goofish.com/item?id=123",
                first_page=1,
                appearances=2,
                pages_seen=[1, 4],
                image_url="https://img.goofish.example/item-123.jpg",
            )
        ],
    )
    store = CheckpointStore.for_config(config)

    store.save(checkpoint)
    loaded = store.load()

    assert loaded is not None
    assert loaded.config == config
    assert loaded.current_page == 4
    assert loaded.raw_records == 120
    assert loaded.records[0].pages_seen == [1, 4]
    assert loaded.records[0].image_url == "https://img.goofish.example/item-123.jpg"
    assert not store.path.with_suffix(".tmp").exists()

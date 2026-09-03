from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from goofish_collector.app import MainWindow
from goofish_collector.models import CrawlConfig, ProductRecord, PushRules, ScheduledCollectionConfig
from goofish_collector.monitor_models import FeishuConfig, NotificationBatch


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_single_collection_page_saves_timed_collection_snapshot(
    tmp_path: Path, qt_app: QApplication
) -> None:
    window = MainWindow(
        default_output_dir=tmp_path,
        monitor_db_path=tmp_path / "monitor.db",
        profile_dir=tmp_path / "profile",
    )
    assert window.centralWidget() is window.content_scroll
    assert not hasattr(window, "main_tabs")
    assert hasattr(window, "schedule_interval_combo")
    assert hasattr(window, "feishu_settings_button")

    window.keyword_edit.setText("FreeClip")
    window.max_pages_spin.setValue(3)
    window.min_price_spin.setValue(300)
    window.max_price_spin.setValue(800)
    window.region_combo.setCurrentText("广东")
    window.publish_combo.setCurrentText("3天内")
    window.personal_checkbox.setChecked(True)
    window.schedule_interval_combo.setCurrentText("15 分钟")

    saved = window._save_scheduled_collection(enabled=True)

    assert isinstance(saved, ScheduledCollectionConfig)
    assert saved.enabled
    assert saved.interval_minutes == 15
    assert saved.crawl_config.keyword == "FreeClip"
    assert saved.crawl_config.max_pages == 3
    assert saved.crawl_config.filters.region == "广东"
    assert saved.crawl_config.filters.personal_only
    assert window._monitor_store.load_scheduled_collection() == saved
    window.close()


def test_timed_collection_can_opt_into_change_only_delivery(
    tmp_path: Path, qt_app: QApplication
) -> None:
    window = MainWindow(
        default_output_dir=tmp_path,
        monitor_db_path=tmp_path / "monitor.db",
        profile_dir=tmp_path / "profile",
    )

    assert not window.notify_changes_only_checkbox.isChecked()
    window.notify_changes_only_checkbox.setChecked(True)
    window.keyword_edit.setText("相机")
    saved = window._save_scheduled_collection(enabled=True)

    assert saved.notify_changes_only is True
    assert window._monitor_store.load_scheduled_collection().notify_changes_only is True
    window.close()


def test_timed_collection_health_shows_a_persisted_delivery_retry(
    tmp_path: Path, qt_app: QApplication
) -> None:
    window = MainWindow(
        default_output_dir=tmp_path,
        monitor_db_path=tmp_path / "monitor.db",
        profile_dir=tmp_path / "profile",
    )
    window.keyword_edit.setText("相机")
    window._save_scheduled_collection(enabled=True)
    batch = window._monitor_store.enqueue_batch(
        NotificationBatch(
            task_id="scheduled_collection",
            task_name="定时采集：相机",
            provider_id="feishu",
            items=[
                ProductRecord(
                    keyword="相机",
                    item_id="1",
                    title="相机",
                    url="https://www.goofish.com/item?id=1",
                )
            ],
            total_count=1,
        )
    )

    window._scheduled_delivery_finished(batch.batch_id, False, "网络暂时不可用")
    qt_app.processEvents()

    retry = window._monitor_store.get_batch(batch.batch_id)
    assert retry.status == "pending"
    assert retry.attempts == 1
    assert window.schedule_health_button.text() == "健康：需处理"
    window._show_scheduled_health()
    assert "发送失败" in window.health_delivery_label.text()
    window.close()


def test_timed_collection_queues_the_next_feishu_batch_while_the_previous_one_is_sending(
    tmp_path: Path, qt_app: QApplication
) -> None:
    window = MainWindow(
        default_output_dir=tmp_path,
        monitor_db_path=tmp_path / "monitor.db",
        profile_dir=tmp_path / "profile",
    )
    window._scheduled_collection = ScheduledCollectionConfig(
        crawl_config=CrawlConfig(keyword="相机", output_dir=tmp_path), enabled=True
    )
    first = ProductRecord(
        keyword="相机", item_id="1", title="商品一", url="https://www.goofish.com/item?id=1"
    )
    second = ProductRecord(
        keyword="相机", item_id="2", title="商品二", url="https://www.goofish.com/item?id=2"
    )

    with patch("goofish_collector.app.NotificationDeliveryWorker") as worker_type:
        worker_type.return_value.isRunning.return_value = True
        window._push_scheduled_results([first])
        window._push_scheduled_results([second])

    queued = [
        batch
        for batch in window._monitor_store.list_batches(status="pending")
        if batch.task_id == "scheduled_collection"
    ]
    assert len(queued) == 2
    assert worker_type.call_count == 1
    window.close()


def test_manual_push_is_opt_in_and_respects_saved_push_rules(
    tmp_path: Path, qt_app: QApplication
) -> None:
    window = MainWindow(
        default_output_dir=tmp_path,
        monitor_db_path=tmp_path / "monitor.db",
        profile_dir=tmp_path / "profile",
    )
    matching = ProductRecord(
        keyword="耳机",
        item_id="1",
        title="华为耳机",
        price=299,
        url="https://www.goofish.com/item?id=1",
    )
    excluded = ProductRecord(
        keyword="耳机",
        item_id="2",
        title="华为单耳耳机",
        price=99,
        url="https://www.goofish.com/item?id=2",
    )
    window._monitor_store.save_push_rules(
        PushRules(max_price=300, include_terms=("华为",), exclude_terms=("单耳",))
    )

    assert not window.manual_push_checkbox.isChecked()
    with patch("goofish_collector.app.NotificationDeliveryWorker") as worker_type:
        window._active_run_manual_push = False
        window._push_manual_results([matching, excluded])
        assert worker_type.call_count == 0

        window._active_run_manual_push = True
        window._push_manual_results([matching, excluded])

    pushed = worker_type.call_args.args[1]
    assert [record.item_id for record in pushed.items] == ["1"]
    assert pushed.task_name == "单次采集：耳机"
    window.close()


def test_timed_collection_failure_sends_an_immediate_feishu_alert(
    tmp_path: Path, qt_app: QApplication
) -> None:
    window = MainWindow(
        default_output_dir=tmp_path,
        monitor_db_path=tmp_path / "monitor.db",
        profile_dir=tmp_path / "profile",
    )
    window._scheduled_collection = ScheduledCollectionConfig(
        crawl_config=CrawlConfig(keyword="相机", output_dir=tmp_path), enabled=True
    )
    window._monitor_store.save_feishu_config(
        FeishuConfig(app_id="cli_demo", app_secret="secret-demo", open_id="ou_demo")
    )
    window._active_run_is_scheduled = True

    with patch("goofish_collector.app.NotificationTextWorker") as worker_type:
        window._crawl_failed("登录状态已失效")

    alert_text = worker_type.call_args.args[1]
    assert "定时采集失败" in alert_text
    assert "相机" in alert_text
    assert "登录状态已失效" in alert_text
    assert "本轮采集失败" in window.health_delivery_label.text()
    window.close()


def test_feishu_setup_status_guides_user_through_binding(
    tmp_path: Path, qt_app: QApplication
) -> None:
    window = MainWindow(
        default_output_dir=tmp_path,
        monitor_db_path=tmp_path / "monitor.db",
        profile_dir=tmp_path / "profile",
    )
    status = getattr(window, "feishu_setup_status", None)

    assert status is not None
    assert status.text().startswith("第 1 步/3")

    window.feishu_app_id_edit.setText("cli_demo")
    window.feishu_secret_edit.setText("secret-demo")
    assert window._save_feishu_settings(show_message=False)
    assert window.feishu_setup_status.text().startswith("第 2 步/3")

    window._monitor_store.save_feishu_config(
        FeishuConfig(app_id="cli_demo", app_secret="secret-demo", open_id="ou_demo")
    )
    window._load_notification_settings()

    assert window.feishu_setup_status.text().startswith("第 3 步/3")
    assert "测试飞书" in window.feishu_setup_status.text()
    assert "已绑定" in window.feishu_status_label.text()
    window.close()


def test_timed_collection_runs_current_rule_then_can_be_stopped(
    tmp_path: Path, qt_app: QApplication
) -> None:
    window = MainWindow(
        default_output_dir=tmp_path,
        monitor_db_path=tmp_path / "monitor.db",
        profile_dir=tmp_path / "profile",
    )
    window.keyword_edit.setText("相机")
    window.schedule_interval_combo.setCurrentText("5 分钟")
    window._monitor_store.save_feishu_config(
        FeishuConfig(app_id="cli_demo", app_secret="secret-demo", open_id="ou_demo")
    )

    with patch.object(window, "_start_crawl") as start_crawl:
        window._start_scheduled_collection()
        qt_app.processEvents()

    start_crawl.assert_called_once()
    assert start_crawl.call_args.kwargs["scheduled"]
    assert start_crawl.call_args.kwargs["config"].keyword == "相机"
    assert window._monitor_store.load_scheduled_collection().enabled

    window._stop_scheduled_collection(show_message=False)
    assert not window._monitor_store.load_scheduled_collection().enabled
    window.close()

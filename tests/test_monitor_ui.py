from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from goofish_collector.app import MainWindow
from goofish_collector.models import ScheduledCollectionConfig
from goofish_collector.monitor_models import FeishuConfig


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

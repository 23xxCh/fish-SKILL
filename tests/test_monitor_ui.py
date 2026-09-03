from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from goofish_collector.app import MainWindow
from goofish_collector.monitor_models import FeishuConfig


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_monitor_page_saves_full_search_rule(tmp_path: Path, qt_app: QApplication) -> None:
    window = MainWindow(
        default_output_dir=tmp_path,
        monitor_db_path=tmp_path / "monitor.db",
        profile_dir=tmp_path / "profile",
    )
    assert window.main_tabs.count() == 2
    assert window.main_tabs.tabText(1) == "新品监控"
    assert window.provider_combo.currentData() == "feishu"

    window.monitor_name_edit.setText("广州耳机")
    window.monitor_keyword_edit.setText("FreeClip")
    window.monitor_min_price.setValue(300)
    window.monitor_max_price.setValue(800)
    window.monitor_region_combo.setCurrentText("广东")
    window.monitor_publish_combo.setCurrentText("3天内")
    window.monitor_sort_combo.setCurrentText("新发布")
    window.monitor_pages_combo.setCurrentText("3")
    window.monitor_interval_combo.setCurrentText("5")
    window.monitor_personal_checkbox.setChecked(True)
    window.monitor_quiet_checkbox.setChecked(True)

    assert window._save_monitor_task()
    state = window._monitor_store.get_task_state(window._selected_task_id)

    assert state.config.keyword == "FreeClip"
    assert state.config.pages == 3
    assert state.config.interval_minutes == 5
    assert state.config.filters.region == "广东"
    assert state.config.filters.sort_mode == "新发布"
    assert state.config.filters.personal_only
    assert state.config.quiet_enabled
    assert window.monitor_table.rowCount() == 1
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
    window.close()

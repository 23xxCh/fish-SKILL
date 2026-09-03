from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from goofish_collector.app import MainWindow, SingleInstanceCoordinator, light_palette
from goofish_collector.models import ProductRecord


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


def _window(tmp_path: Path) -> MainWindow:
    return MainWindow(
        default_output_dir=tmp_path,
        profile_dir=tmp_path / "browser-profile",
        monitor_db_path=tmp_path / "monitor.db",
    )


def test_second_instance_notifies_the_first(qt_app: QApplication) -> None:
    coordinator_name = f"goofish-collector-test-{uuid4()}"
    first = SingleInstanceCoordinator(coordinator_name)
    second: SingleInstanceCoordinator | None = None
    activations: list[bool] = []
    first.activation_requested.connect(lambda: activations.append(True))

    try:
        assert first.start() is True
        second = SingleInstanceCoordinator(coordinator_name)
        assert second.start() is False

        client = QLocalSocket()
        client.connectToServer(coordinator_name)
        QTest.qWait(50)
        assert activations == [True, True]
    finally:
        if second is not None:
            second.close()
        first.close()


def test_window_defaults_and_config(tmp_path: Path, qt_app: QApplication) -> None:
    window = _window(tmp_path)
    window.keyword_edit.setText("  华为耳机  ")
    window.min_price_spin.setValue(100)
    window.max_price_spin.setValue(800)
    window.region_combo.setCurrentText("广东")
    window.publish_combo.setCurrentText("3天内")
    window.personal_checkbox.setChecked(True)
    window.inspection_checkbox.setChecked(True)
    window.free_shipping_checkbox.setChecked(True)
    window.brand_new_checkbox.setChecked(True)

    config = window.build_config()

    assert config.keyword == "华为耳机"
    assert config.max_pages == 50
    assert config.max_items == 0
    assert config.output_dir == tmp_path.resolve()
    assert config.filters.min_price == 100
    assert config.filters.max_price == 800
    assert config.filters.region == "广东"
    assert config.filters.published_within == "3天内"
    assert config.filters.active_labels() == ["个人闲置", "验货宝", "包邮", "全新"]
    assert window.start_button.isEnabled()
    assert not window.pause_button.isEnabled()
    assert not window.resume_button.isEnabled()
    assert not window.stop_button.isEnabled()
    window.close()


def test_window_exposes_a_manual_update_check_without_starting_an_install(
    tmp_path: Path, qt_app: QApplication
) -> None:
    window = _window(tmp_path)

    assert window.update_check_button.text() == "检查更新"
    assert window.update_status_label.text() == "当前版本 v0.9.0"
    assert window._should_auto_check_for_update() is True
    window.close()


def test_window_rejects_reversed_price_range(tmp_path: Path, qt_app: QApplication) -> None:
    window = _window(tmp_path)
    window.keyword_edit.setText("耳机")
    window.min_price_spin.setValue(900)
    window.max_price_spin.setValue(800)

    with pytest.raises(ValueError, match="最低价不能高于最高价"):
        window.build_config()
    window.close()


def test_window_rejects_blank_keyword(tmp_path: Path, qt_app: QApplication) -> None:
    window = _window(tmp_path)
    with pytest.raises(ValueError, match="关键词不能为空"):
        window.build_config()
    window.close()


def test_filter_inputs_override_dark_system_palette(
    tmp_path: Path, qt_app: QApplication
) -> None:
    """价格框和下拉框必须显式使用浅色，不能继承 Windows 深色主题。"""
    window = _window(tmp_path)
    style = window.styleSheet()

    assert "QDoubleSpinBox" in style
    assert "QComboBox" in style
    assert "QComboBox QAbstractItemView" in style
    assert "selection-background-color: #f8d447" in style
    palette = light_palette()
    assert palette.color(QPalette.Base).name() == "#ffffff"
    assert palette.color(QPalette.Text).name() == "#1f2937"
    window.close()


def test_price_inputs_accept_direct_number_typing(
    tmp_path: Path, qt_app: QApplication
) -> None:
    """显示“不限”时点击价格框后应能直接输入数字。"""
    window = _window(tmp_path)
    window.show()
    qt_app.processEvents()

    assert window.min_price_spin.value() == 0
    assert window.min_price_spin.text() == "不限"
    window.min_price_spin.setFocus()
    QTest.keyClicks(window.min_price_spin, "400")
    qt_app.processEvents()

    assert window.min_price_spin.value() == 400
    window.max_price_spin.setFocus()
    QTest.keyClicks(window.max_price_spin, "800")
    qt_app.processEvents()

    assert window.max_price_spin.value() == 800
    window.close()


def test_live_result_sidebar_upserts_current_task_records(
    tmp_path: Path, qt_app: QApplication
) -> None:
    window = _window(tmp_path)
    window.show()
    qt_app.processEvents()

    assert hasattr(window, "result_model")
    assert window.result_panel.isHidden()
    first = ProductRecord(
        keyword="耳机",
        item_id="1",
        title="全新耳机",
        url="https://www.goofish.com/item?id=1",
        price=10,
        region="广东",
        condition="全新",
    )
    window._update_result_records([first])
    qt_app.processEvents()

    assert window.result_panel.isVisible()
    assert window.result_model.rowCount() == 1
    assert window.result_count_label.text() == "采集结果（1）"
    assert window.unique_value.text() == "1"

    duplicate = ProductRecord(
        keyword="耳机",
        item_id="1",
        title="全新耳机",
        url="https://www.goofish.com/item?id=1",
        price=20,
        region="广东",
        condition="全新",
        appearances=2,
        pages_seen=[1, 2],
    )
    window._update_result_records([duplicate])

    assert window.result_model.rowCount() == 1
    assert window.result_model.index(0, 1).data() == "¥20"

    window._clear_result_records()
    assert window.result_model.rowCount() == 0
    assert window.result_panel.isHidden()
    window.close()


def test_live_result_sidebar_scrolls_all_rows_and_opens_only_https_items(
    tmp_path: Path, qt_app: QApplication
) -> None:
    window = _window(tmp_path)
    window.show()
    records = [
        ProductRecord(
            keyword="耳机",
            item_id=str(index),
            title=f"耳机 {index}",
            url=f"https://www.goofish.com/item?id={index}",
            price=index,
        )
        for index in range(30)
    ]
    window._update_result_records(records)
    qt_app.processEvents()

    assert window.result_model.rowCount() == 30
    assert window.result_view.verticalScrollBar().maximum() > 0
    with patch("goofish_collector.app.QDesktopServices.openUrl") as open_url:
        window._open_result_item(window.result_model.index(0, 0))
        window._update_result_records(
            [
                ProductRecord(
                    keyword="耳机",
                    item_id="unsafe",
                    title="不安全链接",
                    url="file:///C:/not-an-item",
                )
            ]
        )
        window._open_result_item(window.result_model.index(30, 0))

    assert open_url.call_count == 1
    assert open_url.call_args.args[0].toString() == "https://www.goofish.com/item?id=0"
    window.close()

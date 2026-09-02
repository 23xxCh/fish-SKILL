from pathlib import Path

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from goofish_collector.app import MainWindow, light_palette


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


def test_window_defaults_and_config(tmp_path: Path, qt_app: QApplication) -> None:
    window = MainWindow(default_output_dir=tmp_path)
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


def test_window_rejects_reversed_price_range(tmp_path: Path, qt_app: QApplication) -> None:
    window = MainWindow(default_output_dir=tmp_path)
    window.keyword_edit.setText("耳机")
    window.min_price_spin.setValue(900)
    window.max_price_spin.setValue(800)

    with pytest.raises(ValueError, match="最低价不能高于最高价"):
        window.build_config()
    window.close()


def test_window_rejects_blank_keyword(tmp_path: Path, qt_app: QApplication) -> None:
    window = MainWindow(default_output_dir=tmp_path)
    with pytest.raises(ValueError, match="关键词不能为空"):
        window.build_config()
    window.close()


def test_filter_inputs_override_dark_system_palette(
    tmp_path: Path, qt_app: QApplication
) -> None:
    """价格框和下拉框必须显式使用浅色，不能继承 Windows 深色主题。"""
    window = MainWindow(default_output_dir=tmp_path)
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
    window = MainWindow(default_output_dir=tmp_path)
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

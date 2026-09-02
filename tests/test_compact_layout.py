from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QLabel

from goofish_collector.app import MainWindow


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _window_rect(window: MainWindow, widget) -> QRect:
    top_left = widget.mapTo(window, QPoint(0, 0))
    return QRect(top_left, widget.size())


def _label(window: MainWindow, text: str) -> QLabel:
    return next(label for label in window.findChildren(QLabel) if label.text() == text)


def test_compact_window_scrolls_instead_of_overlapping_fields(
    tmp_path: Path, qt_app: QApplication
) -> None:
    """980x740 是支持的最小窗口，标签不能被压到输入框里面。"""
    window = MainWindow(default_output_dir=tmp_path)
    window.resize(980, 740)
    window.show()
    qt_app.processEvents()

    pairs = (
        (window.keyword_edit, _label(window, "最大页数")),
        (window.max_pages_spin, _label(window, "结果保存位置")),
        (window.min_price_spin, _label(window, "商品条件")),
    )
    for upper, lower in pairs:
        assert not _window_rect(window, upper).intersects(_window_rect(window, lower))

    assert window.content_scroll.verticalScrollBar().maximum() > 0
    assert window.content_scroll.horizontalScrollBar().maximum() == 0
    assert _label(window, "最大页数").height() >= 16
    window.close()


def test_default_window_still_fits_without_scrolling(
    tmp_path: Path, qt_app: QApplication
) -> None:
    window = MainWindow(default_output_dir=tmp_path)
    window.resize(1160, 860)
    window.show()
    qt_app.processEvents()

    assert window.content_scroll.verticalScrollBar().maximum() == 0
    assert window.content_scroll.horizontalScrollBar().maximum() == 0
    window.close()

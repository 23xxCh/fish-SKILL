from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from goofish_collector.app import MainWindow


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_primary_controls_keep_comfortable_click_targets(
    tmp_path: Path, qt_app: QApplication
) -> None:
    window = MainWindow(default_output_dir=tmp_path)
    window.show()
    qt_app.processEvents()

    for widget in (
        window.login_button,
        window.start_button,
        window.pause_button,
        window.resume_button,
        window.stop_button,
        window.open_button,
        window.min_price_spin,
        window.max_price_spin,
        window.region_combo,
        window.publish_combo,
        window.personal_checkbox,
        window.inspection_checkbox,
        window.free_shipping_checkbox,
        window.brand_new_checkbox,
    ):
        assert widget.height() >= 44

    assert window.region_combo.width() > window.min_price_spin.width()
    assert window.min_price_spin.width() >= 160
    assert window.max_price_spin.width() >= 160
    window.close()

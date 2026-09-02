from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from goofish_collector.app import MainWindow, application_icon_path


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_window_uses_project_icon(tmp_path: Path, qt_app: QApplication) -> None:
    icon_path = application_icon_path()
    window = MainWindow(default_output_dir=tmp_path)

    assert icon_path.name == "app-icon.png"
    assert icon_path.is_file()
    assert not window.windowIcon().isNull()
    window.close()


def test_pyinstaller_embeds_the_windows_icon() -> None:
    project_root = Path(__file__).resolve().parents[1]
    spec = (project_root / "XianyuLinkCollector.spec").read_text(encoding="utf-8")

    assert '("assets/app-icon.png", "assets")' in spec
    assert 'icon="assets/app-icon.ico"' in spec
    assert (project_root / "assets" / "app-icon.ico").is_file()

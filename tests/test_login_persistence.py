from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtWidgets import QApplication

from goofish_collector.app import MainWindow
from goofish_collector.browser import profile_has_saved_login


def _chrome_timestamp(value: datetime) -> int:
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    return int((value - epoch).total_seconds() * 1_000_000)


def _write_cookie(profile_dir: Path, *, expires_at: datetime) -> None:
    database = profile_dir / "Default" / "Network" / "Cookies"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE cookies "
        "(host_key TEXT, encrypted_value BLOB, expires_utc INTEGER)"
    )
    connection.execute(
        "INSERT INTO cookies VALUES (?, ?, ?)",
        (".goofish.com", b"saved-login", _chrome_timestamp(expires_at)),
    )
    connection.commit()
    connection.close()


def test_profile_detects_unexpired_saved_goofish_login(tmp_path: Path) -> None:
    _write_cookie(
        tmp_path,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )

    assert profile_has_saved_login(tmp_path)


def test_profile_ignores_expired_or_missing_login(tmp_path: Path) -> None:
    assert not profile_has_saved_login(tmp_path)
    _write_cookie(
        tmp_path,
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )

    assert not profile_has_saved_login(tmp_path)


def test_window_explains_that_saved_login_is_reused(tmp_path: Path) -> None:
    _write_cookie(
        tmp_path,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    app = QApplication.instance() or QApplication([])

    window = MainWindow(default_output_dir=tmp_path, profile_dir=tmp_path)
    app.processEvents()

    assert window.login_state_value.text() == "登录状态已保存"
    assert "直接开始采集" in window.login_state_hint.text()
    assert window.login_button.text() == "登录 / 切换账号"
    window.close()

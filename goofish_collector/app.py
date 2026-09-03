from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QSharedMemory,
    QStandardPaths,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QCloseEvent, QDesktopServices, QIcon, QPalette
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyleFactory,
    QSystemTrayIcon,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .browser import default_profile_dir, profile_has_saved_login
from . import __version__
from .checkpoint import CheckpointStore
from .models import CrawlConfig, CrawlProgress, ProductRecord, ScheduledCollectionConfig, SearchFilters
from .feishu_binding import FeishuBindingWorker
from .monitor_models import FeishuConfig, NotificationBatch
from .monitor_store import MonitorStore
from .notifications import FeishuProvider
from .updater import PreparedUpdate, UpdateInfo, UpdateService
from .workers import (
    CrawlWorker,
    LoginWorker,
    NotificationDeliveryWorker,
    NotificationTestWorker,
    UpdateCheckWorker,
    UpdatePreparationWorker,
)


class SingleInstanceCoordinator(QObject):
    """Keeps one desktop window alive and wakes it on a repeat launch."""

    activation_requested = Signal()

    def __init__(self, server_name: str = "goofish-link-collector-desktop-v1") -> None:
        super().__init__()
        self._server_name = server_name
        self._lock = QSharedMemory(f"{server_name}-lock", self)
        self._server = QLocalServer(self)
        self._is_primary = False

    def start(self) -> bool:
        if not self._lock.create(1):
            self._notify_primary()
            return False

        if not self._server.listen(self._server_name):
            # The process lock is ours, so an occupied endpoint is stale.
            QLocalServer.removeServer(self._server_name)
            if not self._server.listen(self._server_name):
                self._lock.detach()
                raise RuntimeError("无法创建应用单实例通道")
        self._server.newConnection.connect(self._handle_activation_request)
        self._is_primary = True
        return True

    def _notify_primary(self) -> None:
        client = QLocalSocket(self)
        client.connectToServer(self._server_name)
        if client.waitForConnected(500):
            client.disconnectFromServer()

    def close(self) -> None:
        self._server.close()
        if self._is_primary:
            QLocalServer.removeServer(self._server_name)
            self._is_primary = False
        if self._lock.isAttached():
            self._lock.detach()

    def _handle_activation_request(self) -> None:
        while self._server.hasPendingConnections():
            client = self._server.nextPendingConnection()
            if client is not None:
                client.disconnectFromServer()
                client.deleteLater()
                self.activation_requested.emit()


def application_icon_path() -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return bundle_root / "assets" / "app-icon.png"


def application_icon() -> QIcon:
    path = application_icon_path()
    return QIcon(str(path)) if path.is_file() else QIcon()


def light_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#f5f7fa"))
    palette.setColor(QPalette.WindowText, QColor("#1f2937"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.AlternateBase, QColor("#f5f7fa"))
    palette.setColor(QPalette.Text, QColor("#1f2937"))
    palette.setColor(QPalette.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ButtonText, QColor("#1f2937"))
    palette.setColor(QPalette.Highlight, QColor("#f8d447"))
    palette.setColor(QPalette.HighlightedText, QColor("#1f2937"))
    palette.setColor(QPalette.PlaceholderText, QColor("#8a96a3"))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#9aa5b1"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#9aa5b1"))
    return palette


class OptionalPriceSpinBox(QDoubleSpinBox):
    """Shows “不限” at zero while still accepting numbers on first focus."""

    def __init__(self) -> None:
        super().__init__()
        self.setSpecialValueText("不限")

    def focusInEvent(self, event) -> None:
        if self.value() == self.minimum():
            self.setSpecialValueText(" ")
        super().focusInEvent(event)
        if self.value() == self.minimum():
            self.lineEdit().selectAll()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if self.value() == self.minimum():
            self.setSpecialValueText("不限")


class CrawlResultsModel(QAbstractTableModel):
    _headers = ("商品", "价格")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._records: list[ProductRecord] = []
        self._rows_by_key: dict[str, int] = {}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._headers)

    def headerData(self, section: int, orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self._headers[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        record = self._records[index.row()]
        if role == Qt.UserRole:
            return record.url
        if role == Qt.TextAlignmentRole and index.column() == 1:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            details = "｜".join(
                part for part in (record.region, record.condition, record.publish_or_change) if part
            )
            return "\n".join(part for part in (record.title, details, record.url) if part)
        if role != Qt.DisplayRole:
            return None
        if index.column() == 0:
            details = "｜".join(
                part for part in (record.region, record.condition) if part
            )
            if record.appearances > 1:
                details = "｜".join(part for part in (details, f"出现 {record.appearances} 次") if part)
            return "\n".join(part for part in (record.title or "未命名商品", details) if part)
        if index.column() == 1:
            return "¥—" if record.price is None else f"¥{record.price:g}"
        return None

    def clear(self) -> None:
        self.beginResetModel()
        self._records.clear()
        self._rows_by_key.clear()
        self.endResetModel()

    def upsert(self, records: list[ProductRecord]) -> int:
        for source in records:
            record = ProductRecord.from_dict(source.to_dict())
            row = self._rows_by_key.get(record.key)
            if row is None:
                row = len(self._records)
                self.beginInsertRows(QModelIndex(), row, row)
                self._records.append(record)
                self._rows_by_key[record.key] = row
                self.endInsertRows()
            else:
                self._records[row] = record
                self.dataChanged.emit(self.index(row, 0), self.index(row, len(self._headers) - 1))
        return len(self._records)


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        default_output_dir: Path | None = None,
        profile_dir: Path | None = None,
        monitor_db_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("闲鱼商品采集与定时推送")
        self.setWindowIcon(application_icon())
        self.resize(1160, 860)
        self.setMinimumSize(980, 740)
        self._crawl_worker: CrawlWorker | None = None
        self._login_worker: LoginWorker | None = None
        self._last_output: Path | None = None
        self._notification_test_worker: NotificationTestWorker | None = None
        self._notification_delivery_worker: NotificationDeliveryWorker | None = None
        self._feishu_binding_worker: FeishuBindingWorker | None = None
        self._update_check_worker: UpdateCheckWorker | None = None
        self._update_preparation_worker: UpdatePreparationWorker | None = None
        self._update_check_is_manual = False
        self._scheduled_collection: ScheduledCollectionConfig | None = None
        self._active_run_is_scheduled = False
        self._schedule_timer = QTimer(self)
        self._schedule_timer.setSingleShot(True)
        self._schedule_timer.timeout.connect(self._run_scheduled_collection)
        self._next_scheduled_at: datetime | None = None
        self._force_exit = False
        self._default_output_dir = default_output_dir or self._documents_output_dir()
        self._profile_dir = (profile_dir or default_profile_dir()).resolve()
        self._monitor_store = MonitorStore(monitor_db_path)
        self._update_service = UpdateService(__version__)
        self._build_ui()
        self._refresh_login_state()
        self._set_running_state(False)
        self._load_notification_settings()
        self._load_scheduled_collection()
        self._setup_tray()
        if self._should_auto_check_for_update():
            QTimer.singleShot(2_000, self._check_for_update)

    @staticmethod
    def _documents_output_dir() -> Path:
        documents = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        base = Path(documents) if documents else Path.home() / "Documents"
        return base / "闲鱼采集结果"

    def _should_auto_check_for_update(self) -> bool:
        checked_at = self._monitor_store.load_update_check_at()
        return checked_at is None or datetime.now() - checked_at >= timedelta(days=1)

    @staticmethod
    def _update_staging_dir() -> Path:
        local_data = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
        root = Path(local_data) if local_data else Path.home() / "AppData" / "Local" / "GoofishLinkCollector"
        return root / "updates"

    @staticmethod
    def _packaged_install_dir() -> Path | None:
        executable = Path(sys.executable).resolve()
        if getattr(sys, "frozen", False) and executable.name == "XianyuLinkCollector.exe":
            return executable.parent
        return None

    def _check_for_update(self, *, manual: bool = False) -> None:
        if self._crawl_worker is not None and self._crawl_worker.isRunning():
            if manual:
                QMessageBox.information(self, "暂不能更新", "采集进行中，请结束当前任务后再更新。")
            return
        if self._update_check_worker is not None and self._update_check_worker.isRunning():
            return
        self._update_check_is_manual = manual
        self.update_check_button.setEnabled(False)
        self.update_status_label.setText("正在检查更新…")
        self._update_check_worker = UpdateCheckWorker(self._update_service)
        self._update_check_worker.completed.connect(self._update_check_completed)
        self._update_check_worker.failed.connect(self._update_check_failed)
        self._update_check_worker.finished.connect(self._update_check_finished)
        self._update_check_worker.start()

    def _update_check_completed(self, update: UpdateInfo | None) -> None:
        self._monitor_store.save_update_check_at(datetime.now())
        if update is None:
            self.update_status_label.setText(f"当前已是最新 v{__version__}")
            if self._update_check_is_manual:
                QMessageBox.information(self, "已是最新版本", f"当前版本 v{__version__} 已是最新版本。")
            return
        self.update_status_label.setText(f"发现新版本 v{update.version}")
        install_dir = self._packaged_install_dir()
        if install_dir is None:
            self._append_log(f"发现 v{update.version}，开发环境不执行自动安装。")
            if self._update_check_is_manual:
                QMessageBox.information(self, "发现新版本", f"发现 v{update.version}，请从发布页下载：\n{update.release_url}")
            return
        notes = update.notes[:900] or "本次发布未填写更新说明。"
        answer = QMessageBox.question(
            self,
            "发现新版本",
            f"发现 v{update.version}。\n\n{notes}\n\n现在下载、校验并重启更新吗？\n"
            "旧版本会保留为本机回退备份。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self._prepare_update(update, install_dir)

    def _update_check_failed(self, message: str) -> None:
        self.update_status_label.setText(f"当前版本 v{__version__}")
        self._append_log(f"更新检查失败：{message}")
        if self._update_check_is_manual:
            QMessageBox.warning(self, "检查更新失败", f"未下载任何文件。\n{message}")

    def _update_check_finished(self) -> None:
        self._update_check_worker = None
        if self._update_preparation_worker is None:
            self.update_check_button.setEnabled(self._crawl_worker is None or not self._crawl_worker.isRunning())

    def _prepare_update(self, update: UpdateInfo, install_dir: Path) -> None:
        self.update_status_label.setText(f"正在下载 v{update.version}…")
        self._update_preparation_worker = UpdatePreparationWorker(
            self._update_service,
            update,
            self._update_staging_dir(),
        )
        self._update_preparation_worker.completed.connect(
            lambda prepared: self._update_preparation_completed(prepared, install_dir)
        )
        self._update_preparation_worker.failed.connect(self._update_preparation_failed)
        self._update_preparation_worker.finished.connect(self._update_preparation_finished)
        self._update_preparation_worker.start()

    def _update_preparation_completed(self, prepared: PreparedUpdate, install_dir: Path) -> None:
        try:
            UpdateService.launch_installer(
                prepared,
                install_dir=install_dir,
                parent_pid=os.getpid(),
            )
        except OSError as exc:
            self._update_preparation_failed(str(exc))
            return
        self.update_status_label.setText(f"正在安装 v{prepared.info.version}…")
        self._append_log(f"更新包已校验，正在退出并安装 v{prepared.info.version}。")
        QTimer.singleShot(100, QApplication.instance().quit)

    def _update_preparation_failed(self, message: str) -> None:
        self.update_status_label.setText(f"当前版本 v{__version__}")
        self._append_log(f"更新下载或校验失败：{message}")
        QMessageBox.warning(self, "更新未安装", f"旧版本未被改动。\n{message}")

    def _update_preparation_finished(self) -> None:
        self._update_preparation_worker = None
        self.update_check_button.setEnabled(self._crawl_worker is None or not self._crawl_worker.isRunning())

    def _build_ui(self) -> None:
        self.content_scroll = QScrollArea(self)
        self.content_scroll.setObjectName("contentScroll")
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QFrame.NoFrame)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        central = QWidget(self)
        central.setObjectName("appRoot")
        # The tab bar consumes part of the window; 760 keeps the original page
        # scroll-free at 1160x860 while preserving a scrollbar at the 980x740 minimum.
        central.setMinimumHeight(760)
        central.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.content_scroll.setWidget(central)
        self.setCentralWidget(self.content_scroll)
        root = QVBoxLayout(central)
        root.setSizeConstraint(QLayout.SetNoConstraint)
        root.setContentsMargins(28, 14, 28, 14)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(14)
        brand_mark = QLabel("闲")
        brand_mark.setObjectName("brandMark")
        brand_mark.setFixedSize(48, 48)
        brand_mark.setAlignment(Qt.AlignCenter)
        header.addWidget(brand_mark)

        header_copy = QVBoxLayout()
        header_copy.setSpacing(2)
        title = QLabel("闲鱼商品采集与定时推送")
        title.setObjectName("pageTitle")
        subtitle = QLabel("首次扫码后自动复用本机登录状态；可单次采集或按间隔采集并推送飞书。")
        subtitle.setObjectName("pageSubtitle")
        header_copy.addWidget(title)
        subtitle_row = QHBoxLayout()
        subtitle_row.setSpacing(8)
        subtitle_row.addWidget(subtitle, 1)
        self.update_status_label = QLabel(f"当前版本 v{__version__}")
        self.update_status_label.setObjectName("updateStatus")
        self.update_check_button = QPushButton("检查更新")
        self.update_check_button.setObjectName("updateCheckButton")
        self.update_check_button.clicked.connect(lambda: self._check_for_update(manual=True))
        subtitle_row.addWidget(self.update_status_label)
        subtitle_row.addWidget(self.update_check_button)
        header_copy.addLayout(subtitle_row)
        header.addLayout(header_copy, 1)

        login_panel = QFrame()
        login_panel.setObjectName("loginPanel")
        login_layout = QHBoxLayout(login_panel)
        login_layout.setContentsMargins(14, 9, 10, 9)
        login_layout.setSpacing(14)
        login_copy = QVBoxLayout()
        login_copy.setSpacing(1)
        self.login_state_value = QLabel("正在检查登录状态")
        self.login_state_value.setObjectName("loginStateValue")
        self.login_state_hint = QLabel("请稍候")
        self.login_state_hint.setObjectName("loginStateHint")
        login_copy.addWidget(self.login_state_value)
        login_copy.addWidget(self.login_state_hint)
        login_layout.addLayout(login_copy)
        self.login_button = QPushButton("登录 / 切换账号")
        self.login_button.setObjectName("loginButton")
        self.login_button.clicked.connect(self._start_login)
        login_layout.addWidget(self.login_button)
        header.addWidget(login_panel)
        root.addLayout(header)

        workspace = QHBoxLayout()
        workspace.setSpacing(16)

        settings_panel = QFrame()
        settings_panel.setObjectName("surfacePanel")
        settings_layout = QVBoxLayout(settings_panel)
        settings_layout.setContentsMargins(20, 18, 20, 20)
        settings_layout.setSpacing(13)

        task_title = QLabel("采集设置")
        task_title.setObjectName("sectionTitle")
        task_hint = QLabel("输入关键词并设置停止上限")
        task_hint.setObjectName("sectionHint")
        settings_layout.addWidget(task_title)
        settings_layout.addWidget(task_hint)

        keyword_label = QLabel("搜索关键词")
        keyword_label.setObjectName("fieldLabel")
        settings_layout.addWidget(keyword_label)

        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("例如：华为耳机、相机、显卡")
        self.keyword_edit.setClearButtonEnabled(True)
        settings_layout.addWidget(self.keyword_edit)

        limits = QGridLayout()
        limits.setHorizontalSpacing(14)
        limits.setVerticalSpacing(6)
        max_pages_label = QLabel("最大页数")
        max_pages_label.setObjectName("fieldLabel")
        max_items_label = QLabel("最大商品数")
        max_items_label.setObjectName("fieldLabel")
        limits.addWidget(max_pages_label, 0, 0)
        limits.addWidget(max_items_label, 0, 1)
        self.max_pages_spin = QSpinBox()
        self.max_pages_spin.setRange(1, 200)
        self.max_pages_spin.setValue(50)
        self.max_pages_spin.setSuffix(" 页")
        self.max_items_spin = QSpinBox()
        self.max_items_spin.setRange(0, 100_000)
        self.max_items_spin.setValue(0)
        self.max_items_spin.setSpecialValueText("不限")
        limits.addWidget(self.max_pages_spin, 1, 0)
        limits.addWidget(self.max_items_spin, 1, 1)
        limits.setColumnStretch(0, 1)
        limits.setColumnStretch(1, 1)
        settings_layout.addLayout(limits)

        output_label = QLabel("结果保存位置")
        output_label.setObjectName("fieldLabel")
        settings_layout.addWidget(output_label)
        output_layout = QHBoxLayout()
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(10)
        self.output_edit = QLineEdit(str(self._default_output_dir))
        self.output_browse_button = QPushButton("选择目录")
        self.output_browse_button.setObjectName("compactButton")
        self.output_browse_button.clicked.connect(self._choose_output_dir)
        output_layout.addWidget(self.output_edit, 1)
        output_layout.addWidget(self.output_browse_button)
        settings_layout.addLayout(output_layout)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.HLine)
        settings_layout.addWidget(divider)

        filter_title = QLabel("搜索筛选")
        filter_title.setObjectName("sectionTitle")
        filter_hint = QLabel("不选择时按闲鱼默认条件采集")
        filter_hint.setObjectName("sectionHint")
        settings_layout.addWidget(filter_title)
        settings_layout.addWidget(filter_hint)

        filter_layout = QGridLayout()
        filter_layout.setHorizontalSpacing(12)
        filter_layout.setVerticalSpacing(7)

        self.min_price_spin = OptionalPriceSpinBox()
        self.min_price_spin.setRange(0, 100_000_000)
        self.min_price_spin.setDecimals(2)
        self.min_price_spin.setSingleStep(10)
        self.min_price_spin.setPrefix("¥")
        self.min_price_spin.setMinimumWidth(140)
        self.min_price_spin.setMaximumWidth(170)
        self.max_price_spin = OptionalPriceSpinBox()
        self.max_price_spin.setRange(0, 100_000_000)
        self.max_price_spin.setDecimals(2)
        self.max_price_spin.setSingleStep(10)
        self.max_price_spin.setPrefix("¥")
        self.max_price_spin.setMinimumWidth(140)
        self.max_price_spin.setMaximumWidth(170)

        self.region_combo = QComboBox()
        self.region_combo.setEditable(True)
        self.region_combo.addItems(
            [
                "全国", "北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林",
                "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北",
                "湖南", "广东", "广西", "海南", "四川", "贵州", "云南", "西藏", "陕西",
                "甘肃", "青海", "宁夏", "新疆", "内蒙古", "香港", "澳门", "台湾",
            ]
        )
        if self.region_combo.lineEdit() is not None:
            self.region_combo.lineEdit().setPlaceholderText("省份或城市")
        self.region_combo.setMinimumWidth(150)
        self.publish_combo = QComboBox()
        self.publish_combo.addItems(["不限", "最新", "1天内", "3天内", "7天内", "14天内"])
        self.publish_combo.setMinimumWidth(110)
        self.publish_combo.setMaximumWidth(140)

        for column, text in enumerate(("最低价", "最高价", "地区", "发布时间")):
            label = QLabel(text)
            label.setObjectName("fieldLabel")
            filter_layout.addWidget(label, 0, column)
        filter_layout.addWidget(self.min_price_spin, 1, 0)
        filter_layout.addWidget(self.max_price_spin, 1, 1)
        filter_layout.addWidget(self.region_combo, 1, 2)
        filter_layout.addWidget(self.publish_combo, 1, 3)
        filter_layout.setColumnStretch(0, 0)
        filter_layout.setColumnStretch(1, 0)
        filter_layout.setColumnStretch(2, 1)
        filter_layout.setColumnStretch(3, 0)
        settings_layout.addLayout(filter_layout)

        self.personal_checkbox = QCheckBox("个人闲置")
        self.inspection_checkbox = QCheckBox("验货宝")
        self.free_shipping_checkbox = QCheckBox("包邮")
        self.brand_new_checkbox = QCheckBox("全新")
        conditions_label = QLabel("商品条件")
        conditions_label.setObjectName("fieldLabel")
        settings_layout.addWidget(conditions_label)
        conditions = QHBoxLayout()
        conditions.setSpacing(8)
        for checkbox in (
            self.personal_checkbox,
            self.inspection_checkbox,
            self.free_shipping_checkbox,
            self.brand_new_checkbox,
        ):
            conditions.addWidget(checkbox)
        conditions.addStretch(1)
        settings_layout.addLayout(conditions)
        settings_layout.addStretch(1)
        workspace.addWidget(settings_panel, 1)

        sidebar = QWidget()
        sidebar.setMinimumWidth(300)
        sidebar.setMaximumWidth(340)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(14)

        status_panel = QFrame()
        status_panel.setObjectName("statusPanel")
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(18, 17, 18, 18)
        status_layout.setSpacing(12)
        status_title = QLabel("运行状态")
        status_title.setObjectName("sectionTitle")
        self.status_value = QLabel("等待开始")
        self.status_value.setObjectName("statusTitle")
        self.status_value.setWordWrap(True)
        status_layout.addWidget(status_title)
        status_layout.addWidget(self.status_value)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        metric_names = ("当前页", "原始记录", "唯一商品")
        self.page_value = QLabel("0")
        self.raw_value = QLabel("0")
        self.unique_value = QLabel("0")
        metric_values = (self.page_value, self.raw_value, self.unique_value)
        for column, (name, value) in enumerate(zip(metric_names, metric_values)):
            caption = QLabel(name)
            caption.setObjectName("metricLabel")
            value.setObjectName("metricValue")
            metrics.addWidget(caption, 0, column)
            metrics.addWidget(value, 1, column)
            metrics.setColumnStretch(column, 1)
        status_layout.addLayout(metrics)
        sidebar_layout.addWidget(status_panel)

        action_panel = QFrame()
        action_panel.setObjectName("actionPanel")
        action_layout = QVBoxLayout(action_panel)
        action_layout.setContentsMargins(18, 17, 18, 18)
        action_layout.setSpacing(10)
        action_title = QLabel("任务操作")
        action_title.setObjectName("sectionTitle")
        action_layout.addWidget(action_title)

        self.start_button = QPushButton("开始采集")
        self.start_button.setObjectName("primaryButton")
        self.pause_button = QPushButton("暂停")
        self.resume_button = QPushButton("继续")
        self.stop_button = QPushButton("停止并导出")
        self.open_button = QPushButton("打开结果")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self._start_crawl)
        self.pause_button.clicked.connect(self._pause_crawl)
        self.resume_button.clicked.connect(self._resume_crawl)
        self.stop_button.clicked.connect(self._stop_crawl)
        self.open_button.clicked.connect(self._open_output)
        action_layout.addWidget(self.start_button)
        pause_row = QHBoxLayout()
        pause_row.setSpacing(10)
        pause_row.addWidget(self.pause_button)
        pause_row.addWidget(self.resume_button)
        action_layout.addLayout(pause_row)
        action_layout.addWidget(self.stop_button)
        action_layout.addWidget(self.open_button)
        sidebar_layout.addWidget(action_panel)

        schedule_panel = QFrame()
        schedule_panel.setObjectName("schedulePanel")
        schedule_layout = QVBoxLayout(schedule_panel)
        schedule_layout.setContentsMargins(18, 12, 18, 12)
        schedule_layout.setSpacing(6)
        schedule_title = QLabel("定时采集")
        schedule_title.setObjectName("sectionTitle")
        schedule_title.setToolTip("按当前采集条件循环执行；每次完成后推送飞书。")
        schedule_layout.addWidget(schedule_title)
        schedule_controls = QHBoxLayout()
        self.schedule_interval_combo = QComboBox()
        for minutes in (5, 10, 15, 30, 60):
            self.schedule_interval_combo.addItem(f"{minutes} 分钟", minutes)
        self.schedule_interval_combo.setCurrentText("30 分钟")
        self.feishu_settings_button = QPushButton("设置飞书 / 绑定")
        self.feishu_settings_button.clicked.connect(self._show_feishu_settings)
        schedule_controls.addWidget(self.schedule_interval_combo)
        schedule_controls.addWidget(self.feishu_settings_button)
        schedule_layout.addLayout(schedule_controls)
        self.feishu_status_label = QLabel("飞书：尚未配置")
        self.feishu_status_label.setObjectName("sectionHint")
        schedule_layout.addWidget(self.feishu_status_label)
        self.schedule_status_label = QLabel("定时采集：未启动")
        self.schedule_status_label.setObjectName("sectionHint")
        self.schedule_status_label.setWordWrap(True)
        schedule_layout.addWidget(self.schedule_status_label)
        schedule_buttons = QHBoxLayout()
        self.schedule_start_button = QPushButton("启动定时采集")
        self.schedule_start_button.setObjectName("primaryButton")
        self.schedule_stop_button = QPushButton("停止")
        self.schedule_start_button.clicked.connect(self._start_scheduled_collection)
        self.schedule_stop_button.clicked.connect(self._stop_scheduled_collection)
        schedule_buttons.addWidget(self.schedule_start_button, 1)
        schedule_buttons.addWidget(self.schedule_stop_button)
        schedule_layout.addLayout(schedule_buttons)
        sidebar_layout.addWidget(schedule_panel)

        self.result_panel = QFrame()
        self.result_panel.setObjectName("resultPanel")
        self.result_panel.setFixedHeight(190)
        result_layout = QVBoxLayout(self.result_panel)
        result_layout.setContentsMargins(14, 12, 14, 14)
        result_layout.setSpacing(8)
        result_header = QHBoxLayout()
        self.result_count_label = QLabel("采集结果（0）")
        self.result_count_label.setObjectName("sectionTitle")
        result_header.addWidget(self.result_count_label)
        result_header.addStretch(1)
        result_layout.addLayout(result_header)
        self.result_model = CrawlResultsModel(self)
        self.result_view = QTableView()
        self.result_view.setObjectName("resultTable")
        self.result_view.setModel(self.result_model)
        self.result_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.result_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result_view.setAlternatingRowColors(True)
        self.result_view.setWordWrap(True)
        self.result_view.setTextElideMode(Qt.ElideRight)
        self.result_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.result_view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.result_view.verticalHeader().hide()
        self.result_view.verticalHeader().setDefaultSectionSize(44)
        result_header_view = self.result_view.horizontalHeader()
        result_header_view.setSectionResizeMode(0, QHeaderView.Stretch)
        result_header_view.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.result_view.doubleClicked.connect(self._open_result_item)
        result_layout.addWidget(self.result_view)
        sidebar_layout.addWidget(self.result_panel)
        self.result_panel.hide()
        sidebar_layout.addStretch(1)
        workspace.addWidget(sidebar)
        root.addLayout(workspace)

        log_panel = QFrame()
        log_panel.setObjectName("logPanel")
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(18, 14, 18, 16)
        log_layout.setSpacing(9)
        log_header = QHBoxLayout()
        log_title = QLabel("运行日志")
        log_title.setObjectName("sectionTitle")
        log_hint = QLabel("显示关键进度、重试和导出位置")
        log_hint.setObjectName("sectionHint")
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        log_header.addWidget(log_hint)
        log_layout.addLayout(log_header)
        self.log_view = QTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(55)
        self.log_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.log_view.setPlaceholderText("任务开始后，关键进度会显示在这里。")
        self.log_view.document().setMaximumBlockCount(1000)
        log_layout.addWidget(self.log_view)
        root.addWidget(log_panel, 1)

        self.setStyleSheet(
            """
            QWidget {
                color: #172033; font-family: "Microsoft YaHei UI", "微软雅黑";
                font-size: 14px;
            }
            QMainWindow, QScrollArea#contentScroll, QScrollArea#contentScroll > QWidget,
            QWidget#appRoot { background: #f3f5f7; border: none; }
            QFrame#surfacePanel, QFrame#statusPanel, QFrame#actionPanel, QFrame#schedulePanel, QFrame#resultPanel,
            QFrame#logPanel, QFrame#loginPanel {
                background: #ffffff; border: 1px solid #dbe1e8; border-radius: 12px;
            }
            QFrame#loginPanel { background: #fffdf3; border-color: #ead47c; }
            QFrame#divider { border: none; border-top: 1px solid #e8edf2; }
            QLabel#brandMark {
                background: #ffd84d; color: #172033; border-radius: 12px;
                font-size: 22px; font-weight: 800;
            }
            QLabel#pageTitle { font-size: 24px; font-weight: 800; color: #101827; }
            QLabel#pageSubtitle, QLabel#sectionHint, QLabel#loginStateHint,
            QLabel#metricLabel { color: #667386; }
            QLabel#pageSubtitle { font-size: 14px; }
            QLabel#updateStatus { font-size: 12px; color: #667386; }
            QPushButton#updateCheckButton { min-height: 22px; padding: 4px 10px; font-size: 12px; }
            QLabel#sectionTitle { font-size: 16px; font-weight: 700; color: #172033; }
            QLabel#fieldLabel {
                min-height: 18px; font-size: 13px; font-weight: 600; color: #465468;
            }
            QLabel#loginStateValue { font-size: 14px; font-weight: 700; }
            QLabel#loginStateHint { font-size: 12px; }
            QLabel#statusTitle { font-size: 19px; font-weight: 800; color: #172033; }
            QLabel#metricValue { font-size: 22px; font-weight: 800; color: #172033; }
            QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox, QTextEdit {
                border: 1px solid #cbd5df; border-radius: 8px; padding: 9px 11px;
                background: #ffffff; color: #172033;
                selection-background-color: #f8d447; selection-color: #172033;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus,
            QComboBox:focus, QTextEdit:focus { border-color: #d7aa00; }
            QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox { min-height: 24px; }
            QLineEdit { min-height: 24px; }
            QTextEdit#logView {
                border: none; border-top: 1px solid #e8edf2; border-radius: 0;
                padding: 12px 2px 2px 2px; background: #ffffff;
            }
            QComboBox QLineEdit {
                border: none; padding: 0; background: #ffffff; color: #172033;
                selection-background-color: #f8d447; selection-color: #172033;
            }
            QComboBox QAbstractItemView {
                background: #ffffff; color: #172033; border: 1px solid #cbd5df;
                selection-background-color: #f8d447; selection-color: #172033;
                outline: none;
            }
            QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QTimeEdit:disabled,
            QComboBox:disabled, QTextEdit:disabled {
                background: #eef2f5; color: #98a4b3;
            }
            QCheckBox {
                spacing: 8px; padding: 8px 11px; border: 1px solid #dbe1e8;
                border-radius: 8px; background: #f8fafb; min-height: 26px;
            }
            QCheckBox:hover { background: #f1f4f7; border-color: #bdc8d4; }
            QCheckBox:checked { background: #fff8d7; border-color: #d7aa00; }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QPushButton {
                min-height: 42px; padding: 0 15px; border-radius: 8px;
                border: 1px solid #bdc8d4; background: #ffffff; color: #172033;
                font-weight: 600;
            }
            QPushButton:hover { background: #f1f4f7; border-color: #9eabb9; }
            QPushButton:pressed { background: #e8edf2; }
            QPushButton:focus { border-color: #d7aa00; }
            QPushButton:disabled { color: #9aa5b1; background: #edf1f4; border-color: #dbe1e8; }
            QPushButton#primaryButton {
                min-height: 46px; background: #ffd84d; border-color: #d8ad00;
                color: #172033; font-size: 15px; font-weight: 800;
            }
            QPushButton#primaryButton:hover { background: #ffcf24; }
            QPushButton#primaryButton:pressed { background: #f2c100; }
            QPushButton#primaryButton:disabled { background: #f1e6b3; color: #9a8b52; }
            QPushButton#loginButton { min-height: 42px; }
            QPushButton#compactButton { min-height: 42px; }
            """
        )
        self._build_feishu_dialog()

    def _build_feishu_dialog(self) -> None:
        self._feishu_dialog = QDialog(self)
        self._feishu_dialog.setWindowTitle("飞书推送设置")
        self._feishu_dialog.setWindowIcon(application_icon())
        self._feishu_dialog.setMinimumWidth(560)
        dialog_layout = QVBoxLayout(self._feishu_dialog)
        dialog_layout.setContentsMargins(22, 20, 22, 20)
        dialog_layout.setSpacing(12)

        title = QLabel("飞书推送")
        title.setObjectName("sectionTitle")
        dialog_layout.addWidget(title)
        hint = QLabel(
            "定时采集每次导出 Excel 后，会向已绑定的飞书用户发送本次结果摘要、最多 10 条商品主图和真实商品链接。"
        )
        hint.setObjectName("sectionHint")
        hint.setWordWrap(True)
        dialog_layout.addWidget(hint)
        self.feishu_setup_status = QLabel()
        self.feishu_setup_status.setObjectName("sectionHint")
        self.feishu_setup_status.setWordWrap(True)
        dialog_layout.addWidget(self.feishu_setup_status)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.feishu_app_id_edit = QLineEdit()
        self.feishu_app_id_edit.setPlaceholderText("cli_xxx")
        self.feishu_secret_edit = QLineEdit()
        self.feishu_secret_edit.setEchoMode(QLineEdit.Password)
        self.feishu_secret_edit.setPlaceholderText("App Secret（仅加密保存在本机）")
        self.feishu_binding_label = QLabel("未绑定接收用户")
        self.feishu_binding_label.setObjectName("sectionHint")
        grid.addWidget(QLabel("飞书 App ID"), 0, 0)
        grid.addWidget(self.feishu_app_id_edit, 0, 1)
        grid.addWidget(QLabel("App Secret"), 1, 0)
        grid.addWidget(self.feishu_secret_edit, 1, 1)
        grid.addWidget(self.feishu_binding_label, 2, 0, 1, 2)
        grid.setColumnStretch(1, 1)
        dialog_layout.addLayout(grid)

        actions = QHBoxLayout()
        self.feishu_save_button = QPushButton("保存飞书配置")
        self.feishu_bind_button = QPushButton("开始绑定（5分钟）")
        self.feishu_unbind_button = QPushButton("解绑")
        self.feishu_test_button = QPushButton("测试飞书")
        self.feishu_help_button = QPushButton("配置向导")
        self.feishu_save_button.clicked.connect(self._save_feishu_settings)
        self.feishu_bind_button.clicked.connect(self._start_feishu_binding)
        self.feishu_unbind_button.clicked.connect(self._unbind_feishu)
        self.feishu_test_button.clicked.connect(self._test_notification)
        self.feishu_help_button.clicked.connect(self._show_feishu_guide)
        for button in (
            self.feishu_save_button,
            self.feishu_bind_button,
            self.feishu_unbind_button,
            self.feishu_test_button,
            self.feishu_help_button,
        ):
            actions.addWidget(button)
        dialog_layout.addLayout(actions)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self._feishu_dialog.close)
        dialog_layout.addWidget(close_button)

    def build_config(self) -> CrawlConfig:
        keyword = self.keyword_edit.text().strip()
        if not keyword:
            raise ValueError("关键词不能为空")
        output_text = self.output_edit.text().strip()
        if not output_text:
            raise ValueError("请选择输出目录")
        min_price = self.min_price_spin.value() or None
        max_price = self.max_price_spin.value() or None
        region = self.region_combo.currentText().strip()
        filters = SearchFilters(
            min_price=min_price,
            max_price=max_price,
            region="" if region == "全国" else region,
            published_within=(
                "" if self.publish_combo.currentText() == "不限" else self.publish_combo.currentText()
            ),
            personal_only=self.personal_checkbox.isChecked(),
            inspection_only=self.inspection_checkbox.isChecked(),
            free_shipping=self.free_shipping_checkbox.isChecked(),
            brand_new=self.brand_new_checkbox.isChecked(),
        )
        filters.validate()
        return CrawlConfig(
            keyword=keyword,
            max_pages=self.max_pages_spin.value(),
            max_items=self.max_items_spin.value(),
            output_dir=Path(output_text),
            filters=filters,
        )

    def _refresh_login_state(self) -> bool:
        saved = profile_has_saved_login(self._profile_dir)
        if saved:
            self.login_state_value.setText("登录状态已保存")
            self.login_state_hint.setText("可直接开始采集，失效时会提示重新扫码")
            self.login_state_value.setStyleSheet("color: #13795b;")
        else:
            self.login_state_value.setText("首次使用需要登录")
            self.login_state_hint.setText("扫码一次后会保存在这台电脑")
            self.login_state_value.setStyleSheet("color: #8a6410;")
        return saved

    def _choose_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择 Excel 输出目录", self.output_edit.text()
        )
        if selected:
            self.output_edit.setText(selected)

    def _clear_result_records(self) -> None:
        self.result_model.clear()
        self.result_count_label.setText("采集结果（0）")
        self.unique_value.setText("0")
        self.result_panel.hide()

    def _update_result_records(self, records: list[ProductRecord]) -> None:
        if not records:
            return
        count = self.result_model.upsert(records)
        self.result_count_label.setText(f"采集结果（{count:,}）")
        self.unique_value.setText(f"{count:,}")
        self.result_panel.show()

    def _open_result_item(self, index: QModelIndex) -> None:
        url = str(index.data(Qt.UserRole) or "")
        if url.startswith("https://"):
            QDesktopServices.openUrl(QUrl(url))

    def _append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{stamp}] {message}")

    def _load_scheduled_collection(self) -> None:
        saved = self._monitor_store.load_scheduled_collection()
        if saved is None:
            self._update_schedule_ui()
            return
        self._scheduled_collection = saved
        self._apply_scheduled_config_to_form(saved.crawl_config)
        self.schedule_interval_combo.setCurrentText(f"{saved.interval_minutes} 分钟")
        self._update_schedule_ui()
        if saved.enabled:
            self._schedule_next_run(log_message=False)

    def _apply_scheduled_config_to_form(self, config: CrawlConfig) -> None:
        self.keyword_edit.setText(config.keyword)
        self.max_pages_spin.setValue(config.max_pages)
        self.max_items_spin.setValue(config.max_items)
        self.output_edit.setText(str(config.output_dir))
        self.min_price_spin.setValue(config.filters.min_price or 0)
        self.max_price_spin.setValue(config.filters.max_price or 0)
        self.region_combo.setCurrentText(config.filters.region or "全国")
        self.publish_combo.setCurrentText(config.filters.published_within or "不限")
        self.personal_checkbox.setChecked(config.filters.personal_only)
        self.inspection_checkbox.setChecked(config.filters.inspection_only)
        self.free_shipping_checkbox.setChecked(config.filters.free_shipping)
        self.brand_new_checkbox.setChecked(config.filters.brand_new)

    def _save_scheduled_collection(self, *, enabled: bool) -> ScheduledCollectionConfig:
        config = ScheduledCollectionConfig(
            crawl_config=self.build_config(),
            interval_minutes=int(self.schedule_interval_combo.currentData()),
            enabled=enabled,
        )
        self._monitor_store.save_scheduled_collection(config)
        self._scheduled_collection = config
        self._update_schedule_ui()
        return config

    def _start_scheduled_collection(self) -> None:
        if self._is_crawling() or (
            self._login_worker is not None and self._login_worker.isRunning()
        ):
            QMessageBox.information(self, "浏览器正在使用", "请等待当前采集或登录结束后再启动定时采集。")
            return
        try:
            scheduled = self._save_scheduled_collection(enabled=True)
            FeishuProvider(self._monitor_store.load_feishu_config()).validate_config()
        except ValueError as exc:
            self._stop_scheduled_collection(show_message=False)
            QMessageBox.warning(
                self,
                "飞书尚未就绪",
                f"{exc}\n请先点击“设置飞书 / 绑定”完成配置和测试。",
            )
            return
        self._append_log(
            f"已启动定时采集：每 {scheduled.interval_minutes} 分钟执行一次；现在开始首次采集。"
        )
        self._schedule_next_run(delay_seconds=0, log_message=False)

    def _stop_scheduled_collection(self, *, show_message: bool = True) -> None:
        self._schedule_timer.stop()
        if self._scheduled_collection is not None:
            disabled = ScheduledCollectionConfig(
                crawl_config=self._scheduled_collection.crawl_config,
                interval_minutes=self._scheduled_collection.interval_minutes,
                enabled=False,
            )
            self._monitor_store.save_scheduled_collection(disabled)
            self._scheduled_collection = disabled
        self._next_scheduled_at = None
        self._update_schedule_ui()
        if show_message:
            self._append_log("定时采集已停止；当前正在运行的采集会继续导出，但不会再推送飞书。")

    def _schedule_next_run(
        self, *, delay_seconds: int | None = None, log_message: bool = True
    ) -> None:
        scheduled = self._scheduled_collection
        if scheduled is None or not scheduled.enabled:
            self._update_schedule_ui()
            return
        delay = delay_seconds
        if delay is None:
            delay = scheduled.interval_minutes * 60
        self._next_scheduled_at = datetime.now().replace(microsecond=0)
        self._next_scheduled_at = self._next_scheduled_at + timedelta(seconds=delay)
        self._schedule_timer.start(max(0, delay) * 1000)
        self._update_schedule_ui()
        if log_message:
            self._append_log(
                f"下次定时采集：{self._next_scheduled_at.strftime('%H:%M')}。"
            )

    def _run_scheduled_collection(self) -> None:
        scheduled = self._scheduled_collection
        if scheduled is None or not scheduled.enabled:
            return
        if self._is_crawling() or (
            self._login_worker is not None and self._login_worker.isRunning()
        ):
            self._schedule_next_run(delay_seconds=30, log_message=False)
            return
        self._next_scheduled_at = None
        self._update_schedule_ui()
        self._append_log(f"定时采集开始：关键词“{scheduled.crawl_config.keyword}”。")
        self._start_crawl(config=scheduled.crawl_config, scheduled=True)

    def _update_schedule_ui(self) -> None:
        scheduled = self._scheduled_collection
        enabled = bool(scheduled and scheduled.enabled)
        self.schedule_interval_combo.setEnabled(not self._is_crawling() and not enabled)
        self.schedule_start_button.setEnabled(not self._is_crawling() and not enabled)
        self.schedule_stop_button.setEnabled(enabled)
        if not enabled:
            self.schedule_status_label.setText("定时采集：未启动")
        elif self._is_crawling():
            self.schedule_status_label.setText("定时采集：本次正在执行")
        elif self._next_scheduled_at is not None:
            self.schedule_status_label.setText(
                f"定时采集：已启动，下次 {self._next_scheduled_at.strftime('%H:%M')}"
            )
        else:
            self.schedule_status_label.setText("定时采集：已启动，等待执行")

    def _start_login(self) -> None:
        if self._login_worker is not None and self._login_worker.isRunning():
            return
        if self._is_crawling():
            QMessageBox.information(self, "采集正在运行", "请先停止当前采集后再打开登录窗口。")
            return
        self._schedule_timer.stop()
        if self._refresh_login_state():
            self._append_log("正在打开已保存的闲鱼账号；需要时可在浏览器中切换账号。")
        else:
            self._append_log("正在打开专用浏览器，请完成首次扫码登录。")
        self.login_button.setEnabled(False)
        self._login_worker = LoginWorker(self._profile_dir)
        self._login_worker.log.connect(self._append_log)
        self._login_worker.failed.connect(self._login_failed)
        self._login_worker.finished.connect(self._login_finished)
        self._login_worker.start()

    def _login_failed(self, message: str) -> None:
        self._append_log(f"登录窗口错误：{message}")
        QMessageBox.warning(self, "登录窗口错误", message)

    def _login_finished(self) -> None:
        self.login_button.setEnabled(not self._is_crawling())
        if self._refresh_login_state():
            self._append_log("登录窗口已关闭，登录状态已保存；下次可直接开始采集。")
        else:
            self._append_log("登录窗口已关闭，但尚未检测到可复用的登录状态。")
        if self._login_worker is not None:
            self._login_worker.deleteLater()
            self._login_worker = None
        self._schedule_next_run(log_message=False)

    def _start_crawl(
        self, *, config: CrawlConfig | None = None, scheduled: bool = False
    ) -> None:
        if self._is_crawling():
            return
        if config is None:
            try:
                config = self.build_config()
            except ValueError as exc:
                QMessageBox.warning(self, "输入有误", str(exc))
                return

        store = CheckpointStore.for_config(config)
        resume = False
        if store.exists():
            try:
                checkpoint = store.load()
            except Exception as exc:
                QMessageBox.warning(self, "检查点损坏", f"无法读取上次进度：{exc}")
                return
            if scheduled:
                resume = checkpoint is not None and checkpoint.config == config
                if not resume:
                    store.delete()
            elif checkpoint is not None and checkpoint.config == config:
                answer = QMessageBox.question(
                    self,
                    "发现未完成任务",
                    f"已完成到第 {checkpoint.current_page} 页，是否从检查点继续？\n"
                    "选择“否”会从第一页重新采集。",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                    QMessageBox.Yes,
                )
                if answer == QMessageBox.Cancel:
                    return
                resume = answer == QMessageBox.Yes
                if not resume:
                    store.delete()
            else:
                answer = QMessageBox.question(
                    self,
                    "任务参数已变化",
                    "同一关键词存在不同参数的检查点，是否放弃旧进度并重新开始？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
                store.delete()

        self._schedule_timer.stop()
        self._last_output = None
        self._active_run_is_scheduled = scheduled
        self._clear_result_records()
        self.page_value.setText("0")
        self.raw_value.setText("0")
        self.unique_value.setText("0")
        self.status_value.setText("启动中")
        run_name = "定时任务" if scheduled else "开始任务"
        self._append_log(
            f"{run_name}：关键词“{config.keyword}”，最多 {config.max_pages} 页，"
            f"商品上限 {'不限' if config.max_items == 0 else config.max_items}。"
        )
        if config.filters.is_active:
            self._append_log(
                f"筛选条件：价格 {config.filters.price_label()}，地区 "
                f"{config.filters.region or '全国'}，{config.filters.other_label()}。"
            )
        self._crawl_worker = CrawlWorker(
            config,
            resume=resume,
            profile_dir=self._profile_dir,
        )
        self._crawl_worker.log.connect(self._append_log)
        self._crawl_worker.progress.connect(self._update_progress)
        self._crawl_worker.page_records.connect(self._update_result_records)
        self._crawl_worker.verification.connect(self._verification_required)
        self._crawl_worker.succeeded.connect(self._crawl_succeeded)
        self._crawl_worker.failed.connect(self._crawl_failed)
        self._crawl_worker.finished.connect(self._crawl_thread_finished)
        self._set_running_state(True)
        self._crawl_worker.start()

    def _update_progress(self, progress: CrawlProgress) -> None:
        self.page_value.setText(str(progress.current_page))
        self.raw_value.setText(f"{progress.raw_records:,}")
        self.unique_value.setText(f"{progress.unique_records:,}")
        self.status_value.setText(progress.message or progress.status)

    def _verification_required(self, message: str) -> None:
        self.status_value.setText("等待人工验证")
        self.login_state_value.setText("需要重新确认登录")
        self.login_state_hint.setText("请在浏览器完成后点击继续")
        self.login_state_value.setStyleSheet("color: #a15c00;")
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(True)
        self._append_log(message)
        QMessageBox.information(
            self,
            "需要人工处理",
            "请在打开的浏览器中完成登录或安全验证，然后回到软件点击“继续”。",
        )

    def _pause_crawl(self) -> None:
        if self._crawl_worker is None:
            return
        self._crawl_worker.pause()
        self.status_value.setText("已暂停")
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(True)
        self._append_log("任务已暂停。")

    def _resume_crawl(self) -> None:
        if self._crawl_worker is None:
            return
        self._crawl_worker.resume()
        self.status_value.setText("继续运行")
        self.pause_button.setEnabled(True)
        self.resume_button.setEnabled(False)
        self._append_log("任务已继续。")

    def _stop_crawl(self) -> None:
        if self._crawl_worker is None:
            return
        self._crawl_worker.stop()
        self.status_value.setText("正在停止并导出")
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self._append_log("已请求停止；当前浏览器操作结束后会导出已有结果。")

    def _crawl_succeeded(
        self, output: str, reason: str, count: int, records: list[ProductRecord]
    ) -> None:
        self._last_output = Path(output)
        self.status_value.setText(reason)
        self.unique_value.setText(f"{count:,}")
        self._append_log(f"任务结束：{reason}。已导出 {count:,} 条唯一商品。")
        self._append_log(f"Excel：{output}")
        self.open_button.setEnabled(True)
        if self._active_run_is_scheduled:
            if not reason.startswith(("用户停止", "运行错误")):
                self._push_scheduled_results(records)
            return
        if reason.startswith("运行错误"):
            QMessageBox.warning(self, "任务部分完成", f"{reason}\n已导出当前结果。")
        else:
            QMessageBox.information(self, "采集完成", f"已导出 {count:,} 条商品链接。")

    def _push_scheduled_results(self, records: list[ProductRecord]) -> None:
        scheduled = self._scheduled_collection
        if scheduled is None or not scheduled.enabled:
            return
        if (
            self._notification_delivery_worker is not None
            and self._notification_delivery_worker.isRunning()
        ):
            self._append_log("上一条飞书推送仍在发送，本次结果未重复推送。")
            return
        batch = NotificationBatch(
            task_id="scheduled_collection",
            task_name=f"定时采集：{scheduled.crawl_config.keyword}",
            provider_id="feishu",
            items=records,
            total_count=len(records),
            item_label="商品",
        )
        worker = NotificationDeliveryWorker(
            FeishuProvider(self._monitor_store.load_feishu_config()), batch
        )
        self._notification_delivery_worker = worker
        worker.completed.connect(self._scheduled_delivery_finished)
        worker.finished.connect(self._scheduled_delivery_thread_finished)
        worker.start()

    def _scheduled_delivery_finished(self, success: bool, message: str) -> None:
        if success:
            self._append_log(f"本次定时采集结果已推送至飞书：{message}")
        else:
            self._append_log(f"飞书推送失败：{message}")

    def _scheduled_delivery_thread_finished(self) -> None:
        if self._notification_delivery_worker is not None:
            self._notification_delivery_worker.deleteLater()
            self._notification_delivery_worker = None

    def _crawl_failed(self, message: str) -> None:
        self.status_value.setText("任务失败")
        self._append_log(f"任务失败：{message}")
        if not self._active_run_is_scheduled:
            QMessageBox.critical(self, "任务失败", message)

    def _crawl_thread_finished(self) -> None:
        self._set_running_state(False)
        self._refresh_login_state()
        if self._crawl_worker is not None:
            self._crawl_worker.deleteLater()
            self._crawl_worker = None
        self._active_run_is_scheduled = False
        self._schedule_next_run(log_message=False)

    def _open_output(self) -> None:
        if self._last_output is None or not self._last_output.exists():
            QMessageBox.warning(self, "文件不存在", "还没有可打开的结果文件。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_output)))

    def _is_crawling(self) -> bool:
        return self._crawl_worker is not None and self._crawl_worker.isRunning()

    def _set_running_state(self, running: bool) -> None:
        self.keyword_edit.setEnabled(not running)
        self.max_pages_spin.setEnabled(not running)
        self.max_items_spin.setEnabled(not running)
        self.output_edit.setEnabled(not running)
        self.output_browse_button.setEnabled(not running)
        self.min_price_spin.setEnabled(not running)
        self.max_price_spin.setEnabled(not running)
        self.region_combo.setEnabled(not running)
        self.publish_combo.setEnabled(not running)
        self.personal_checkbox.setEnabled(not running)
        self.inspection_checkbox.setEnabled(not running)
        self.free_shipping_checkbox.setEnabled(not running)
        self.brand_new_checkbox.setEnabled(not running)
        self.login_button.setEnabled(not running)
        self.start_button.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.resume_button.setEnabled(False)
        self.stop_button.setEnabled(running)
        self.open_button.setEnabled(not running and self._last_output is not None)
        self.feishu_settings_button.setEnabled(not running)
        self.update_check_button.setEnabled(
            not running
            and self._update_check_worker is None
            and self._update_preparation_worker is None
        )
        self._update_schedule_ui()

    def _show_feishu_settings(self) -> None:
        self._refresh_feishu_setup_status()
        self._feishu_dialog.show()
        self._feishu_dialog.raise_()
        self._feishu_dialog.activateWindow()

    def _load_notification_settings(self) -> None:
        feishu = self._monitor_store.load_feishu_config()
        self.feishu_app_id_edit.setText(feishu.app_id)
        self.feishu_secret_edit.setText(feishu.app_secret)
        self.feishu_binding_label.setText(
            f"已绑定：…{feishu.open_id[-6:]}" if feishu.open_id else "未绑定接收用户"
        )
        self._refresh_feishu_setup_status()

    def _refresh_feishu_setup_status(self) -> None:
        config = self._monitor_store.load_feishu_config()
        if not config.app_id or not config.app_secret:
            text = "第 1 步/3：填写 App ID 和 App Secret，然后点击“保存飞书配置”。"
            sidebar = "飞书：尚未配置"
        elif not config.open_id:
            text = "第 2 步/3：点击“开始绑定”，再在飞书私聊机器人发送“绑定”。"
            sidebar = "飞书：待绑定接收用户"
        else:
            text = "第 3 步/3：点击“测试飞书”，确认收到通知后再启动定时采集。"
            sidebar = "飞书：已绑定，可推送"
        self.feishu_setup_status.setText(text)
        self.feishu_status_label.setText(sidebar)

    def _save_feishu_settings(self, *, show_message: bool = True) -> bool:
        existing = self._monitor_store.load_feishu_config()
        config = FeishuConfig(
            app_id=self.feishu_app_id_edit.text().strip(),
            app_secret=self.feishu_secret_edit.text().strip(),
            open_id=existing.open_id,
        )
        if not config.app_id or not config.app_secret:
            if show_message:
                QMessageBox.warning(self, "配置不完整", "请填写飞书 App ID 和 App Secret。")
            return False
        self._monitor_store.save_feishu_config(config)
        self._refresh_feishu_setup_status()
        if show_message:
            QMessageBox.information(self, "已保存", "飞书凭证已使用 Windows DPAPI 加密保存在本机。")
        return True

    def _start_feishu_binding(self) -> None:
        if self._feishu_binding_worker is not None and self._feishu_binding_worker.isRunning():
            return
        if not self._save_feishu_settings(show_message=False):
            QMessageBox.warning(self, "配置不完整", "请先填写飞书 App ID 和 App Secret。")
            return
        config = self._monitor_store.load_feishu_config()
        if config.open_id:
            QMessageBox.information(self, "已经绑定", "如需换人，请先点击“解绑”。")
            return
        self._feishu_binding_worker = FeishuBindingWorker(self._monitor_store, config)
        self._feishu_binding_worker.bound.connect(self._feishu_bound)
        self._feishu_binding_worker.failed.connect(self._feishu_binding_failed)
        self._feishu_binding_worker.expired.connect(self._feishu_binding_expired)
        self._feishu_binding_worker.finished.connect(self._feishu_binding_finished)
        self.feishu_bind_button.setEnabled(False)
        self.feishu_binding_label.setText("等待私聊机器人发送“绑定”（5分钟）")
        self._append_log("飞书绑定窗口已开启，请在5分钟内私聊机器人发送“绑定”。")
        self._feishu_binding_worker.start()

    def _show_feishu_guide(self) -> None:
        QMessageBox.information(
            self,
            "飞书机器人配置向导",
            "请在飞书开放平台依次完成：\n\n"
            "1. 创建企业自建应用。\n"
            "2. 开启机器人能力。\n"
            "3. 开通 im:message 和 im:resource 权限。\n"
            "4. 事件订阅选择“使用长连接接收事件”。\n"
            "5. 添加 im.message.receive_v1 事件。\n"
            "6. 把自己加入应用可用范围。\n"
            "7. 创建并发布应用版本。\n\n"
            "im:resource 用于上传本次推送的商品主图；未申请或未发布该权限时，"
            "软件仍会推送文字和“查看商品”按钮。\n\n"
            "然后回到软件填写 App ID 和 App Secret，点击“开始绑定”，"
            "并在5分钟内私聊机器人发送“绑定”。",
        )

    def _feishu_bound(self, open_id: str) -> None:
        self.feishu_binding_label.setText(f"已绑定：…{open_id[-6:]}")
        self._refresh_feishu_setup_status()
        self._append_log("飞书接收用户绑定成功。")
        QMessageBox.information(self, "绑定成功", "飞书机器人已绑定到这台电脑。")

    def _feishu_binding_failed(self, message: str) -> None:
        self.feishu_binding_label.setText("绑定失败")
        self._append_log(f"飞书绑定失败：{message}")
        QMessageBox.warning(self, "飞书绑定失败", message)

    def _feishu_binding_expired(self) -> None:
        self.feishu_binding_label.setText("绑定窗口已过期")
        self._append_log("飞书绑定窗口已过期，请重新开始绑定。")

    def _feishu_binding_finished(self) -> None:
        self.feishu_bind_button.setEnabled(True)
        if self._feishu_binding_worker is not None:
            self._feishu_binding_worker.deleteLater()
            self._feishu_binding_worker = None

    def _unbind_feishu(self) -> None:
        config = self._monitor_store.load_feishu_config()
        self._monitor_store.save_feishu_config(replace(config, open_id=""))
        self.feishu_binding_label.setText("未绑定接收用户")
        self._refresh_feishu_setup_status()
        self._append_log("飞书接收用户已解绑。")

    def _test_notification(self) -> None:
        if self._notification_test_worker is not None and self._notification_test_worker.isRunning():
            return
        if not self._save_feishu_settings(show_message=False):
            QMessageBox.warning(self, "配置不完整", "请先填写并保存飞书配置。")
            return
        self.feishu_test_button.setEnabled(False)
        self._notification_test_worker = NotificationTestWorker(
            FeishuProvider(self._monitor_store.load_feishu_config())
        )
        self._notification_test_worker.completed.connect(self._notification_test_finished)
        self._notification_test_worker.finished.connect(
            self._notification_test_thread_finished
        )
        self._notification_test_worker.start()

    def _notification_test_finished(self, success: bool, message: str) -> None:
        if success:
            self._append_log("飞书测试通知发送成功。")
            QMessageBox.information(self, "测试成功", "飞书测试通知已发送。")
        else:
            self._append_log(f"飞书测试通知失败：{message}")
            QMessageBox.warning(self, "测试失败", message)

    def _notification_test_thread_finished(self) -> None:
        self.feishu_test_button.setEnabled(True)
        if self._notification_test_worker is not None:
            self._notification_test_worker.deleteLater()
            self._notification_test_worker = None

    def _setup_tray(self) -> None:
        self._tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(application_icon(), self)
        self._tray.setToolTip("闲鱼定时采集与飞书推送")
        menu = self._tray.contextMenu()
        if menu is None:
            from PySide6.QtWidgets import QMenu

            menu = QMenu(self)
        show_action = QAction("显示主窗口", self)
        quit_action = QAction("退出软件", self)
        show_action.triggered.connect(self._show_from_tray)
        quit_action.triggered.connect(self._quit_from_tray)
        menu.addAction(show_action)
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda reason: self._show_from_tray()
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick)
            else None
        )
        self._tray.show()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        self._force_exit = True
        self.close()
        app = QApplication.instance()
        if app is not None and not self.isVisible():
            app.quit()

    def _stop_background_workers(self) -> bool:
        if (
            self._notification_test_worker is not None
            and self._notification_test_worker.isRunning()
            and not self._notification_test_worker.wait(16_000)
        ):
            return False
        if (
            self._notification_delivery_worker is not None
            and self._notification_delivery_worker.isRunning()
            and not self._notification_delivery_worker.wait(16_000)
        ):
            return False
        if self._feishu_binding_worker is not None and self._feishu_binding_worker.isRunning():
            self._feishu_binding_worker.stop()
            if not self._feishu_binding_worker.wait(3_000):
                return False
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        scheduled_enabled = bool(
            self._scheduled_collection and self._scheduled_collection.enabled
        )
        if (
            not self._force_exit
            and scheduled_enabled
            and self._tray is not None
            and self._tray.isVisible()
        ):
            self.hide()
            self._tray.showMessage(
                "闲鱼定时采集仍在运行",
                "窗口已缩到系统托盘。电脑需要保持开机；要完全退出请右键托盘图标选择“退出软件”。",
                QSystemTrayIcon.Information,
                4_000,
            )
            event.ignore()
            return
        self._schedule_timer.stop()
        if self._force_exit and scheduled_enabled:
            self._stop_scheduled_collection(show_message=False)
        if self._is_crawling():
            answer = QMessageBox.question(
                self,
                "退出软件",
                "采集仍在运行。是否停止任务并退出？检查点会保留。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self._crawl_worker.stop()
            if not self._crawl_worker.wait(5_000):
                QMessageBox.warning(
                    self,
                    "正在结束浏览器操作",
                    "浏览器仍在结束当前操作，请稍候再关闭软件。检查点已经保留。",
                )
                event.ignore()
                return
        if not self._stop_background_workers():
            QMessageBox.warning(self, "正在结束后台任务", "飞书推送或绑定仍在结束，请稍后再退出。")
            event.ignore()
            return
        if self._login_worker is not None and self._login_worker.isRunning():
            self._login_worker.stop()
            if not self._login_worker.wait(3_000):
                QMessageBox.warning(self, "正在关闭登录窗口", "请先关闭专用浏览器窗口。")
                event.ignore()
                return
        if self._tray is not None:
            self._tray.hide()
        event.accept()


def main() -> int:
    if "--self-test" in sys.argv:
        from .selftest import run_self_test

        return run_self_test()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("闲鱼商品采集与定时推送")
    app.setOrganizationName("Local")
    app.setWindowIcon(application_icon())
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(light_palette())
    instance = SingleInstanceCoordinator()
    if not instance.start():
        return 0
    window = MainWindow()
    instance.activation_requested.connect(window._show_from_tray)
    window.show()
    try:
        return app.exec()
    finally:
        instance.close()


if __name__ == "__main__":
    raise SystemExit(main())

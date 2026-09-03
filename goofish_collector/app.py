from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QStandardPaths, Qt, QTime, QUrl
from PySide6.QtGui import QAction, QColor, QCloseEvent, QDesktopServices, QIcon, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
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
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .browser import default_profile_dir, profile_has_saved_login
from .checkpoint import CheckpointStore
from .models import CrawlConfig, CrawlProgress, SearchFilters
from .exporter import export_monitor_workbook
from .feishu_binding import FeishuBindingWorker
from .monitor_models import FeishuConfig, MonitorTaskConfig, WxPusherConfig
from .monitor_store import MonitorStore
from .notifications import FeishuProvider, WxPusherProvider
from .workers import (
    CrawlWorker,
    LoginWorker,
    MonitorSchedulerWorker,
    NotificationTestWorker,
)


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


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        default_output_dir: Path | None = None,
        profile_dir: Path | None = None,
        monitor_db_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("闲鱼商品采集与新品监控")
        self.setWindowIcon(application_icon())
        self.resize(1160, 860)
        self.setMinimumSize(980, 740)
        self._crawl_worker: CrawlWorker | None = None
        self._login_worker: LoginWorker | None = None
        self._last_output: Path | None = None
        self._monitor_worker: MonitorSchedulerWorker | None = None
        self._notification_test_worker: NotificationTestWorker | None = None
        self._feishu_binding_worker: FeishuBindingWorker | None = None
        self._selected_task_id = ""
        self._force_exit = False
        self._default_output_dir = default_output_dir or self._documents_output_dir()
        self._profile_dir = (profile_dir or default_profile_dir()).resolve()
        self._monitor_store = MonitorStore(monitor_db_path)
        self._build_ui()
        self._refresh_login_state()
        self._set_running_state(False)
        self._load_notification_settings()
        self._refresh_monitor_tasks()
        self._setup_tray()
        if any(
            state.config.enabled for state in self._monitor_store.list_task_states()
        ) or self._monitor_store.list_batches(status="pending"):
            self._ensure_monitor_scheduler()

    @staticmethod
    def _documents_output_dir() -> Path:
        documents = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        base = Path(documents) if documents else Path.home() / "Documents"
        return base / "闲鱼采集结果"

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
        title = QLabel("闲鱼商品采集与新品监控")
        title.setObjectName("pageTitle")
        subtitle = QLabel("首次扫码后自动复用本机登录状态，采集结果直接导出为 WPS Excel。")
        subtitle.setObjectName("pageSubtitle")
        header_copy.addWidget(title)
        header_copy.addWidget(subtitle)
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
            QFrame#surfacePanel, QFrame#statusPanel, QFrame#actionPanel,
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
        self.main_tabs = QTabWidget(self)
        self.main_tabs.setObjectName("mainTabs")
        self.content_scroll.setParent(self.main_tabs)
        self.main_tabs.addTab(self.content_scroll, "单次采集")
        self.main_tabs.addTab(self._build_monitor_page(), "新品监控")
        self.setCentralWidget(self.main_tabs)
        self.setStyleSheet(
            self.styleSheet()
            + """
            QTabWidget#mainTabs::pane { border: none; background: #f3f5f7; }
            QTabBar::tab { min-width: 120px; min-height: 34px; padding: 4px 14px;
                background: #e7ebef; color: #526174; font-weight: 700; }
            QTabBar::tab:selected { background: #ffd84d; color: #172033; }
            QTableWidget { background: #ffffff; alternate-background-color: #f8fafb;
                border: 1px solid #dbe1e8; border-radius: 8px; gridline-color: #e8edf2; }
            QHeaderView::section { background: #eef2f5; color: #465468; border: none;
                border-bottom: 1px solid #dbe1e8; padding: 8px; font-weight: 700; }
            """
        )

    def _build_monitor_page(self) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setObjectName("monitorScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = QWidget()
        page.setObjectName("appRoot")
        page.setMinimumWidth(900)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 24)
        layout.setSpacing(16)

        heading = QLabel("新品监控")
        heading.setObjectName("pageTitle")
        hint = QLabel(
            "首次扫描静默建立基线，之后只把从未见过的商品发到手机；不自动联系卖家。"
        )
        hint.setObjectName("pageSubtitle")
        layout.addWidget(heading)
        layout.addWidget(hint)

        list_panel = QFrame()
        list_panel.setObjectName("surfacePanel")
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(18, 16, 18, 18)
        list_header = QHBoxLayout()
        list_title = QLabel("监控任务")
        list_title.setObjectName("sectionTitle")
        self.monitor_summary_label = QLabel("0 个任务")
        self.monitor_summary_label.setObjectName("sectionHint")
        list_header.addWidget(list_title)
        list_header.addStretch(1)
        list_header.addWidget(self.monitor_summary_label)
        list_layout.addLayout(list_header)
        self.monitor_table = QTableWidget(0, 7)
        self.monitor_table.setHorizontalHeaderLabels(
            ["任务名称", "关键词", "状态", "间隔", "页数", "上次扫描", "下次扫描"]
        )
        self.monitor_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.monitor_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.monitor_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.monitor_table.setAlternatingRowColors(True)
        self.monitor_table.verticalHeader().setVisible(False)
        self.monitor_table.horizontalHeader().setStretchLastSection(True)
        self.monitor_table.setMinimumHeight(180)
        self.monitor_table.itemSelectionChanged.connect(self._load_selected_monitor_task)
        list_layout.addWidget(self.monitor_table)
        layout.addWidget(list_panel)

        editor_panel = QFrame()
        editor_panel.setObjectName("surfacePanel")
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(18, 16, 18, 18)
        editor_layout.setSpacing(11)
        editor_title_row = QHBoxLayout()
        editor_title = QLabel("任务规则")
        editor_title.setObjectName("sectionTitle")
        self.monitor_editing_label = QLabel("新建任务")
        self.monitor_editing_label.setObjectName("sectionHint")
        editor_title_row.addWidget(editor_title)
        editor_title_row.addStretch(1)
        editor_title_row.addWidget(self.monitor_editing_label)
        editor_layout.addLayout(editor_title_row)

        identity = QGridLayout()
        identity.setHorizontalSpacing(12)
        identity.setVerticalSpacing(7)
        self.monitor_name_edit = QLineEdit()
        self.monitor_name_edit.setPlaceholderText("例如：广州 FreeClip 新品")
        self.monitor_keyword_edit = QLineEdit()
        self.monitor_keyword_edit.setPlaceholderText("输入一个搜索关键词")
        for column, text in enumerate(("任务名称", "搜索关键词")):
            label = QLabel(text)
            label.setObjectName("fieldLabel")
            identity.addWidget(label, 0, column)
        identity.addWidget(self.monitor_name_edit, 1, 0)
        identity.addWidget(self.monitor_keyword_edit, 1, 1)
        identity.setColumnStretch(0, 1)
        identity.setColumnStretch(1, 1)
        editor_layout.addLayout(identity)

        rule_grid = QGridLayout()
        rule_grid.setHorizontalSpacing(10)
        rule_grid.setVerticalSpacing(7)
        self.monitor_min_price = OptionalPriceSpinBox()
        self.monitor_max_price = OptionalPriceSpinBox()
        for spin in (self.monitor_min_price, self.monitor_max_price):
            spin.setRange(0, 100_000_000)
            spin.setDecimals(2)
            spin.setPrefix("¥")
            spin.setMinimumWidth(130)
        self.monitor_region_combo = QComboBox()
        self.monitor_region_combo.setEditable(True)
        self.monitor_region_combo.addItems(
            [self.region_combo.itemText(index) for index in range(self.region_combo.count())]
        )
        self.monitor_publish_combo = QComboBox()
        self.monitor_publish_combo.addItems(
            [self.publish_combo.itemText(index) for index in range(self.publish_combo.count())]
        )
        self.monitor_sort_combo = QComboBox()
        self.monitor_sort_combo.addItems(["综合", "新降价", "新发布"])
        self.monitor_pages_combo = QComboBox()
        self.monitor_pages_combo.addItems(["1", "2", "3"])
        self.monitor_interval_combo = QComboBox()
        self.monitor_interval_combo.addItems(["5", "10", "15", "30"])
        self.monitor_interval_combo.setCurrentText("10")
        fields = (
            ("最低价", self.monitor_min_price),
            ("最高价", self.monitor_max_price),
            ("地区", self.monitor_region_combo),
            ("发布时间", self.monitor_publish_combo),
            ("排序", self.monitor_sort_combo),
            ("页数", self.monitor_pages_combo),
            ("间隔(分钟)", self.monitor_interval_combo),
        )
        for column, (text, widget) in enumerate(fields):
            label = QLabel(text)
            label.setObjectName("fieldLabel")
            rule_grid.addWidget(label, 0, column)
            rule_grid.addWidget(widget, 1, column)
        rule_grid.setColumnStretch(2, 1)
        editor_layout.addLayout(rule_grid)

        conditions = QHBoxLayout()
        conditions.setSpacing(8)
        self.monitor_personal_checkbox = QCheckBox("个人闲置")
        self.monitor_inspection_checkbox = QCheckBox("验货宝")
        self.monitor_shipping_checkbox = QCheckBox("包邮")
        self.monitor_new_checkbox = QCheckBox("全新")
        for checkbox in (
            self.monitor_personal_checkbox,
            self.monitor_inspection_checkbox,
            self.monitor_shipping_checkbox,
            self.monitor_new_checkbox,
        ):
            conditions.addWidget(checkbox)
        self.monitor_quiet_checkbox = QCheckBox("免打扰")
        self.monitor_quiet_start = QTimeEdit(QTime(22, 0))
        self.monitor_quiet_end = QTimeEdit(QTime(7, 0))
        for editor in (self.monitor_quiet_start, self.monitor_quiet_end):
            editor.setDisplayFormat("HH:mm")
            editor.setEnabled(False)
        self.monitor_quiet_checkbox.toggled.connect(self.monitor_quiet_start.setEnabled)
        self.monitor_quiet_checkbox.toggled.connect(self.monitor_quiet_end.setEnabled)
        conditions.addStretch(1)
        conditions.addWidget(self.monitor_quiet_checkbox)
        conditions.addWidget(self.monitor_quiet_start)
        conditions.addWidget(QLabel("至"))
        conditions.addWidget(self.monitor_quiet_end)
        editor_layout.addLayout(conditions)

        task_actions = QHBoxLayout()
        self.monitor_new_button = QPushButton("新建 / 清空")
        self.monitor_save_button = QPushButton("保存任务")
        self.monitor_save_button.setObjectName("primaryButton")
        self.monitor_scan_button = QPushButton("立即扫描")
        self.monitor_toggle_button = QPushButton("启动监控")
        self.monitor_delete_button = QPushButton("删除")
        self.monitor_export_button = QPushButton("导出 Excel")
        self.monitor_new_button.clicked.connect(self._clear_monitor_editor)
        self.monitor_save_button.clicked.connect(self._save_monitor_task)
        self.monitor_scan_button.clicked.connect(self._scan_monitor_now)
        self.monitor_toggle_button.clicked.connect(self._toggle_monitor_task)
        self.monitor_delete_button.clicked.connect(self._delete_monitor_task)
        self.monitor_export_button.clicked.connect(self._export_monitor_task)
        for button in (
            self.monitor_new_button,
            self.monitor_save_button,
            self.monitor_scan_button,
            self.monitor_toggle_button,
            self.monitor_delete_button,
            self.monitor_export_button,
        ):
            task_actions.addWidget(button)
        editor_layout.addLayout(task_actions)
        layout.addWidget(editor_panel)

        notice_panel = QFrame()
        notice_panel.setObjectName("surfacePanel")
        notice_layout = QVBoxLayout(notice_panel)
        notice_layout.setContentsMargins(18, 16, 18, 18)
        notice_layout.setSpacing(10)
        notice_header = QHBoxLayout()
        notice_title = QLabel("手机通知")
        notice_title.setObjectName("sectionTitle")
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("飞书应用机器人（默认）", "feishu")
        self.provider_combo.addItem("WxPusher 极简推送", "wxpusher")
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        notice_header.addWidget(notice_title)
        notice_header.addStretch(1)
        notice_header.addWidget(QLabel("当前通道"))
        notice_header.addWidget(self.provider_combo)
        notice_layout.addLayout(notice_header)
        self.provider_hint = QLabel("一次只启用一个通道；切换不会改变已经排队的通知。")
        self.provider_hint.setObjectName("sectionHint")
        notice_layout.addWidget(self.provider_hint)
        self.feishu_setup_status = QLabel()
        self.feishu_setup_status.setObjectName("sectionHint")
        self.feishu_setup_status.setWordWrap(True)
        notice_layout.addWidget(self.feishu_setup_status)
        failed_row = QHBoxLayout()
        self.failed_batch_combo = QComboBox()
        self.failed_batch_combo.setMinimumWidth(320)
        self.retry_failed_button = QPushButton("使用当前通道重试")
        self.retry_failed_button.clicked.connect(self._retry_failed_batch)
        failed_row.addWidget(QLabel("失败通知"))
        failed_row.addWidget(self.failed_batch_combo, 1)
        failed_row.addWidget(self.retry_failed_button)
        notice_layout.addLayout(failed_row)

        feishu_grid = QGridLayout()
        feishu_grid.setHorizontalSpacing(10)
        self.feishu_app_id_edit = QLineEdit()
        self.feishu_app_id_edit.setPlaceholderText("cli_xxx")
        self.feishu_secret_edit = QLineEdit()
        self.feishu_secret_edit.setEchoMode(QLineEdit.Password)
        self.feishu_secret_edit.setPlaceholderText("App Secret（仅加密保存在本机）")
        self.feishu_binding_label = QLabel("未绑定接收用户")
        self.feishu_binding_label.setObjectName("sectionHint")
        self.feishu_save_button = QPushButton("保存飞书配置")
        self.feishu_bind_button = QPushButton("开始绑定（5分钟）")
        self.feishu_unbind_button = QPushButton("解绑")
        self.feishu_test_button = QPushButton("测试飞书")
        self.feishu_help_button = QPushButton("配置向导")
        self.feishu_save_button.clicked.connect(self._save_feishu_settings)
        self.feishu_bind_button.clicked.connect(self._start_feishu_binding)
        self.feishu_unbind_button.clicked.connect(self._unbind_feishu)
        self.feishu_test_button.clicked.connect(lambda: self._test_notification("feishu"))
        self.feishu_help_button.clicked.connect(self._show_feishu_guide)
        feishu_grid.addWidget(QLabel("飞书 App ID"), 0, 0)
        feishu_grid.addWidget(self.feishu_app_id_edit, 0, 1)
        feishu_grid.addWidget(QLabel("App Secret"), 0, 2)
        feishu_grid.addWidget(self.feishu_secret_edit, 0, 3)
        feishu_grid.addWidget(self.feishu_binding_label, 1, 0, 1, 2)
        feishu_buttons = QHBoxLayout()
        for button in (
            self.feishu_save_button,
            self.feishu_bind_button,
            self.feishu_unbind_button,
            self.feishu_test_button,
            self.feishu_help_button,
        ):
            feishu_buttons.addWidget(button)
        feishu_grid.addLayout(feishu_buttons, 1, 2, 1, 2)
        notice_layout.addLayout(feishu_grid)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.HLine)
        notice_layout.addWidget(divider)
        wx_row = QHBoxLayout()
        self.wxpusher_spt_edit = QLineEdit()
        self.wxpusher_spt_edit.setEchoMode(QLineEdit.Password)
        self.wxpusher_spt_edit.setPlaceholderText("SPT_...（相当于私人收件地址，请勿泄露）")
        self.wxpusher_save_button = QPushButton("保存 SPT")
        self.wxpusher_test_button = QPushButton("测试 WxPusher")
        self.wxpusher_reset_button = QPushButton("重置")
        self.wxpusher_help_button = QPushButton("获取 SPT / 官方说明")
        self.wxpusher_save_button.clicked.connect(self._save_wxpusher_settings)
        self.wxpusher_test_button.clicked.connect(lambda: self._test_notification("wxpusher"))
        self.wxpusher_reset_button.clicked.connect(self._reset_wxpusher)
        self.wxpusher_help_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://wxpusher.zjiecode.com/docs/"))
        )
        wx_row.addWidget(QLabel("WxPusher SPT"))
        wx_row.addWidget(self.wxpusher_spt_edit, 1)
        for button in (
            self.wxpusher_save_button,
            self.wxpusher_test_button,
            self.wxpusher_reset_button,
            self.wxpusher_help_button,
        ):
            wx_row.addWidget(button)
        notice_layout.addLayout(wx_row)
        layout.addWidget(notice_panel)

        monitor_log_panel = QFrame()
        monitor_log_panel.setObjectName("logPanel")
        monitor_log_layout = QVBoxLayout(monitor_log_panel)
        monitor_log_layout.setContentsMargins(18, 14, 18, 16)
        monitor_log_title = QLabel("监控日志")
        monitor_log_title.setObjectName("sectionTitle")
        self.monitor_log_view = QTextEdit()
        self.monitor_log_view.setReadOnly(True)
        self.monitor_log_view.setMinimumHeight(120)
        self.monitor_log_view.setPlaceholderText("扫描、发现新品和通知结果会显示在这里。")
        self.monitor_log_view.document().setMaximumBlockCount(1000)
        monitor_log_layout.addWidget(monitor_log_title)
        monitor_log_layout.addWidget(self.monitor_log_view)
        layout.addWidget(monitor_log_panel)
        layout.addStretch(1)
        scroll.setWidget(page)
        return scroll

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

    def _append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{stamp}] {message}")

    def _start_login(self) -> None:
        if self._login_worker is not None and self._login_worker.isRunning():
            return
        if not self._suspend_monitor_scheduler():
            QMessageBox.warning(
                self,
                "监控正在结束当前扫描",
                "请稍后再打开登录窗口，避免两个 Edge 同时占用专用登录目录。",
            )
            return
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
        if any(
            state.config.enabled for state in self._monitor_store.list_task_states()
        ) or self._monitor_store.list_batches(status="pending"):
            self._ensure_monitor_scheduler()

    def _start_crawl(self) -> None:
        if self._is_crawling():
            return
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
            if checkpoint is not None and checkpoint.config == config:
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

        if not self._suspend_monitor_scheduler():
            QMessageBox.warning(
                self,
                "监控正在结束当前扫描",
                "请稍后再开始单次采集，避免两个 Edge 同时占用专用登录目录。",
            )
            return
        self._last_output = None
        self.page_value.setText("0")
        self.raw_value.setText("0")
        self.unique_value.setText("0")
        self.status_value.setText("启动中")
        self._append_log(
            f"开始任务：关键词“{config.keyword}”，最多 {config.max_pages} 页，"
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

    def _crawl_succeeded(self, output: str, reason: str, count: int) -> None:
        self._last_output = Path(output)
        self.status_value.setText(reason)
        self.unique_value.setText(f"{count:,}")
        self._append_log(f"任务结束：{reason}。已导出 {count:,} 条唯一商品。")
        self._append_log(f"Excel：{output}")
        self.open_button.setEnabled(True)
        if reason.startswith("运行错误"):
            QMessageBox.warning(self, "任务部分完成", f"{reason}\n已导出当前结果。")
        else:
            QMessageBox.information(self, "采集完成", f"已导出 {count:,} 条商品链接。")

    def _crawl_failed(self, message: str) -> None:
        self.status_value.setText("任务失败")
        self._append_log(f"任务失败：{message}")
        QMessageBox.critical(self, "任务失败", message)

    def _crawl_thread_finished(self) -> None:
        self._set_running_state(False)
        self._refresh_login_state()
        if self._crawl_worker is not None:
            self._crawl_worker.deleteLater()
            self._crawl_worker = None
        if any(
            state.config.enabled for state in self._monitor_store.list_task_states()
        ) or self._monitor_store.list_batches(status="pending"):
            self._ensure_monitor_scheduler()

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

    def _append_monitor_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.monitor_log_view.append(f"[{stamp}] {message}")

    def _monitor_filters(self) -> SearchFilters:
        region = self.monitor_region_combo.currentText().strip()
        filters = SearchFilters(
            min_price=self.monitor_min_price.value() or None,
            max_price=self.monitor_max_price.value() or None,
            region="" if region == "全国" else region,
            published_within=(
                ""
                if self.monitor_publish_combo.currentText() == "不限"
                else self.monitor_publish_combo.currentText()
            ),
            personal_only=self.monitor_personal_checkbox.isChecked(),
            inspection_only=self.monitor_inspection_checkbox.isChecked(),
            free_shipping=self.monitor_shipping_checkbox.isChecked(),
            brand_new=self.monitor_new_checkbox.isChecked(),
            sort_mode=self.monitor_sort_combo.currentText(),
        )
        filters.validate()
        return filters

    def _build_monitor_task(self) -> MonitorTaskConfig:
        enabled = False
        if self._selected_task_id:
            enabled = self._monitor_store.get_task_state(self._selected_task_id).config.enabled
        return MonitorTaskConfig(
            task_id=self._selected_task_id or uuid4().hex,
            name=self.monitor_name_edit.text(),
            keyword=self.monitor_keyword_edit.text(),
            filters=self._monitor_filters(),
            pages=int(self.monitor_pages_combo.currentText()),
            interval_minutes=int(self.monitor_interval_combo.currentText()),
            quiet_enabled=self.monitor_quiet_checkbox.isChecked(),
            quiet_start=self.monitor_quiet_start.time().toString("HH:mm"),
            quiet_end=self.monitor_quiet_end.time().toString("HH:mm"),
            enabled=enabled,
        )

    def _refresh_monitor_tasks(self) -> None:
        states = self._monitor_store.list_task_states()
        selected = self._selected_task_id
        self.monitor_table.blockSignals(True)
        self.monitor_table.setRowCount(len(states))
        status_labels = {
            "paused": "已暂停",
            "waiting": "监控中",
            "running": "扫描中",
            "error": "运行错误",
            "needs_login": "等待登录",
        }
        selected_row = -1
        for row, state in enumerate(states):
            config = state.config
            values = (
                config.name,
                config.keyword,
                status_labels.get(state.status, state.status),
                f"{config.interval_minutes} 分钟",
                f"{config.pages} 页",
                state.last_run_at.replace("T", " ") or "尚未扫描",
                state.next_run_at.replace("T", " ") or ("等待调度" if config.enabled else "—"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, config.task_id)
                self.monitor_table.setItem(row, column, item)
            if config.task_id == selected:
                selected_row = row
        self.monitor_table.resizeColumnsToContents()
        self.monitor_table.blockSignals(False)
        enabled_count = sum(1 for state in states if state.config.enabled)
        self.monitor_summary_label.setText(
            f"{len(states)} 个任务 · {enabled_count} 个正在监控"
        )
        if selected_row >= 0:
            self.monitor_table.selectRow(selected_row)
        elif selected and not any(state.config.task_id == selected for state in states):
            self._clear_monitor_editor()

    def _load_selected_monitor_task(self) -> None:
        row = self.monitor_table.currentRow()
        if row < 0:
            return
        item = self.monitor_table.item(row, 0)
        task_id = str(item.data(Qt.UserRole) or "") if item else ""
        if not task_id:
            return
        state = self._monitor_store.get_task_state(task_id)
        config = state.config
        self._selected_task_id = task_id
        self.monitor_editing_label.setText(f"正在编辑：{config.name}")
        self.monitor_name_edit.setText(config.name)
        self.monitor_keyword_edit.setText(config.keyword)
        self.monitor_min_price.setValue(config.filters.min_price or 0)
        self.monitor_max_price.setValue(config.filters.max_price or 0)
        self.monitor_region_combo.setCurrentText(config.filters.region or "全国")
        self.monitor_publish_combo.setCurrentText(config.filters.published_within or "不限")
        self.monitor_sort_combo.setCurrentText(config.filters.sort_mode)
        self.monitor_pages_combo.setCurrentText(str(config.pages))
        self.monitor_interval_combo.setCurrentText(str(config.interval_minutes))
        self.monitor_personal_checkbox.setChecked(config.filters.personal_only)
        self.monitor_inspection_checkbox.setChecked(config.filters.inspection_only)
        self.monitor_shipping_checkbox.setChecked(config.filters.free_shipping)
        self.monitor_new_checkbox.setChecked(config.filters.brand_new)
        self.monitor_quiet_checkbox.setChecked(config.quiet_enabled)
        self.monitor_quiet_start.setTime(QTime.fromString(config.quiet_start, "HH:mm"))
        self.monitor_quiet_end.setTime(QTime.fromString(config.quiet_end, "HH:mm"))
        self.monitor_toggle_button.setText("暂停监控" if config.enabled else "启动监控")

    def _clear_monitor_editor(self) -> None:
        self._selected_task_id = ""
        self.monitor_table.clearSelection()
        self.monitor_editing_label.setText("新建任务")
        self.monitor_name_edit.clear()
        self.monitor_keyword_edit.clear()
        self.monitor_min_price.setValue(0)
        self.monitor_max_price.setValue(0)
        self.monitor_region_combo.setCurrentText("全国")
        self.monitor_publish_combo.setCurrentText("不限")
        self.monitor_sort_combo.setCurrentText("综合")
        self.monitor_pages_combo.setCurrentText("1")
        self.monitor_interval_combo.setCurrentText("10")
        for checkbox in (
            self.monitor_personal_checkbox,
            self.monitor_inspection_checkbox,
            self.monitor_shipping_checkbox,
            self.monitor_new_checkbox,
            self.monitor_quiet_checkbox,
        ):
            checkbox.setChecked(False)
        self.monitor_quiet_start.setTime(QTime(22, 0))
        self.monitor_quiet_end.setTime(QTime(7, 0))
        self.monitor_toggle_button.setText("启动监控")

    def _save_monitor_task(self) -> bool:
        try:
            task = self._build_monitor_task()
            old_state = (
                self._monitor_store.get_task_state(task.task_id)
                if self._selected_task_id
                else None
            )
            state = self._monitor_store.save_task(task)
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self, "任务规则有误", str(exc))
            return False
        self._selected_task_id = task.task_id
        if old_state and old_state.config.rule_fingerprint != task.rule_fingerprint:
            self._append_monitor_log(
                f"任务“{task.name}”规则已修改：历史数据保留，下次扫描会静默重建基线。"
            )
        else:
            self._append_monitor_log(f"任务“{task.name}”已保存。")
        self._refresh_monitor_tasks()
        return True

    def _selected_task(self):
        if not self._selected_task_id:
            QMessageBox.information(self, "请选择任务", "请先在任务列表中选择一个任务。")
            return None
        try:
            return self._monitor_store.get_task_state(self._selected_task_id)
        except KeyError:
            self._refresh_monitor_tasks()
            return None

    def _scan_monitor_now(self) -> None:
        if self._is_crawling() or (
            self._login_worker is not None and self._login_worker.isRunning()
        ):
            QMessageBox.information(
                self,
                "浏览器正在使用",
                "请先完成单次采集或登录窗口，再立即扫描监控任务。",
            )
            return
        if not self._selected_task_id and not self._save_monitor_task():
            return
        state = self._selected_task()
        if state is None:
            return
        self._ensure_monitor_scheduler()
        self._monitor_worker.request_scan(state.config.task_id)
        self._append_monitor_log(f"已请求立即扫描“{state.config.name}”。")

    def _toggle_monitor_task(self) -> None:
        state = self._selected_task()
        if state is None:
            return
        enabled = not state.config.enabled
        updated = replace(state.config, enabled=enabled)
        self._monitor_store.save_task(updated)
        if enabled:
            self._monitor_store.update_task_runtime(
                updated.task_id, status="waiting", next_run_at=""
            )
            self._ensure_monitor_scheduler()
            self._monitor_worker.wake()
            self._append_monitor_log(f"任务“{updated.name}”已启动，将立即建立或检查基线。")
        else:
            self._append_monitor_log(f"任务“{updated.name}”已暂停。")
        self._refresh_monitor_tasks()
        self._load_selected_monitor_task()

    def _delete_monitor_task(self) -> None:
        state = self._selected_task()
        if state is None:
            return
        answer = QMessageBox.question(
            self,
            "删除监控任务",
            f"确定删除“{state.config.name}”吗？该任务的基线、历史商品和通知队列也会删除。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._monitor_store.delete_task(state.config.task_id)
        self._append_monitor_log(f"已删除任务“{state.config.name}”。")
        self._clear_monitor_editor()
        self._refresh_monitor_tasks()

    def _export_monitor_task(self) -> None:
        state = self._selected_task()
        if state is None:
            return
        records = self._monitor_store.list_products(
            state.config.task_id, all_generations=True
        )
        if not records:
            QMessageBox.information(self, "暂无数据", "该任务还没有扫描到可导出的商品。")
            return
        try:
            output = export_monitor_workbook(
                state.config, records, Path(self.output_edit.text().strip())
            )
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self._append_monitor_log(f"监控数据已导出：{output}")
        QMessageBox.information(self, "导出完成", f"已导出 {len(records)} 条历史商品。")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(output.parent)))

    def _ensure_monitor_scheduler(self) -> None:
        if self._is_crawling() or (
            self._login_worker is not None and self._login_worker.isRunning()
        ):
            return
        if self._monitor_worker is not None and self._monitor_worker.isRunning():
            return
        worker = MonitorSchedulerWorker(
            self._monitor_store, profile_dir=self._profile_dir
        )
        self._monitor_worker = worker
        worker.log.connect(self._append_monitor_log)
        worker.task_updated.connect(lambda _: self._refresh_monitor_tasks())
        worker.verification.connect(self._monitor_verification_required)
        worker.delivery.connect(
            lambda _batch_id, _success, _message: self._refresh_failed_batches()
        )
        worker.finished.connect(lambda current=worker: self._monitor_scheduler_finished(current))
        worker.start()

    def _monitor_scheduler_finished(self, worker: MonitorSchedulerWorker) -> None:
        worker.deleteLater()
        if self._monitor_worker is worker:
            self._monitor_worker = None

    def _suspend_monitor_scheduler(self) -> bool:
        worker = self._monitor_worker
        if worker is None or not worker.isRunning():
            return True
        worker.stop()
        if not worker.wait(10_000):
            return False
        if self._monitor_worker is worker:
            self._monitor_worker = None
        worker.deleteLater()
        return True

    def _monitor_verification_required(self, task_id: str) -> None:
        try:
            state = self._monitor_store.get_task_state(task_id)
            self._monitor_store.update_task_runtime(task_id, status="needs_login")
            name = state.config.name
        except KeyError:
            name = task_id
        self._refresh_monitor_tasks()
        self._append_monitor_log(
            f"任务“{name}”需要人工登录或验证；请在已显示的 Edge 中完成后点“立即扫描”。"
        )
        if self.isVisible():
            QMessageBox.information(
                self,
                "需要人工登录",
                "闲鱼要求重新登录或安全验证。请在 Edge 中手动完成，再点击“立即扫描”。",
            )

    def _load_notification_settings(self) -> None:
        provider_id = self._monitor_store.get_active_provider()
        index = self.provider_combo.findData(provider_id)
        self.provider_combo.blockSignals(True)
        self.provider_combo.setCurrentIndex(max(0, index))
        self.provider_combo.blockSignals(False)
        feishu = self._monitor_store.load_feishu_config()
        wxpusher = self._monitor_store.load_wxpusher_config()
        self.feishu_app_id_edit.setText(feishu.app_id)
        self.feishu_secret_edit.setText(feishu.app_secret)
        self.wxpusher_spt_edit.setText(wxpusher.spt)
        self.feishu_binding_label.setText(
            f"已绑定：…{feishu.open_id[-6:]}" if feishu.open_id else "未绑定接收用户"
        )
        self._provider_changed()
        self._refresh_feishu_setup_status()
        self._refresh_failed_batches()

    def _refresh_feishu_setup_status(self) -> None:
        config = self._monitor_store.load_feishu_config()
        if not config.app_id or not config.app_secret:
            text = "第 1 步/3：填写 App ID 和 App Secret，然后点击“保存飞书配置”。"
        elif not config.open_id:
            text = "第 2 步/3：点击“开始绑定”，再在飞书私聊机器人发送“绑定”。"
        else:
            text = "第 3 步/3：点击“测试飞书”，确认手机收到通知后再启动监控。"
        self.feishu_setup_status.setText(text)

    def _provider_changed(self) -> None:
        provider_id = self.provider_combo.currentData() or "feishu"
        self._monitor_store.set_active_provider(str(provider_id))
        if provider_id == "feishu":
            self.provider_hint.setText(
                "当前使用飞书。需开通机器人、im:message、长连接事件并发布应用版本。"
            )
        else:
            self.provider_hint.setText(
                "当前使用 WxPusher。SPT 相当于私人收件地址，泄露后别人可向你推送消息。"
            )
        if self._monitor_worker is not None:
            self._monitor_worker.wake()

    def _refresh_failed_batches(self) -> None:
        batches = self._monitor_store.list_batches(status="failed")
        self.failed_batch_combo.clear()
        for batch in batches:
            self.failed_batch_combo.addItem(
                f"{batch.task_name} · {batch.total_count} 件 · {batch.last_error[:50]}",
                batch.batch_id,
            )
        self.retry_failed_button.setEnabled(bool(batches))

    def _retry_failed_batch(self) -> None:
        batch_id = self.failed_batch_combo.currentData()
        if not batch_id:
            return
        batch = self._monitor_store.retry_with_current_provider(str(batch_id))
        self._append_monitor_log(
            f"失败通知已改用当前通道 {batch.provider_id} 重新进入队列。"
        )
        self._refresh_failed_batches()
        self._ensure_monitor_scheduler()
        self._monitor_worker.wake()

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
            QMessageBox.information(self, "已保存", "飞书凭证已使用 Windows DPAPI 加密保存。")
        return True

    def _save_wxpusher_settings(self, *, show_message: bool = True) -> bool:
        config = WxPusherConfig(spt=self.wxpusher_spt_edit.text().strip())
        try:
            WxPusherProvider(config).validate_config()
        except ValueError as exc:
            if show_message:
                QMessageBox.warning(self, "SPT 无效", str(exc))
            return False
        self._monitor_store.save_wxpusher_config(config)
        if show_message:
            QMessageBox.information(self, "已保存", "SPT 已使用 Windows DPAPI 加密保存。")
        return True

    def _reset_wxpusher(self) -> None:
        self._monitor_store.save_wxpusher_config(WxPusherConfig())
        self.wxpusher_spt_edit.clear()
        self._append_monitor_log("WxPusher SPT 已从本机配置中清除。")

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
        self._append_monitor_log("飞书绑定窗口已开启，请在5分钟内私聊机器人发送“绑定”。")
        self._feishu_binding_worker.start()

    def _show_feishu_guide(self) -> None:
        QMessageBox.information(
            self,
            "飞书机器人配置向导",
            "请在飞书开放平台依次完成：\n\n"
            "1. 创建企业自建应用。\n"
            "2. 开启机器人能力。\n"
            "3. 开通 im:message 权限。\n"
            "4. 事件订阅选择“使用长连接接收事件”。\n"
            "5. 添加 im.message.receive_v1 事件。\n"
            "6. 把自己加入应用可用范围。\n"
            "7. 创建并发布应用版本。\n\n"
            "然后回到软件填写 App ID 和 App Secret，点击“开始绑定”，"
            "并在5分钟内私聊机器人发送“绑定”。",
        )

    def _feishu_bound(self, open_id: str) -> None:
        self.feishu_binding_label.setText(f"已绑定：…{open_id[-6:]}")
        self._refresh_feishu_setup_status()
        self._append_monitor_log("飞书接收用户绑定成功。")
        QMessageBox.information(self, "绑定成功", "飞书机器人已绑定到这台电脑。")

    def _feishu_binding_failed(self, message: str) -> None:
        self.feishu_binding_label.setText("绑定失败")
        self._append_monitor_log(f"飞书绑定失败：{message}")
        QMessageBox.warning(self, "飞书绑定失败", message)

    def _feishu_binding_expired(self) -> None:
        self.feishu_binding_label.setText("绑定窗口已过期")
        self._append_monitor_log("飞书绑定窗口已过期，请重新开始绑定。")

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
        self._append_monitor_log("飞书接收用户已解绑。")

    def _test_notification(self, provider_id: str) -> None:
        if self._notification_test_worker is not None and self._notification_test_worker.isRunning():
            return
        if provider_id == "feishu":
            if not self._save_feishu_settings(show_message=False):
                QMessageBox.warning(self, "配置不完整", "请先填写并保存飞书配置。")
                return
            provider = FeishuProvider(self._monitor_store.load_feishu_config())
            button = self.feishu_test_button
        else:
            if not self._save_wxpusher_settings(show_message=False):
                QMessageBox.warning(self, "配置不完整", "请输入有效的 SPT_ 令牌。")
                return
            provider = WxPusherProvider(self._monitor_store.load_wxpusher_config())
            button = self.wxpusher_test_button
        button.setEnabled(False)
        self._notification_test_worker = NotificationTestWorker(provider)
        self._notification_test_worker.completed.connect(
            lambda success, message, selected=provider_id: self._notification_test_finished(
                selected, success, message
            )
        )
        self._notification_test_worker.finished.connect(
            self._notification_test_thread_finished
        )
        self._notification_test_worker.start()

    def _notification_test_finished(
        self, provider_id: str, success: bool, message: str
    ) -> None:
        label = "飞书" if provider_id == "feishu" else "WxPusher"
        if success:
            self._append_monitor_log(f"{label}测试通知发送成功。")
            QMessageBox.information(self, "测试成功", f"{label}测试通知已发送。")
        else:
            self._append_monitor_log(f"{label}测试通知失败：{message}")
            QMessageBox.warning(self, "测试失败", message)

    def _notification_test_thread_finished(self) -> None:
        self.feishu_test_button.setEnabled(True)
        self.wxpusher_test_button.setEnabled(True)
        if self._notification_test_worker is not None:
            self._notification_test_worker.deleteLater()
            self._notification_test_worker = None

    def _setup_tray(self) -> None:
        self._tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(application_icon(), self)
        self._tray.setToolTip("闲鱼新品监控")
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
        if self._monitor_worker is not None and self._monitor_worker.isRunning():
            self._monitor_worker.stop()
            if not self._monitor_worker.wait(8_000):
                return False
        if self._feishu_binding_worker is not None and self._feishu_binding_worker.isRunning():
            self._feishu_binding_worker.stop()
            self._feishu_binding_worker.wait(3_000)
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        has_enabled_monitors = any(
            state.config.enabled for state in self._monitor_store.list_task_states()
        )
        if (
            not self._force_exit
            and has_enabled_monitors
            and self._tray is not None
            and self._tray.isVisible()
        ):
            self.hide()
            self._tray.showMessage(
                "闲鱼新品监控仍在运行",
                "窗口已缩到系统托盘。需要完全退出时，请右键托盘图标选择“退出软件”。",
                QSystemTrayIcon.Information,
                4_000,
            )
            event.ignore()
            return
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
            QMessageBox.warning(self, "正在停止监控", "浏览器仍在结束当前操作，请稍后再退出。")
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
    app.setApplicationName("闲鱼商品采集与新品监控")
    app.setOrganizationName("Local")
    app.setWindowIcon(application_icon())
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(light_palette())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

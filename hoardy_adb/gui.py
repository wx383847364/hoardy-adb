"""安卓应用搬家的 Mac 桌面界面，界面线程不执行耗时手机操作。"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from functools import partial
import os
import json
from pathlib import Path
import sys
import threading
import time
from typing import Callable

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSettings, QStandardPaths, QUrl, QSize
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap, QCloseEvent, QPainter, QFont, QPen
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QListWidget, QFrame, QLineEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFileDialog, QMessageBox, QProgressBar,
    QStyledItemDelegate, QStyleOptionViewItem, QStyle, QCheckBox, QPlainTextEdit,
)

from hoardy_adb import apk

ACTIVE_ROLE = Qt.ItemDataRole.UserRole + 2


def install_spinner(frame: int) -> QPixmap:
    """绘制共用的十二瓣旋转提示，状态行与任务卡复用，不依赖动图或额外资源。"""
    pixmap = QPixmap(22, 22)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(11, 11)
    painter.rotate((frame % 12) * 30)
    for index in range(12):
        color = QColor("#087cff")
        color.setAlpha(45 + index * 19)
        painter.setPen(QPen(color, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(0, -6, 0, -9)
        painter.rotate(30)
    painter.end()
    return pixmap

STATES = {"ready": "可导出", "loading": "后台读资料", "unavailable": "不可导出", "pending": "待处理",
          "exported": "已导出", "installed": "安装成功", "skipped": "已跳过", "failed": "失败"}
STYLE = """
QMainWindow, QWidget#main { background: #ffffff; color: #202832; }
QWidget { font-family: '.AppleSystemUIFont', 'PingFang SC'; font-size: 14px; color: #202832; }
QFrame#sidebar { background: #f1f3f6; border-right: 1px solid #e1e5ea; }
QLabel#brand { font-size: 19px; font-weight: 700; }
QLabel#title { font-size: 27px; font-weight: 700; }
QLabel#subtitle, QLabel#muted { color: #7b8492; }
QLabel#section { font-size: 16px; font-weight: 600; }
QLabel#number { font-size: 24px; font-weight: 650; }
QListWidget#nav { background: transparent; border: none; outline: none; }
QListWidget#nav::item { padding: 15px 12px; margin: 4px 0; border-radius: 8px; font-size: 15px; }
QListWidget#nav::item:selected { background: #dceafe; color: #0878f9; font-weight: 600; }
QFrame#summary { background: #f8f9fb; border: 1px solid #e6e9ef; border-radius: 12px; }
QFrame#summary QWidget { background: transparent; }
QFrame#device { border: 1px solid #e0e4ea; border-radius: 10px; background: #ffffff; }
QPushButton { background: #ffffff; border: 1px solid #d8dfe8; border-radius: 7px; padding: 8px 13px; }
QPushButton:hover { background: #eef5ff; border-color: #a6ccfc; }
QPushButton:disabled { color: #a1a9b4; background: #f3f5f7; border-color: #e4e7eb; }
QPushButton#primary { background: #087cff; color: white; border: 0; font-weight: 600; padding: 12px 18px; }
QPushButton#primary:hover { background: #0068df; }
QPushButton#primary:disabled { background: #b8d7fc; color: white; }
QPushButton#secondary { color: #0878f9; border: 1px solid #b4d4ff; padding: 11px; }
QLineEdit, QComboBox { border: 1px solid #dbe1e8; border-radius: 7px; padding: 9px; background: #ffffff; }
QLineEdit:focus { border-color: #1682ff; }
QComboBox::drop-down { border: 0; width: 25px; }
QTableWidget { border: 1px solid #e2e7ed; border-radius: 8px; background: white; gridline-color: #e9edf2; outline: none; }
QTableWidget::item { padding: 8px; border-bottom: 1px solid #e9edf2; }
QTableWidget::item:selected { background: #e6f1ff; color: #202832; }
QHeaderView::section { background: #f3f5f8; border: 0; border-bottom: 1px solid #e2e7ed; padding: 12px 7px; color: #6c7685; }
QTableWidget QCheckBox { margin-left: 10px; }
QProgressBar { border: 0; border-radius: 4px; background: #e7edf5; height: 8px; text-align: center; }
QProgressBar::chunk { background: #1682ff; border-radius: 4px; }
QScrollArea { border: 0; background: transparent; }
QToolTip { background: white; border: 1px solid #dbe1e8; padding: 6px; }
"""


def size_text(size: int) -> str:
    """以安装包字节数显示大小，不冒充迁移数据的大小。"""
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    if size < 1024**3:
        return f"{size / 1024**2:.1f} MB"
    return f"{size / 1024**3:.2f} GB"


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    """创建统一命名的标签，长说明按需换行。"""
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def app_icon(app: apk.App) -> QIcon:
    """优先使用手机真实图标；无法解析的自适应图标使用统一占位图。"""
    image = QPixmap()
    if app.icon and image.loadFromData(app.icon):
        return QIcon(image)
    image = QPixmap(48, 48)
    image.fill(QColor("#dceafe"))
    painter = QPainter(image)
    painter.setPen(QColor("#087cff"))
    painter.setFont(QFont("PingFang SC", 22, QFont.Weight.Bold))
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, (app.name or app.package)[0].upper())
    painter.end()
    return QIcon(image)


class Worker(QThread):
    """把扫描、传输和磁盘检查放到后台，并用信号回到界面线程。"""

    event = Signal(object)
    done = Signal(object)
    error = Signal(str)

    def __init__(self, job: Callable, parent: QWidget):
        """每个任务拥有独立停止标志，防止停止状态泄漏到下一个任务。"""
        super().__init__(parent)
        self.job = job
        self.stop = threading.Event()

    def run(self) -> None:
        """只发出结果或错误，不在工作线程触碰界面控件。"""
        apk.LOG.info("Worker started kind=%s", self.objectName())
        try:
            self.done.emit(self.job(self.event.emit, self.stop))
        except apk.Stopped:
            self.done.emit(None)
        except Exception as exc:
            apk.LOG.exception("Worker failed kind=%s", self.objectName())
            self.error.emit(str(exc))
        finally:
            apk.LOG.info("Worker finished kind=%s", self.objectName())


class SizeItem(QTableWidgetItem):
    """按照真实字节数排序，不能把 10 MB 排在 2 MB 前面。"""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        """保持大小列的数值语义，与单位和显示精度无关。"""
        return self.data(Qt.ItemDataRole.UserRole) < other.data(Qt.ItemDataRole.UserRole)


class SelectedAppDelegate(QStyledItemDelegate):
    """把持久勾选状态显示为整行浅蓝，避免依赖鼠标焦点高亮。"""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        """所有列读取同一行复选框，排序与滚动后仍保持一致的选择提示。"""
        adjusted = QStyleOptionViewItem(option)
        checked = index.siblingAtColumn(0).data(Qt.ItemDataRole.CheckStateRole)
        active = bool(index.siblingAtColumn(0).data(ACTIVE_ROLE))
        if active and index.column() == 4:
            # 状态单元格自行绘制蓝色文字，避免主题的选中色覆盖安装状态颜色。
            painter.save()
            painter.fillRect(option.rect, QColor("#e6f1ff"))
            icon = index.data(Qt.ItemDataRole.DecorationRole)
            if isinstance(icon, QIcon):
                painter.drawPixmap(option.rect.left() + 8, option.rect.center().y() - 11, icon.pixmap(22, 22))
            painter.setPen(QColor("#087cff"))
            font = option.font
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(option.rect.adjusted(36, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "正在安装")
            painter.restore()
            return
        if active or checked == Qt.CheckState.Checked.value:
            adjusted.state |= QStyle.StateFlag.State_Selected
        else:
            adjusted.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, adjusted, index)
        if active and index.column() == 0:
            painter.fillRect(option.rect.left(), option.rect.top(), 4, option.rect.height(), QColor("#087cff"))


class Window(QMainWindow):
    """管理所选手机、应用清单以及导出到本地再安装的两阶段流程。"""

    def __init__(self):
        """恢复本地设置并构建第一版浅色三栏布局。"""
        super().__init__()
        self.settings = QSettings("HoardyADB", "AndroidAppMover")
        default = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)) / "AndroidApps"
        self.library = Path(str(self.settings.value("library", str(default))))
        executable = str(self.settings.value("adb", ""))
        if executable:
            os.environ["HOARDY_ADB"] = executable
        self.mode = "phone"
        self.records: list[apk.App] = []
        self.phone_records: list[apk.App] = []
        self.selected: dict[str, set[str]] = {"phone": set(), "library": set(), "history": set()}
        self.worker: Worker | None = None
        self.device_worker: Worker | None = None
        self.device_poll_uncertain = False
        self.device_snapshot: tuple | None = None
        self.known_devices: dict[str, str] = {}
        self.row_snapshots: dict[str, apk.App] = {}
        self.table_mode = ""
        self.summary_snapshot: tuple | None = None
        self.failure_snapshot: tuple | None = None
        self.startup_library_pending = True
        self.device_usb_hint = ""
        self.active_install: apk.App | None = None
        self.install_started = 0.0
        self.spinner_frame = 0
        self.spinner_timer = QTimer(self)
        self.spinner_timer.setInterval(80)
        self.spinner_timer.timeout.connect(self.tick_install_spinner)
        self.job_kind = ""
        self.devices: list[apk.Device] = []
        self.last_export: list[apk.App] = []
        self.library_records: list[apk.App] = []
        self.failure_records: list[apk.App] = []
        self.queue_target = ""
        self.queue_session = ""
        self.failure_selection: set[str] = set()
        self.last_batch: list[apk.App] = []
        self.retry_operation = "export"
        self.source_serial = ""
        self.roles_initialized = False
        self.close_requested = False
        self.pending_export: list[apk.App] | None = None
        self.export_index: dict[str, list[apk.App]] = {}
        self.export_index_ready = False
        self.index_refresh_pending = False
        self.row_apps: dict[str, apk.App] = {}
        self.setWindowTitle("安卓应用搬家")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1420, screen.width() - 60), min(900, screen.height() - 80))
        self.setMinimumSize(1120, 720)
        self.build_ui()
        self.timer = QTimer(self)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self.refresh_devices)
        self.timer.start()
        QTimer.singleShot(150, self.refresh_devices)

    def build_ui(self) -> None:
        """使用真实列表与设备状态构建界面，不把示例手机写死进产品。"""
        main = QWidget()
        main.setObjectName("main")
        self.setCentralWidget(main)
        outer = QHBoxLayout(main)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 30, 18, 20)
        side.addWidget(label("▣  安卓应用搬家", "brand"))
        side.addSpacing(22)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.addItems(["▦   手机应用", "▱   本地应用库", "◷   迁移记录"])
        self.nav.setFixedHeight(190)
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self.change_view)
        side.addWidget(self.nav)
        side.addSpacing(16)
        side.addWidget(label("已连接设备", "muted"))
        self.source = QComboBox()
        self.target = QComboBox()
        self.source.setToolTip("选择用于读取和导出应用的旧手机")
        self.target.setToolTip("选择用于安装应用的新手机")
        self.source.currentIndexChanged.connect(self.source_changed)
        self.target.currentIndexChanged.connect(self.update_summary)
        for title, combo in [("旧手机 · 来源", self.source), ("新手机 · 目标", self.target)]:
            card = QFrame()
            card.setObjectName("device")
            layout = QVBoxLayout(card)
            layout.addWidget(label(title, "section"))
            layout.addWidget(combo)
            side.addWidget(card)
        self.connection = label("正在检测手机…", "muted", True)
        side.addWidget(self.connection)
        side.addStretch()
        self.refresh = QPushButton("刷新当前列表")
        self.refresh.clicked.connect(self.refresh_all)
        side.addWidget(self.refresh)
        detect = QPushButton("重新检测设备")
        detect.clicked.connect(self.refresh_devices)
        side.addWidget(detect)
        settings = QPushButton("设置 ADB…")
        settings.clicked.connect(self.choose_adb)
        side.addWidget(settings)
        logs = QPushButton("打开日志目录")
        logs.clicked.connect(self.reveal_logs)
        side.addWidget(logs)
        side.addWidget(label("免费 · 本地处理 · 无需账号", "muted"))
        outer.addWidget(sidebar)

        body = QVBoxLayout()
        body.setContentsMargins(28, 28, 24, 22)
        body.setSpacing(16)
        outer.addLayout(body, 1)
        self.title = label("选择要导出的应用", "title")
        self.subtitle = label("连接旧手机，选择应用，保存到 Mac 后安装到新手机。", "subtitle", True)
        body.addWidget(self.title)
        body.addWidget(self.subtitle)
        self.install_activity = QFrame()
        self.install_activity.setStyleSheet("QFrame {background:#edf5ff;border:1px solid #bdd9ff;border-radius:10px;} QLabel {border:0;background:transparent;}")
        activity = QHBoxLayout(self.install_activity)
        activity.setContentsMargins(16, 14, 16, 14)
        self.activity_spinner = QLabel()
        self.activity_spinner.setFixedSize(26, 26)
        activity.addWidget(self.activity_spinner)
        self.activity_icon = QLabel()
        self.activity_icon.setFixedSize(44, 44)
        activity.addWidget(self.activity_icon)
        activity_text = QVBoxLayout()
        self.activity_title = label("", "section")
        self.activity_title.setStyleSheet("color:#0965c9;font-size:19px;font-weight:650;")
        activity_text.addWidget(self.activity_title)
        self.activity_note = label("", "muted", True)
        activity_text.addWidget(self.activity_note)
        activity.addLayout(activity_text, 1)
        self.activity_elapsed = label("已用 00:00", "section")
        activity.addWidget(self.activity_elapsed)
        locate = QPushButton("定位当前应用")
        locate.clicked.connect(self.locate_active_install)
        activity.addWidget(locate)
        body.addWidget(self.install_activity)
        self.install_activity.hide()
        self.batch_banner = QFrame()
        self.batch_banner.setStyleSheet("QFrame {background:#fff7e8;border:1px solid #f5dca7;border-radius:8px;} QLabel {border:0;}")
        banner = QHBoxLayout(self.batch_banner)
        self.pause_text = label("", wrap=True)
        banner.addWidget(self.pause_text, 1)
        reconnect = QPushButton("重新检测连接")
        reconnect.clicked.connect(self.refresh_devices)
        banner.addWidget(reconnect)
        body.addWidget(self.batch_banner)
        self.batch_banner.hide()
        self.batch_stats = QFrame()
        stats_layout = QHBoxLayout(self.batch_stats)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        self.stat_numbers: list[QLabel] = []
        for title, color in [("安装成功", "#278354"), ("已安装，跳过", "#7b8492"),
                             ("安装失败", "#cf534d"), ("尚未处理", "#9b752c")]:
            stat_card = QFrame()
            stat_card.setStyleSheet("QFrame {background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;} QLabel {border:0;}")
            stat_layout = QVBoxLayout(stat_card)
            number = label("0", "number")
            number.setStyleSheet(f"color:{color};")
            stat_layout.addWidget(number)
            caption = label(title, "muted")
            stat_layout.addWidget(caption)
            if title == "尚未处理":
                self.pending_stat_title = caption
            self.stat_numbers.append(number)
            stats_layout.addWidget(stat_card)
        body.addWidget(self.batch_stats)
        self.batch_stats.hide()
        content = QHBoxLayout()
        content.setSpacing(18)
        body.addLayout(content, 1)
        left = QVBoxLayout()
        content.addLayout(left, 1)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索应用名称或包名（如：微信 或 com.tencent.mm）")
        self.search.textChanged.connect(self.apply_filter)
        toolbar.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.addItems(["全部用户应用", "已选择", "不可用 / 失败"])
        self.filter.currentIndexChanged.connect(self.apply_filter)
        toolbar.addWidget(self.filter)
        left.addLayout(toolbar)
        self.batch_filter = QComboBox()
        self.batch_filter.addItem("全部导出批次", "")
        self.batch_filter.currentIndexChanged.connect(self.change_library_batch)
        self.batch_filter.hide()
        left.addWidget(self.batch_filter)
        selection_tools = QHBoxLayout()
        self.select_all = QCheckBox("全选当前结果")
        self.select_all.setTristate(True)
        self.select_all.clicked.connect(self.select_visible)
        self.clear_button = QPushButton("清空选择")
        self.clear_button.clicked.connect(self.clear_selection)
        self.hide_exported = QCheckBox("隐藏已导出")
        self.hide_exported.toggled.connect(self.apply_filter)
        selection_tools.addWidget(self.select_all)
        selection_tools.addWidget(self.clear_button)
        selection_tools.addStretch()
        selection_tools.addWidget(self.hide_exported)
        left.addLayout(selection_tools)
        self.count = label("尚未读取应用", "muted")
        self.count.setWordWrap(True)
        left.addWidget(self.count)
        self.table = QTableWidget(0, 5)
        self.table.setItemDelegate(SelectedAppDelegate(self.table))
        self.table.setHorizontalHeaderLabels(["☐", "应用名称", "版本", "安装包大小", "状态"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(2, 85)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 128)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().sectionClicked.connect(self.header_clicked)
        self.table.itemChanged.connect(self.item_changed)
        self.table.setIconSize(QPixmap(40, 40).size())
        left.addWidget(self.table, 1)
        self.empty = label("用数据线连接旧手机，并开启 USB 调试。", "muted", True)
        left.addWidget(self.empty)
        self.progress = QProgressBar()
        self.progress.hide()
        left.addWidget(self.progress)
        self.status = label("就绪", "muted", True)
        left.addWidget(self.status)
        controls = QHBoxLayout()
        self.stop_button = QPushButton("停止后续任务")
        self.stop_button.clicked.connect(self.stop_task)
        self.stop_button.hide()
        self.retry = QPushButton("重试失败项")
        self.retry.clicked.connect(self.retry_failed)
        self.retry.hide()
        controls.addWidget(self.stop_button)
        controls.addWidget(self.retry)
        self.failure_button = QPushButton("查看失败详情")
        self.failure_button.setEnabled(False)
        self.failure_button.clicked.connect(self.show_failure_details)
        controls.addWidget(self.failure_button)
        controls.addStretch()
        left.addLayout(controls)

        summary = QFrame()
        self.summary_frame = summary
        summary.setObjectName("summary")
        summary.setFixedWidth(265)
        right = QVBoxLayout(summary)
        right.setContentsMargins(18, 20, 18, 20)
        right.setSpacing(14)
        self.summary_title = label("导出清单", "section")
        self.summary_text = label("已选择 0 个应用", "muted")
        right.addWidget(self.summary_title)
        right.addWidget(self.summary_text)
        self.selected_list = QListWidget()
        self.selected_list.setWordWrap(True)
        self.selected_list.setIconSize(QSize(32, 32))
        self.selected_list.setStyleSheet("QListWidget {border:0;background:transparent;} QListWidget::item {padding:5px 0;}")
        right.addWidget(self.selected_list, 1)
        self.total = label("0 MB", "number")
        right.addWidget(self.total)
        right.addWidget(label("保存位置", "section"))
        self.location = label(str(self.library), "muted", True)
        self.location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(self.location)
        folder_buttons = QHBoxLayout()
        change = QPushButton("更改…")
        change.clicked.connect(self.choose_library)
        reveal = QPushButton("打开文件夹")
        reveal.clicked.connect(self.reveal_library)
        folder_buttons.addWidget(change)
        folder_buttons.addWidget(reveal)
        right.addLayout(folder_buttons)
        right.addWidget(label("ⓘ 仅迁移应用安装包，不含应用内数据。", "muted", True))
        right.addSpacing(10)
        self.next_title = label("下一步", "section")
        right.addWidget(self.next_title)
        self.next_button = QPushButton("安装到新手机  →")
        self.next_button.setObjectName("secondary")
        self.next_button.clicked.connect(self.install_last_export)
        right.addWidget(self.next_button)
        self.install_hint = label("进入本地应用库，选择全部或某次导出的应用安装。\n已有应用默认跳过。", "muted", True)
        right.addWidget(self.install_hint)
        content.addWidget(summary)
        self.failure_panel = QFrame()
        self.failure_panel.setObjectName("summary")
        self.failure_panel.setFixedWidth(360)
        details = QVBoxLayout(self.failure_panel)
        details.setContentsMargins(16, 20, 16, 16)
        details.addWidget(label("安装结果详情", "section"))
        self.failure_filter = QComboBox()
        self.failure_filter.addItems(["失败项", "尚未处理"])
        self.failure_filter.currentIndexChanged.connect(self.populate_failure_panel)
        details.addWidget(self.failure_filter)
        self.failure_all = QCheckBox("全选失败项")
        self.failure_all.clicked.connect(self.select_all_failures)
        details.addWidget(self.failure_all)
        self.failure_table = QTableWidget(0, 2)
        self.failure_table.setHorizontalHeaderLabels(["选择", "应用 / 原因"])
        self.failure_table.setColumnWidth(0, 48)
        self.failure_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.failure_table.verticalHeader().hide()
        self.failure_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.failure_table.itemChanged.connect(self.failure_item_changed)
        self.failure_table.currentCellChanged.connect(self.show_failure_error)
        details.addWidget(self.failure_table, 2)
        details.addWidget(label("完整原因 / 原始错误", "section"))
        self.failure_error = QPlainTextEdit()
        self.failure_error.setReadOnly(True)
        details.addWidget(self.failure_error, 1)
        copy_error = QPushButton("复制错误信息")
        copy_error.clicked.connect(self.copy_failure_error)
        details.addWidget(copy_error)
        self.failure_hint = label("", "muted", True)
        details.addWidget(self.failure_hint)
        self.retry_selected = QPushButton("重试所选失败项")
        self.retry_selected.setObjectName("primary")
        self.retry_selected.clicked.connect(self.retry_selected_failures)
        details.addWidget(self.retry_selected)
        collapse = QPushButton("收起详情")
        collapse.clicked.connect(self.show_failure_details)
        details.addWidget(collapse)
        content.addWidget(self.failure_panel)
        self.failure_panel.hide()

        footer = QHBoxLayout()
        self.action_reason = label("", "muted", True)
        body.addWidget(self.action_reason)
        self.footer = label("已选择 0 个应用", "section")
        footer.addWidget(self.footer, 1)
        self.action = QPushButton("导出所选应用")
        self.action.setObjectName("primary")
        self.action.clicked.connect(self.start_selected)
        footer.addWidget(self.action)
        body.addLayout(footer)
        self.update_summary()

    def serial(self, target: bool = False) -> str:
        """只返回已授权且在线设备的序列号。"""
        combo = self.target if target else self.source
        selected = str(combo.currentData() or "")
        return selected if not self.device_poll_uncertain and any(
            device.serial == selected and device.state == "device" for device in self.devices) else ""

    def start_job(self, kind: str, job: Callable) -> None:
        """同一窗口只运行一个扫描或迁移任务，避免共享状态竞争。"""
        if self.worker is not None:
            return
        self.job_kind = kind
        self.worker = Worker(job, self)
        self.worker.setObjectName(kind)
        self.worker.event.connect(self.handle_event)
        self.worker.done.connect(self.job_done)
        self.worker.error.connect(self.job_error)
        self.worker.finished.connect(self.job_finished)
        self.refresh.setEnabled(False)
        self.source.setEnabled(False)
        self.target.setEnabled(False)
        self.nav.setEnabled(False)
        if kind != "devices":
            self.progress.setRange(0, 0)
            self.progress.show()
            self.stop_button.setVisible(kind in ("scan", "export", "install"))
        self.update_summary()
        self.worker.start()

    def device_job(self, notify: apk.Notify, stop: threading.Event) -> list[apk.Device]:
        """后台读取连接状态，不触碰或修改手机应用。"""
        started = time.monotonic()
        adb = apk.Adb()
        if self.device_snapshot is None:
            try:
                apk.LOG.info("Shared ADB server status: %s", adb.run(["server-status"], timeout=10).strip())
            except RuntimeError:
                apk.LOG.info("ADB server status is unavailable for this version")
        devices = adb.devices()
        apk.LOG.info("Device poll elapsed=%.3fs devices=%s", time.monotonic() - started,
                     [(device.serial, device.model, device.state) for device in devices])
        wanted = self.queue_target or self.source_serial
        hint = ""
        if wanted and not any(device.serial == wanted and device.state == "device" for device in devices):
            hint = apk.usb_diagnostic(wanted)
        notify({"kind": "usb_diagnostic", "hint": hint})
        return devices

    def refresh_devices(self) -> None:
        """独立只读轮询不占用迁移线程，也不锁按钮、重建列表或显示全局进度。"""
        if self.device_worker is not None or self.close_requested:
            return
        self.device_worker = Worker(self.device_job, self)
        self.device_worker.setObjectName("devices")
        self.device_worker.event.connect(self.device_poll_event)
        self.device_worker.done.connect(self.device_poll_done)
        self.device_worker.error.connect(self.device_poll_error)
        self.device_worker.finished.connect(self.device_poll_finished)
        self.device_worker.start()

    def device_poll_event(self, event: dict) -> None:
        """把 USB 物理连接与调试接口的诊断局部显示到设备区，并记录变化。"""
        hint = event.get("hint", "")
        if hint == self.device_usb_hint:
            return
        self.device_usb_hint = hint
        apk.LOG.info("USB diagnosis changed: %s", hint or "ADB target is online")
        if hint:
            self.connection.setText(hint)
        self.update_summary()

    def device_poll_done(self, result: object) -> None:
        """只在设备状态变化时更新设备区，首次选定旧手机后才启动应用扫描。"""
        self.set_devices(result or [])
        if self.startup_library_pending and self.worker is None:
            # 先从既有清单识别原目标手机，不能因它是唯一在线设备就当成旧手机扫描。
            self.startup_library_pending = False
            self.refresh_export_index()
            return
        if not self.worker and self.mode == "phone" and self.serial() and self.source_serial != self.serial():
            self.scan_phone()

    def device_poll_error(self, message: str) -> None:
        """检测失败保留上次设备和列表，暂停新传输，等待下次成功轮询恢复状态。"""
        self.device_poll_uncertain = True
        apk.LOG.warning("Device detection uncertain; keeping previous devices; error=%s", message)
        self.connection.setText("设备检测暂时失败，已保留列表，正在重试。")
        self.update_summary()

    def device_poll_finished(self) -> None:
        """仅释放轮询线程，不改动表格、进度、选择或任务按钮。"""
        previous = self.device_worker
        self.device_worker = None
        if previous:
            previous.deleteLater()
        if self.close_requested and self.worker is None:
            self.close()

    def refresh_all(self) -> None:
        """仅刷新当前页面的数据列表；设备检测有独立按钮与后台轮询。"""
        if self.worker is None:
            if self.mode == "phone" and self.serial():
                self.scan_phone()
            elif self.mode != "phone":
                self.load_view()
            else:
                self.refresh_devices()

    def set_devices(self, devices: list[apk.Device]) -> None:
        """增量同步设备选项；离线设备保留角色与应用列表，但不能启动传输。"""
        snapshot = (self.queue_target, tuple(sorted((device.serial, device.model, device.state) for device in devices)))
        was_uncertain = self.device_poll_uncertain
        self.device_poll_uncertain = False
        if snapshot == self.device_snapshot and not was_uncertain:
            return
        apk.LOG.info("Device state changed old=%s new=%s", self.device_snapshot, snapshot)
        self.device_snapshot = snapshot
        old_source = str(self.source.currentData() or "")
        old_target = str(self.target.currentData() or "")
        self.devices = list(devices)
        for device in devices:
            self.known_devices[device.serial] = device.model
        online = [device for device in devices if device.state == "device"]
        serials = {device.serial for device in online}
        source = old_source or (next((device.serial for device in online if device.serial != self.queue_target), "")
                                if not self.roles_initialized and self.mode == "phone" else "")
        target = old_target or next((device.serial for device in online if device.serial != source), "")
        if self.queue_target in serials:
            target = self.queue_target
            if source == target:
                source = ""
        states = {device.serial: device.state for device in devices}
        for combo, selected, placeholder in [(self.source, source, "请选择旧手机"), (self.target, target, "请选择新手机")]:
            combo.blockSignals(True)
            if combo.count() == 0:
                combo.addItem(placeholder, "")
            for serial, model in self.known_devices.items():
                state = states.get(serial, "disconnected")
                suffix = {"device": "", "unauthorized": "（待授权）", "offline": "（离线）"}.get(state, "（未连接）")
                text = model + suffix
                index = combo.findData(serial)
                if index < 0:
                    combo.addItem(text, serial)
                    index = combo.count() - 1
                    combo.setItemData(index, serial, Qt.ItemDataRole.ToolTipRole)
                elif combo.itemText(index) != text:
                    combo.setItemText(index, text)
            index = max(0, combo.findData(selected))
            if combo.currentIndex() != index:
                combo.setCurrentIndex(index)
            combo.blockSignals(False)
        if online:
            self.roles_initialized = True
        if any(device.state == "unauthorized" for device in devices):
            self.connection.setText("请解锁手机，并在弹窗中允许 USB 调试。")
        elif online:
            self.connection.setText(f"● 已连接 {len(online)} 台设备")
        else:
            self.connection.setText(self.device_usb_hint or "手机已断开，已保留设备与列表。请检查连接并允许 USB 调试。")
        if self.mode == "phone" and self.source_serial and self.source_serial not in serials and not self.worker:
            self.status.setText("旧手机暂不可用，已保留上次应用清单和选择；重新连接后可继续。")
        self.update_summary()

    def source_changed(self) -> None:
        """切换旧手机时清除原设备清单，防止沿用另一台手机的应用路径。"""
        if self.worker is not None:
            return
        self.phone_records = []
        self.selected["phone"].clear()
        if self.mode == "phone":
            self.set_records([])
            self.scan_phone()

    def scan_job(self, serial: str, notify: apk.Notify, stop: threading.Event) -> list[apk.App]:
        """在同一后台流程发布清单、核对备份和补充资料，不另起任务抢占扫描。"""
        cache = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / serial
        return apk.scan_apps(apk.Adb(serial), cache, notify, stop, library=self.library)

    def scan_phone(self) -> None:
        """同设备刷新保留列表、选择与滚动位置，仅在新清单到达后更新变化行。"""
        serial = self.serial()
        if not serial or self.worker:
            return
        if serial != self.source_serial:
            self.selected["phone"].clear()
            self.phone_records = []
            self.set_records([])
        self.source_serial = serial
        self.status.setText("正在刷新应用清单，已保留现有内容…")
        self.start_job("scan", partial(self.scan_job, serial))

    def library_job(self, history: bool, notify: apk.Notify, stop: threading.Event) -> list[apk.App]:
        """后台读取当前目录的记录和导出索引，目录快照用于拒绝迟到的旧结果。"""
        library = self.library
        records = apk.load_library(library, history)
        past = records if history else apk.load_library(library, True)
        installs = [app for app in past if app.operation == "install"]
        latest = installs[0].session if installs else ""
        target = ""
        if latest:
            try:
                target = json.loads((Path(latest) / "manifest.json").read_text(encoding="utf-8")).get("serial", "")
            except (OSError, ValueError):
                pass
        notify({"kind": "batch_details", "apps": [app for app in installs if app.session == latest],
                "serial": target, "session": latest, "library": str(library.resolve())})
        notify({"kind": "export_index", "index": apk.build_export_index(library),
                "library": str(library.resolve()), "completed": 0, "total": len(records)})
        return records

    def refresh_export_index(self) -> None:
        """切换应用库后排队核对；空闲时复用现有 Worker，不重新扫描手机。"""
        if self.worker:
            self.index_refresh_pending = True
            return
        self.index_refresh_pending = False
        if self.mode == "phone":
            self.start_job("index", partial(self.library_job, False))
        else:
            self.load_view()

    def load_view(self) -> None:
        """根据导航恢复手机清单或重新读取磁盘上的应用库。"""
        if self.worker:
            return
        if self.mode == "phone":
            self.set_records(self.phone_records)
            if not self.phone_records and self.serial():
                self.scan_phone()
            elif not self.export_index_ready:
                self.refresh_export_index()
        else:
            self.start_job(self.mode, partial(self.library_job, self.mode == "history"))

    def change_view(self, row: int) -> None:
        """切换页面时保留各页勾选；传输期间仍可查看已完成项。"""
        self.mode = ("phone", "library", "history")[max(0, row)]
        # 单线换机时只有新手机在线；进入应用库后允许把唯一设备作为目标。
        if self.mode != "phone" and self.serial() and not self.serial(True):
            online = [device for device in self.devices if device.state == "device"]
            if len(online) == 1:
                serial = self.serial()
                self.source.blockSignals(True)
                self.source.setCurrentIndex(0)
                self.source.blockSignals(False)
                self.target.setCurrentIndex(self.target.findData(serial))
        self.title.setText({"phone": "选择要导出的应用", "library": "本地应用库", "history": "迁移记录"}[self.mode])
        self.subtitle.setText({
            "phone": "从旧手机选择应用，保存到 Mac 后安装到新手机。",
            "library": "选择已完整导出的应用，安装到指定的新手机。",
            "history": "查看每次导出和安装的结果，失败项可以单独重试。",
        }[self.mode])
        self.summary_title.setText("导出清单" if self.mode == "phone" else "安装清单")
        self.action.setText("导出所选应用" if self.mode == "phone" else "安装所选应用")
        self.hide_exported.setVisible(self.mode == "phone")
        self.batch_filter.setVisible(self.mode == "library")
        self.search.clear()
        self.filter.setCurrentIndex(0)
        if not self.worker:
            self.load_view()
        self.update_summary()

    def set_library_records(self, apps: list[apk.App]) -> None:
        """缓存全部备份并按导出目录提供批次选项，刷新时保留仍然存在的批次。"""
        previous = self.batch_filter.currentData()
        self.library_records = list(apps)
        sessions = sorted({app.session for app in apps}, reverse=True)
        options = [("全部导出批次", "")]
        for session in sessions:
            count = sum(app.session == session for app in apps)
            title = Path(session).name if session else "旧版备份（无批次记录）"
            options.append((f"{title} · {count} 个应用", session or "legacy"))
        existing = [(self.batch_filter.itemText(index), self.batch_filter.itemData(index)) for index in range(self.batch_filter.count())]
        if existing != options:
            self.batch_filter.blockSignals(True)
            self.batch_filter.clear()
            for text, data in options:
                self.batch_filter.addItem(text, data)
            self.batch_filter.setCurrentIndex(max(0, self.batch_filter.findData(previous)))
            self.batch_filter.blockSignals(False)
        self.change_library_batch(preserve_selection=True)

    def change_library_batch(self, index: int = 0, *, preserve_selection: bool = False) -> None:
        """切换批次清空安装选择；全部视图按包去重，优先最新的完整备份。"""
        if self.mode != "library":
            return
        session = self.batch_filter.currentData() or ""
        apps = [app for app in self.library_records
                if not session or app.session == ("" if session == "legacy" else session)]
        if not session:
            chosen: dict[str, apk.App] = {}
            for app in apps:
                previous = chosen.get(app.package)
                # 清单按时间倒序读取，损坏的新副本不能挡住有效的旧副本。
                if previous is None or (previous.status != "exported" and app.status == "exported"):
                    chosen[app.package] = app
            apps = list(chosen.values())
        if not preserve_selection:
            self.selected["library"].clear()
        self.set_records(apps)

    def set_records(self, apps: list[apk.App]) -> None:
        """按稳定键增删改表格行；同页刷新保留排序、选择与滚动，不重建未变行。"""
        apps = list(apps)
        self.records = apps
        self.table.horizontalHeaderItem(2).setText("任务时间" if self.mode == "history" else "版本")
        self.table.setColumnWidth(2, 120 if self.mode == "history" else 85)
        self.selected[self.mode].intersection_update(app.key for app in apps)
        vertical = self.table.verticalScrollBar().value()
        horizontal = self.table.horizontalScrollBar().value()
        self.table.blockSignals(True)
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        if self.table_mode != self.mode:
            # 跨页面使用不同的行身份；只有真正切页才清空旧页面表格。
            self.table.setRowCount(0)
            self.row_apps.clear()
            self.row_snapshots.clear()
            self.table_mode = self.mode
        current = {(app.key if self.mode != "history" else f"{app.session}|{app.package}"): app for app in apps}
        removed, updated, added = 0, 0, 0
        for row in range(self.table.rowCount() - 1, -1, -1):
            key = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if key not in current:
                self.table.removeRow(row)
                self.row_apps.pop(key, None)
                self.row_snapshots.pop(key, None)
                removed += 1
        rows = {self.table.item(row, 0).data(Qt.ItemDataRole.UserRole): row for row in range(self.table.rowCount())}
        for key, app in current.items():
            if key not in rows:
                self.append_row(app)
                added += 1
            elif self.row_snapshots.get(key) != app:
                self.append_row(app, rows[key])
                updated += 1
            else:
                self.row_apps[key] = app
                self.table.item(rows[key], 0).setCheckState(Qt.CheckState.Checked if app.key in self.selected[self.mode] else Qt.CheckState.Unchecked)
        self.table.setSortingEnabled(sorting)
        self.table.blockSignals(False)
        self.apply_filter()
        self.table.verticalScrollBar().setValue(vertical)
        self.table.horizontalScrollBar().setValue(horizontal)
        apk.LOG.info("List refreshed view=%s total=%d added=%d updated=%d removed=%d", self.mode, len(apps), added, updated, removed)

    def append_row(self, app: apk.App, row: int | None = None) -> None:
        """新增或就地更新一行，保留滚动位置与包名对应的勾选状态。"""
        if row is None:
            row = self.table.rowCount()
            self.table.insertRow(row)
        self.table.setRowHeight(row, 72)
        key = app.key if self.mode != "history" else f"{app.session}|{app.package}"
        self.row_apps[key] = app
        self.row_snapshots[key] = replace(app, paths=list(app.paths), files=list(app.files), hashes=dict(app.hashes))
        checkbox = QTableWidgetItem()
        checkbox.setData(Qt.ItemDataRole.UserRole, key)
        selectable = app.status != "unavailable" and self.mode != "history"
        checkbox.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable if selectable else Qt.ItemFlag.NoItemFlags)
        checkbox.setCheckState(Qt.CheckState.Checked if app.key in self.selected[self.mode] else Qt.CheckState.Unchecked)
        self.table.setItem(row, 0, checkbox)
        package_line = app.package
        if self.mode == "history":
            package_line = ("导出 · " if app.operation == "export" else "安装 · ") + package_line
        name = QTableWidgetItem(f"{app.name or app.package}\n{package_line}")
        name.setIcon(app_icon(app))
        name.setToolTip(app.error or app.package)
        self.table.setItem(row, 1, name)
        version = app.version or "—"
        if self.mode == "history":
            stamp = Path(app.session).name
            version = stamp[:10] + "\n" + stamp[11:19].replace("-", ":")
        self.table.setItem(row, 2, QTableWidgetItem(version))
        size = SizeItem("待确认" if app.status == "loading" else size_text(app.size))
        size.setData(Qt.ItemDataRole.UserRole, app.size)
        self.table.setItem(row, 3, size)
        status = QTableWidgetItem(STATES.get(app.status, app.status))
        color = "#7b8492" if app.status == "loading" else "#cf534d" if app.status in ("failed", "unavailable") else "#25894d"
        status.setForeground(QColor(color))
        status.setToolTip(app.error or (Path(app.session).name if app.session else ""))
        self.table.setItem(row, 4, status)

    def apply_filter(self) -> None:
        """组合筛选并同步隐藏项选择；普通搜索保留勾选，隐藏已导出必须取消勾选。"""
        query = self.search.text().strip().casefold()
        visible = 0
        states = Counter()
        visibility = {}
        self.table.blockSignals(True)
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        for row in range(self.table.rowCount()):
            checkbox = self.table.item(row, 0)
            app = self.row_apps[checkbox.data(Qt.ItemDataRole.UserRole)]
            active = self.mode == "library" and self.active_install is not None and app.key == self.active_install.key
            checkbox.setData(ACTIVE_ROLE, active)
            filter_status = app.status
            matches = query in f"{app.name} {app.package}".casefold()
            if self.mode == "phone":
                state = apk.export_state(app, self.export_index, self.export_index_ready)
                states[state] += 1
                exported = state == "exported"
                if exported and self.hide_exported.isChecked():
                    self.selected["phone"].discard(app.package)
                    checkbox.setCheckState(Qt.CheckState.Unchecked)
                    matches = False
                cell = self.table.item(row, 4)
                text = {"exported": "已导出", "not_exported": "未导出", "checking": "版本核对中"}[state]
                if app.status == "unavailable":
                    text += "\n不可导出"
                cell.setText(text)
                color = "#cf534d" if app.status == "unavailable" else "#25894d" if exported else "#7b8492"
                cell.setForeground(QColor(color))
                hint = ""
                if exported:
                    hint = "当前应用库中已有该版本的可用备份。"
                elif state == "not_exported" and self.export_index.get(app.package):
                    hint = "已有其他版本备份，当前版本需重新导出。"
                elif state == "checking":
                    hint = "正在核对当前版本与本地备份，确认前不会隐藏。"
                cell.setToolTip(app.error or hint)
            if self.mode == "library":
                result = next((item for item in self.failure_records if item.operation == "install"
                               and item.package == app.package and item.files == app.files), None)
                cell = self.table.item(row, 4)
                cell.setText(STATES.get(result.status, result.status) if result else STATES.get(app.status, app.status))
                cell.setForeground(QColor("#cf534d" if result and result.status == "failed" else "#7b8492" if result and result.status == "pending" else "#25894d"))
                cell.setToolTip(apk.explain_error(result.error) if result and result.error else app.error)
                if result:
                    filter_status = result.status
                if active:
                    cell.setText("正在安装")
                    cell.setForeground(QColor("#087cff"))
                    cell.setIcon(QIcon(install_spinner(self.spinner_frame)))
                else:
                    cell.setIcon(QIcon())
                    if self.active_install and result and result.status == "pending":
                        cell.setText("排队等待")
                font = cell.font()
                if font.bold() != active:
                    font.setBold(active)
                    cell.setFont(font)
            if self.filter.currentIndex() == 1:
                matches = matches and app.key in self.selected[self.mode]
            elif self.filter.currentIndex() == 2:
                matches = matches and filter_status in ("failed", "unavailable", "pending")
            visibility[checkbox.data(Qt.ItemDataRole.UserRole)] = matches
            visible += int(matches)
        self.table.setSortingEnabled(sorting)
        # 状态列排序会移动行，必须在排序完成后按稳定键设置可见性。
        for row in range(self.table.rowCount()):
            key = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            self.table.setRowHidden(row, not visibility[key])
        self.table.blockSignals(False)
        if self.mode == "phone":
            text = f"共 {len(self.records)} 个 · 已导出 {states['exported']} 个 · 未导出 {states['not_exported']} 个"
            if states["checking"]:
                text += f" · 核对中 {states['checking']} 个"
            self.count.setText(text + f" · 当前显示 {visible} 个")
            if self.pending_export is not None:
                current = {app.package: app for app in self.phone_records}
                self.pending_export = [app for app in self.pending_export
                                       if app.package in self.selected["phone"] and not (
                                           self.hide_exported.isChecked() and apk.export_state(
                                               current.get(app.package, app), self.export_index,
                                               self.export_index_ready) == "exported")]
        else:
            self.count.setText(f"共 {len(self.records)} 个应用，当前显示 {visible} 个")
        self.empty.setVisible(not self.records)
        self.empty.setText("未发现记录。连接旧手机并刷新，或选择已有备份目录。")
        self.update_summary()

    def item_changed(self, item: QTableWidgetItem) -> None:
        """复选框驱动选择集合，显示顺序和搜索结果不能改变选择身份。"""
        if item.column() != 0:
            return
        app = self.row_apps[item.data(Qt.ItemDataRole.UserRole)]
        if item.checkState() == Qt.CheckState.Checked:
            self.selected[self.mode].add(app.key)
        else:
            self.selected[self.mode].discard(app.key)
        self.apply_filter()

    def header_clicked(self, section: int) -> None:
        """原表头勾选与可见全选控件共用同一套选择规则。"""
        if section != 0 or self.mode == "history":
            return
        rows = [row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)
                and self.table.item(row, 0).flags() & Qt.ItemFlag.ItemIsEnabled]
        checked = all(self.table.item(row, 0).checkState() == Qt.CheckState.Checked for row in rows)
        self.select_visible(not checked)

    def select_visible(self, checked: bool) -> None:
        """全选或取消当前可见的可操作项，搜索隐藏的选择保持不变。"""
        if self.mode == "history":
            return
        rows = [row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)
                and self.table.item(row, 0).flags() & Qt.ItemFlag.ItemIsEnabled]
        self.table.blockSignals(True)
        for row in rows:
            item = self.table.item(row, 0)
            app = self.row_apps[item.data(Qt.ItemDataRole.UserRole)]
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            if checked:
                self.selected[self.mode].add(app.key)
            else:
                self.selected[self.mode].discard(app.key)
        self.table.blockSignals(False)
        self.apply_filter()

    def clear_selection(self) -> None:
        """清空当前页面全部勾选，也清理尚未启动的手机导出请求。"""
        self.selected[self.mode].clear()
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self.apply_filter()

    def sync_selection_controls(self) -> None:
        """用可见可选行计算三态全选，不能把隐藏行的选择当作全选。"""
        items = [self.table.item(row, 0) for row in range(self.table.rowCount())
                 if not self.table.isRowHidden(row) and self.table.item(row, 0).flags() & Qt.ItemFlag.ItemIsEnabled]
        selected = sum(item.checkState() == Qt.CheckState.Checked for item in items)
        state = Qt.CheckState.Unchecked if not selected else Qt.CheckState.Checked if selected == len(items) else Qt.CheckState.PartiallyChecked
        self.select_all.blockSignals(True)
        self.select_all.setCheckState(state)
        self.select_all.blockSignals(False)
        self.select_all.setEnabled(bool(items) and self.mode != "history")
        self.clear_button.setEnabled(bool(self.selected[self.mode]))
        self.table.horizontalHeaderItem(0).setText("☑" if state == Qt.CheckState.Checked else "◩" if selected else "☐")

    def selected_apps(self) -> list[apk.App]:
        """从当前完整列表返回已选项，搜索隐藏的已选项仍包含在任务内。"""
        return [app for app in self.records if app.key in self.selected[self.mode]]

    def update_summary(self) -> None:
        """同步右侧清单与主按钮，空选择或错误设备角色不能启动任务。"""
        if not hasattr(self, "action"):
            return
        apps = self.selected_apps()
        active = self.active_install if self.mode == "library" else None
        display_apps = [active] if active else apps
        total = size_text(sum(app.size for app in display_apps))
        text = f"已选择 {len(apps)} 个应用 · {total}"
        pending = sum(app.status == "loading" for app in apps)
        if pending:
            text += f"（{pending} 个大小待确认）"
        self.summary_text.setText(f"已选择 {len(apps)} 个应用")
        self.summary_title.setText("当前安装" if active else "导出清单" if self.mode == "phone" else "安装清单")
        if active:
            self.summary_text.setText("正在安装 1 个应用")
            text = f"正在安装 · {active.name or active.package} · {total}"
        self.footer.setText(text)
        self.total.setText(total + (" + 待确认" if pending else ""))
        snapshot = tuple((app.key, app.name, app.package, app.size, app.status == "loading", app.icon) for app in display_apps)
        if snapshot != self.summary_snapshot:
            # 设备轮询或结果计数变化不应重建用户正在阅读的所选清单。
            scroll = self.selected_list.verticalScrollBar().value()
            self.selected_list.clear()
            for app in display_apps:
                self.selected_list.addItem(f"{app.name or app.package}\n" + ("大小待确认" if app.status == "loading" else size_text(app.size)))
                item = self.selected_list.item(self.selected_list.count() - 1)
                item.setIcon(app_icon(app))
                item.setSizeHint(QSize(190, 44))
            self.selected_list.verticalScrollBar().setValue(scroll)
            self.summary_snapshot = snapshot
        self.table.viewport().update()
        idle = self.worker is None and self.active_install is None
        can_export = idle or self.job_kind == "scan"
        source, target = self.serial(), self.serial(True)
        different = bool(target) and target != source
        enabled = bool(apps) and not self.pending_export and ((can_export and bool(source)) if self.mode == "phone" else (idle and different))
        self.action.setEnabled(enabled and self.mode != "history")
        self.next_button.setVisible(self.mode == "phone")
        self.next_title.setVisible(self.mode == "phone")
        if self.mode == "library":
            self.next_button.setText(f"安装所选 {len(apps)} 个应用  →" if apps else "请先勾选应用")
            self.next_button.setEnabled(idle and different and bool(apps))
            self.install_hint.setText("先勾选应用或点击“全选当前结果”，\n再点击安装；已有应用默认跳过。")
        else:
            self.next_button.setText("选择备份并安装  →")
            self.next_button.setEnabled(idle)
            self.install_hint.setText("进入本地应用库，选择全部或某次导出的应用安装。\n已有应用默认跳过。")
        self.retry.setEnabled(idle)
        pending_apps = [app for app in self.failure_records if app.operation == "install" and app.status == "pending"]
        original_target = bool(target) and (not self.queue_target or target == self.queue_target)
        if self.mode == "library" and not apps and pending_apps:
            self.action.setText(f"继续剩余 {len(pending_apps)} 个应用")
            self.action.setEnabled(idle and different and original_target)
            self.install_hint.setText(f"本批次还有 {len(pending_apps)} 个尚未处理。恢复原手机连接后，点击底部“继续剩余”。")
        elif self.mode == "library":
            self.action.setText(f"安装所选 {len(apps)} 个应用" if apps else "安装所选应用")
        if active:
            self.action.setText("正在安装…")
            self.action.setEnabled(False)
            self.install_hint.setText("正在处理当前应用。若手机弹出安装确认，请及时处理。\n“停止后续任务”会等待当前应用结束后暂停队列。")
        reason = ""
        if self.mode == "library":
            if self.device_poll_uncertain:
                reason = "设备检测暂时失败，已保留应用列表与选择；正在重试，稍后可重新检测设备。"
            elif not target:
                reason = self.device_usb_hint or "尚未连接或选择新手机。请连接数据线、允许 USB 调试，点击“重新检测连接”。"
            elif target == source:
                reason = "来源和目标是同一台手机，请重新选择新手机。"
            elif not idle:
                reason = "正在安装，单项失败会记录原因并继续下一项。" if self.job_kind == "install" else "正在读取资料，请稍候；仍可查看结果。"
            elif not apps and pending_apps and not original_target:
                reason = "请重新连接并选择本批次原来的新手机，再继续剩余应用。"
            elif not apps and not pending_apps:
                reason = "请勾选要安装的应用，或在右侧失败详情中选择失败项重试。"
            elif apps:
                reason = "单项失败后继续下一项；已安装应用自动跳过。"
        self.action_reason.setText(reason)
        failures = {app.package for app in self.failure_records if app.status == "failed"}
        selected_failures = failures & self.failure_selection
        self.failure_all.blockSignals(True)
        self.failure_all.setChecked(bool(failures) and failures == selected_failures)
        self.failure_all.blockSignals(False)
        self.retry_selected.setText(f"重试所选 {len(selected_failures)} 个应用")
        exporting = any(app.operation == "export" and app.status == "failed" for app in self.failure_records)
        self.retry_selected.setEnabled(idle and bool(selected_failures) and (bool(source) if exporting else different and original_target))
        self.failure_hint.setText("请连接并选择本批次原来的新手机。" if not different or not original_target else
                                  "选择失败项后重试，不包含尚未处理的应用。")
        self.batch_banner.setVisible(self.mode == "library" and bool(pending_apps) and idle)
        self.pause_text.setText(f"安装已暂停 · 保留 {len(pending_apps)} 个未处理应用。" +
                               ("原目标手机已连接；清空应用选择后可点击“继续剩余”。" if original_target and different else
                                "请重新连接原目标手机，随后可继续剩余应用。"))
        self.batch_stats.setVisible(self.mode == "library" and bool(self.failure_records))
        self.install_activity.setVisible(self.mode == "library" and self.active_install is not None)
        self.sync_selection_controls()

    def choose_library(self) -> None:
        """切库后立即清空旧索引再后台核对，防止套用上一目录的导出状态。"""
        if self.worker:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择应用库目录", str(self.library))
        if folder:
            self.library = Path(folder)
            self.settings.setValue("library", folder)
            self.location.setText(folder)
            self.export_index = {}
            self.export_index_ready = False
            self.library_records = []
            self.failure_records = []
            self.last_batch = []
            self.queue_target, self.queue_session = "", ""
            self.failure_selection.clear()
            self.update_failure_button()
            self.batch_filter.blockSignals(True)
            self.batch_filter.clear()
            self.batch_filter.addItem("全部导出批次", "")
            self.batch_filter.blockSignals(False)
            self.apply_filter()
            self.refresh_export_index()

    def choose_adb(self) -> None:
        """允许配置独立 ADB，可修复电脑没有安装工具或旧路径失效的情况。"""
        if self.worker:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "选择 Android platform-tools 中的 adb")
        if filename:
            self.settings.setValue("adb", filename)
            os.environ["HOARDY_ADB"] = filename
            self.refresh_devices()

    def reveal_library(self) -> None:
        """打开当前应用库；尚未创建时只打开其已存在的父目录。"""
        path = self.library if self.library.exists() else self.library.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def reveal_logs(self) -> None:
        """打开应用的本地滚动日志目录，便于用户查找连接和安装错误。"""
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(apk.LOG_PATH.parent)))

    def batch_job(self, serial: str, apps: list[apk.App], operation: str,
                  notify: apk.Notify, stop: threading.Event, resume: Path | None = None) -> list[apk.App]:
        """任务开始时固定手机和应用清单，随后切换视图不会改变操作目标。"""
        return apk.run_batch(apk.Adb(serial), apps, self.library, operation, notify, stop, resume)

    def begin_batch(self, apps: list[apk.App], operation: str, resume: Path | None = None) -> None:
        """导出请求可抢先结束后台资料读取；实际导出始终重新获取完整文件组。"""
        serial = self.serial(operation == "install")
        if not apps or not serial:
            return
        if self.worker:
            if self.job_kind == "scan" and operation == "export":
                self.pending_export = list(apps)
                self.worker.stop.set()
                self.table.setEnabled(False)
                self.status.setText("正在结束后台资料读取，随后导出所选应用…")
                self.update_summary()
            return
        if operation == "install" and serial == self.serial():
            QMessageBox.warning(self, "请选择新手机", "来源和目标不能是同一台手机。")
            return
        if resume is not None and self.queue_target and serial != self.queue_target:
            QMessageBox.warning(self, "请连接原新手机", "此队列只能在本批次原来的新手机上继续或重试。")
            return
        if operation == "export" and any(app.source and app.source != serial for app in apps):
            QMessageBox.warning(self, "请连接原旧手机", "这些应用来自另一台旧手机，请选择原设备后重试。")
            return
        if operation == "install":
            response = QMessageBox.question(
                self, "安装到新手机", f"将在 {self.target.currentText()} 上安装 {len(apps)} 个应用。\n"
                "已有应用会跳过，不会卸载或覆盖。是否开始？")
            if response != QMessageBox.StandardButton.Yes:
                return
        self.last_batch = []
        if resume is None:
            self.failure_records = []
            self.failure_selection.clear()
        self.update_failure_button()
        self.retry_operation = operation
        self.retry.hide()
        self.status.setText("正在导出应用…" if operation == "export" else "正在安装应用…")
        self.start_job(operation, partial(self.batch_job, serial, list(apps), operation, resume=resume))

    def start_selected(self) -> None:
        """主按钮根据页面执行导出或安装，不自动连带执行下一阶段。"""
        if self.mode == "library" and not self.selected_apps():
            self.continue_pending()
            return
        self.begin_batch(self.selected_apps(), "export" if self.mode == "phone" else "install")

    def install_last_export(self) -> None:
        """手机页进入备份选择，应用库页安装当前选择，保留批次与勾选。"""
        if self.worker:
            return
        if self.mode == "library":
            self.begin_batch(self.selected_apps(), "install")
            return
        if self.mode != "phone":
            return
        self.batch_filter.setCurrentIndex(0)
        if self.nav.currentRow() == 1:
            self.load_view()
        else:
            self.nav.setCurrentRow(1)

    def retry_failed(self) -> None:
        """先展开失败列表供用户选择，不能把失败和未处理项目一起立即重试。"""
        self.failure_filter.setCurrentIndex(0)
        if self.failure_panel.isHidden():
            self.show_failure_details()

    def update_failure_button(self) -> None:
        """根据本次或恢复的记录显示失败与待处理数量，尚未执行不算安装失败。"""
        counts = Counter(app.status for app in self.failure_records)
        waiting = max(0, counts["pending"] - (1 if self.active_install else 0))
        pending_title = "排队" if self.active_install else "待处理"
        self.failure_button.setText(f"查看失败详情（失败 {counts['failed']} · {pending_title} {waiting}）"
                                    if counts['failed'] or counts['pending'] else "查看失败详情")
        self.failure_button.setEnabled(bool(counts['failed'] or counts['pending']))
        self.failure_filter.setItemText(0, f"失败项 · {counts['failed']} 个")
        self.failure_filter.setItemText(1, f"{'排队等待' if self.active_install else '尚未处理'} · {waiting} 个")
        self.retry.setVisible(False)
        for number, state in zip(self.stat_numbers, ("installed", "skipped", "failed", "pending")):
            number.setText(str(max(0, counts[state] - (1 if state == "pending" and self.active_install else 0))))
        self.pending_stat_title.setText("排队等待" if self.active_install else "尚未处理")
        if not self.failure_panel.isHidden():
            self.populate_failure_panel()
        if hasattr(self, "action"):
            self.apply_filter()

    def tick_install_spinner(self) -> None:
        """每帧仅更新当前安装状态图标与任务卡，动画不刷新列表或改变排序、滚动。"""
        if self.active_install is None:
            return
        self.spinner_frame = (self.spinner_frame + 1) % 12
        pixmap = install_spinner(self.spinner_frame)
        self.activity_spinner.setPixmap(pixmap)
        elapsed = max(0, int(time.monotonic() - self.install_started))
        self.activity_elapsed.setText(f"已用 {elapsed // 60:02d}:{elapsed % 60:02d}")
        if elapsed >= 90:
            self.activity_note.setText(f"{self.active_install.package} · 安装尚未返回结果，请留意手机上的安装确认；大应用可能耗时较长。")
        if self.mode != "library":
            return
        # 图标变化只重绘该单元格，不调用 apply_filter，也不重建任何行。
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            checkbox = self.table.item(row, 0)
            if checkbox.data(ACTIVE_ROLE):
                self.table.item(row, 4).setIcon(QIcon(pixmap))
                break
        self.table.blockSignals(False)

    def clear_install_activity(self) -> None:
        """安装返回结果、暂停或出错时停止动画，清除临时运行状态而不修改备份记录。"""
        if self.active_install is None:
            return
        self.active_install = None
        self.spinner_timer.stop()
        self.install_activity.hide()
        self.update_failure_button()

    def locate_active_install(self) -> None:
        """用户主动定位当前安装项才清除筛选并滚动，后台安装切换不抢占阅读位置。"""
        if self.active_install is None or self.mode != "library":
            return
        self.search.clear()
        self.filter.setCurrentIndex(0)
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(ACTIVE_ROLE):
                self.table.scrollToItem(self.table.item(row, 1), QAbstractItemView.ScrollHint.PositionAtCenter)
                break

    def show_failure_details(self) -> None:
        """在右侧展开或收起结果面板，不占用主线程模态循环，也不清空安装选择。"""
        opened = self.failure_panel.isHidden()
        self.failure_panel.setVisible(opened)
        self.summary_frame.setVisible(not opened)
        if opened:
            self.populate_failure_panel()
        self.update_summary()

    def populate_failure_panel(self) -> None:
        """失败与未处理分别展示，只有失败项可勾选重试，保留当前有效选择。"""
        status = "failed" if self.failure_filter.currentIndex() == 0 else "pending"
        apps = [app for app in self.failure_records if app.status == status
                and not (status == "pending" and self.active_install and app.key == self.active_install.key)]
        self.failure_selection.intersection_update(app.package for app in self.failure_records if app.status == "failed")
        snapshot = (status, tuple((app.package, app.name, app.status, app.error) for app in apps),
                    frozenset(self.failure_selection))
        if snapshot == self.failure_snapshot:
            return
        self.failure_snapshot = snapshot
        scroll = self.failure_table.verticalScrollBar().value()
        selected = self.failure_table.item(self.failure_table.currentRow(), 0)
        current_package = selected.data(Qt.ItemDataRole.UserRole) if selected else ""
        self.failure_table.blockSignals(True)
        self.failure_table.setRowCount(len(apps))
        for row, app in enumerate(apps):
            check = QTableWidgetItem()
            check.setData(Qt.ItemDataRole.UserRole, app.package)
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | (Qt.ItemFlag.ItemIsUserCheckable if status == "failed" else Qt.ItemFlag.NoItemFlags))
            check.setCheckState(Qt.CheckState.Checked if app.package in self.failure_selection else Qt.CheckState.Unchecked)
            reason = apk.explain_error(app.error).splitlines()[0] if app.error else "尚未执行，连接恢复后可继续。"
            item = QTableWidgetItem(f"{app.name or app.package}\n{app.package}\n{reason}")
            item.setToolTip(reason)
            self.failure_table.setItem(row, 0, check)
            self.failure_table.setItem(row, 1, item)
            self.failure_table.setRowHeight(row, 106)
        self.failure_table.blockSignals(False)
        self.failure_all.setVisible(status == "failed")
        self.failure_error.clear()
        if apps:
            row = next((index for index, app in enumerate(apps) if app.package == current_package), 0)
            self.failure_table.setCurrentCell(row, 1)
            self.show_failure_error(row, 1, -1, -1)
        self.failure_table.verticalScrollBar().setValue(scroll)
        self.update_summary()

    def failure_item_changed(self, item: QTableWidgetItem) -> None:
        """失败勾选独立于应用库选择，避免重试时混入未处理或成功项。"""
        if item.column() != 0:
            return
        package = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self.failure_selection.add(package)
        else:
            self.failure_selection.discard(package)
        self.update_summary()

    def select_all_failures(self, checked: bool) -> None:
        """全选仅处理当前失败项，未处理项始终由恢复队列入口操作。"""
        self.failure_selection = {app.package for app in self.failure_records if app.status == "failed"} if checked else set()
        self.populate_failure_panel()

    def show_failure_error(self, row: int, column: int, previous_row: int, previous_column: int) -> None:
        """随当前行展示完整错误；未处理项说明尚未执行，不伪造安装失败原因。"""
        if row < 0 or self.failure_table.item(row, 0) is None:
            return
        package = self.failure_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        app = next((item for item in self.failure_records if item.package == package), None)
        if app:
            reason = apk.explain_error(app.error) if app.error else "尚未执行；连接恢复后点击继续剩余应用。"
            self.failure_error.setPlainText(f"{app.name or app.package}\n{app.package}\n\n{reason}")

    def copy_failure_error(self) -> None:
        """复制当前错误文本，便于用户保留或反馈原始失败信息。"""
        QApplication.clipboard().setText(self.failure_error.toPlainText())

    def retry_selected_failures(self) -> None:
        """在原批次仅重试用户勾选的失败应用，保留其他失败和未处理状态。"""
        apps = [app for app in self.failure_records if app.status == "failed" and app.package in self.failure_selection]
        if apps:
            self.begin_batch(apps, apps[0].operation,
                             Path(self.queue_session) if self.queue_session and apps[0].operation == "install" else None)

    def continue_pending(self) -> None:
        """继续原设备上的未处理安装项，不包含失败和已完成应用。"""
        apps = [app for app in self.failure_records if app.status == "pending" and app.operation == "install"]
        if apps and self.queue_session:
            self.begin_batch(apps, "install", Path(self.queue_session))

    def stop_task(self) -> None:
        """请求在文件或应用事务完成后停止，避免强杀安装导致状态不明。"""
        if self.worker:
            self.pending_export = None
            self.worker.stop.set()
            self.stop_button.setEnabled(False)
            self.status.setText("正在完成当前操作，随后停止…")

    def update_phone_app(self, app: apk.App) -> None:
        """只替换对应手机行的资料，选择仍按包名保存，不重建表格或改变滚动位置。"""
        scroll = self.table.verticalScrollBar().value()
        self.phone_records = [app if item.package == app.package else item for item in self.phone_records]
        if self.mode != "phone":
            return
        if self.row_snapshots.get(app.key) == app:
            return
        self.records = list(self.phone_records)
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == app.package:
                self.append_row(app, row)
                break
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self.apply_filter()
        self.table.verticalScrollBar().setValue(scroll)

    def accept_export_result(self, backup: apk.App) -> None:
        """成功结果直接增量入索引，按包名取消选择，备份文件路径不写入手机对象。"""
        copies = self.export_index.setdefault(backup.package, [])
        if not any(item.files == backup.files for item in copies):
            copies.append(backup)
        self.selected["phone"].discard(backup.package)
        for current in self.phone_records:
            if current.package != backup.package:
                continue
            if (not backup.source or backup.source == self.source_serial) and (backup.version or backup.version_code):
                # 导出的实际版本比扫描快照更新，只回填资料字段，保持手机列表键为包名。
                actual = replace(current, version=backup.version, version_code=backup.version_code,
                                 name=backup.name if backup.name and backup.name != backup.package else current.name,
                                 icon=backup.icon or current.icon, paths=list(backup.paths), size=backup.size,
                                 status="ready", error="")
                self.update_phone_app(actual)
            break
        if self.mode == "phone":
            self.table.blockSignals(True)
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item.data(Qt.ItemDataRole.UserRole) == backup.package:
                    item.setCheckState(Qt.CheckState.Unchecked)
            self.table.blockSignals(False)
            self.apply_filter()

    def handle_event(self, event: dict) -> None:
        """处理清单、索引和逐项结果；后台确认已导出时同步清理隐藏选择。"""
        if event["kind"] == "batch_details":
            if Path(event["library"]).resolve() == self.library.resolve():
                self.startup_library_pending = False
                self.failure_records = event["apps"]
                self.queue_target = event.get("serial", "")
                self.queue_session = event.get("session", "")
                apk.LOG.info("Restored install queue session=%s target=%s apps=%d", self.queue_session, self.queue_target, len(self.failure_records))
                self.update_failure_button()
            return
        if event["kind"] == "batch":
            self.failure_records = event["apps"]
            self.queue_target = event["serial"]
            self.queue_session = event["session"]
            if self.job_kind == "install":
                self.selected["library"].clear()
                self.table.blockSignals(True)
                for row in range(self.table.rowCount()):
                    self.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
                self.table.blockSignals(False)
            self.update_failure_button()
            return
        if event["kind"] == "paused":
            self.clear_install_activity()
            self.status.setText("安装已暂停 · " + event["reason"])
            return
        if event["kind"] == "export_index":
            if Path(event["library"]).resolve() == self.library.resolve():
                self.export_index = event["index"]
                self.export_index_ready = True
                self.index_refresh_pending = False
                self.apply_filter()
            return
        completed, total = event["completed"], event["total"]
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(completed)
        if event["kind"] == "apps":
            self.phone_records = list(event["apps"])
            if self.mode == "phone":
                self.set_records(self.phone_records)
            self.status.setText(f"已发现 {total} 个应用，正在后台补充资料；现在可以勾选导出。")
            return
        app = event["app"]
        if event["kind"] == "app":
            self.update_phone_app(app)
            if not self.pending_export:
                self.status.setText(f"后台补充资料 · {completed}/{total} · {app.name} · 可随时勾选导出")
        else:
            if event["kind"] == "progress" and self.job_kind == "install":
                self.active_install = replace(app)
                self.install_started = time.monotonic()
                self.activity_icon.setPixmap(app_icon(app).pixmap(40, 40))
                self.activity_title.setText(f"正在安装 · {app.name or app.package}")
                self.activity_note.setText(f"{app.package} · {size_text(app.size)} · 已处理 {completed} / {total} 项 · 手机如弹出安装提示，请确认。")
                self.tick_install_spinner()
                self.spinner_timer.start()
                self.update_failure_button()
            if event["kind"] == "result":
                if self.active_install and self.active_install.package == app.package:
                    self.clear_install_activity()
                self.failure_records = [item for item in self.failure_records if item.package != app.package] + [replace(app)]
                self.update_failure_button()
            if event["kind"] == "result" and self.job_kind == "export" and app.status == "exported":
                self.accept_export_result(app)
            verb = "导出" if self.job_kind == "export" else "安装"
            self.status.setText(f"正在{verb} · {completed}/{total} · {app.name}")

    def job_done(self, result: object) -> None:
        """保存后台结果；任务统计按真实成功项计算，不把失败项计入完成。"""
        if self.job_kind == "devices":
            self.set_devices(result or [])
        elif self.job_kind in ("library", "history"):
            if self.job_kind == "library":
                self.set_library_records(result or [])
            else:
                self.set_records(result or [])
            self.status.setText("应用库已读取" if self.job_kind == "library" else "迁移记录已读取")
            self.retry.hide()
        elif self.job_kind == "scan":
            if result is not None:
                self.phone_records = result
            self.set_records(self.phone_records)
            ready = sum(app.status != "loading" for app in self.phone_records)
            self.status.setText(f"已发现 {len(self.phone_records)} 个用户应用，资料已读取 {ready} 个")
        elif self.job_kind in ("export", "install"):
            self.clear_install_activity()
            self.last_batch = result or []
            self.failure_records = list(self.last_batch)
            self.update_failure_button()
            if self.job_kind == "export":
                self.last_export = [app for app in self.last_batch if app.status == "exported"]
            counts = Counter(app.status for app in self.last_batch)
            self.status.setText(f"任务结束 · 成功 {counts['exported'] + counts['installed']} · "
                                f"跳过 {counts['skipped']} · 失败 {counts['failed']} · 待处理 {counts['pending']}")
            if self.job_kind == "install" and counts["pending"]:
                self.status.setText(f"安装已暂停 · 成功 {counts['installed']} · 跳过 {counts['skipped']} · 失败 {counts['failed']} · 尚未处理 {counts['pending']}")
            self.retry.hide()

    def job_error(self, message: str) -> None:
        """设备轮询错误显示就地提示，任务错误保留明确原因供用户排查。"""
        apk.LOG.error("Job error kind=%s error=%s", self.job_kind, message)
        self.clear_install_activity()
        self.status.setText(message)
        if self.job_kind == "devices":
            self.device_poll_error(message)

    def job_finished(self) -> None:
        """释放线程并恢复按钮；关闭窗口前必须等当前事务安全结束。"""
        kind = self.job_kind
        previous = self.worker
        self.worker = None
        if previous:
            previous.deleteLater()
        self.refresh.setEnabled(True)
        self.source.setEnabled(True)
        self.target.setEnabled(True)
        self.nav.setEnabled(True)
        self.table.setEnabled(True)
        self.stop_button.hide()
        self.stop_button.setEnabled(True)
        self.progress.hide()
        self.update_summary()
        if self.close_requested:
            self.close()
        elif self.pending_export is not None:
            apps = self.pending_export
            self.pending_export = None
            # 直接交接任务，避免设备轮询在下一个事件循环先占用工作线程。
            if apps:
                self.begin_batch(apps, "export")
            else:
                self.status.setText("所选项已取消或已经导出，本次无需重复导出。")
        elif self.index_refresh_pending:
            self.refresh_export_index()
        elif kind == "index":
            self.set_devices(self.devices)
            if self.mode == "phone" and self.serial() and self.source_serial != self.serial():
                QTimer.singleShot(0, self.scan_phone)
        elif kind == "devices" and self.mode == "phone" and self.serial() and self.source_serial != self.serial():
            QTimer.singleShot(0, self.scan_phone)

    def closeEvent(self, event: QCloseEvent) -> None:
        """传输时关闭窗口先请求停止，等待工作线程退出后才销毁界面。"""
        if self.worker or self.device_worker:
            self.close_requested = True
            self.timer.stop()
            if self.worker:
                self.stop_task()
            event.ignore()
        else:
            self.spinner_timer.stop()
            self.settings.sync()
            event.accept()


def log_unhandled_exception(kind, error, traceback) -> None:
    """把未捕获的界面回调异常写入日志，同时保留 Python 默认错误输出。"""
    apk.LOG.critical("Unhandled UI exception", exc_info=(kind, error, traceback))
    sys.__excepthook__(kind, error, traceback)


def main() -> None:
    """启动中文桌面工具；作为独立打包入口时同样不要求终端环境。"""
    apk.configure_logging()
    sys.excepthook = log_unhandled_exception
    application = QApplication.instance() or QApplication(sys.argv)
    application.setOrganizationName("HoardyADB")
    application.setApplicationName("安卓应用搬家")
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    window = Window()
    window.show()
    sys.exit(application.exec())


if __name__ == "__main__":
    main()

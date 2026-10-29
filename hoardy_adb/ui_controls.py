"""A 方案的轻量 Qt 交互控件；不访问手机，不持有传输业务状态。"""
from __future__ import annotations

from functools import lru_cache
import subprocess
import sys

from PySide6.QtCore import Qt, QEvent, Signal, QPoint, QRect, QRectF, QSize, Property, QPropertyAnimation, QEasingCurve, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QFontMetrics
from PySide6.QtWidgets import (
    QApplication, QPushButton, QComboBox, QFrame, QVBoxLayout, QLabel, QListView,
    QStyledItemDelegate, QGraphicsOpacityEffect, QWidget, QStyle,
)

DETAIL_ROLE = Qt.ItemDataRole.UserRole + 20
NAME_ROLE = Qt.ItemDataRole.UserRole + 22


@lru_cache(maxsize=1)
def motion_duration() -> int:
    """读取 Mac 减少动态效果偏好；每次启动只查一次，无法读取时使用 150ms。"""
    if sys.platform == "darwin":
        try:
            result = subprocess.run(["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
                                    capture_output=True, text=True, timeout=1, check=False)
            if result.stdout.strip().lower() in ("1", "true", "yes"):
                return 0
        except (OSError, subprocess.SubprocessError):
            pass
    return 150


class AppButton(QPushButton):
    """白底浅蓝按钮，动画只影响绘制，不缩放控件布局和点击区域。"""

    def __init__(self, text: str = "", parent=None):
        """保留 QPushButton 的信号与接口，统一添加悬停、按压和执行中反馈。"""
        super().__init__(text, parent)
        self._hover = 0.0
        self._press = 0.0
        self._frame = 0
        self.hover_animation = QPropertyAnimation(self, b"hoverAmount", self)
        self.press_animation = QPropertyAnimation(self, b"pressAmount", self)
        self.hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.press_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.loading_timer = QTimer(self)
        self.loading_timer.setInterval(80)
        self.loading_timer.timeout.connect(self.loading_tick)
        self.pressed.connect(self.press_feedback)
        self.released.connect(self.release_feedback)
        self.setMinimumHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def get_hover(self) -> float:
        """返回当前悬停强度，供 Qt 属性动画读取。"""
        return self._hover

    def set_hover(self, value: float) -> None:
        """仅重绘当前按钮，避免动效触发父页面刷新。"""
        self._hover = value
        self.update()

    hoverAmount = Property(float, get_hover, set_hover)

    def get_press(self) -> float:
        """返回按压强度，布局尺寸始终不变。"""
        return self._press

    def set_press(self, value: float) -> None:
        """只改变按钮内部绘制缩放。"""
        self._press = value
        self.update()

    pressAmount = Property(float, get_press, set_press)

    def animate(self, animation: QPropertyAnimation, target: float) -> None:
        """从当前值平滑过渡；减少动态效果时立即应用终值。"""
        animation.stop()
        name = bytes(animation.propertyName()).decode()
        animation.setStartValue(self.property(name))
        animation.setEndValue(target)
        animation.setDuration(min(motion_duration(), 110))
        animation.start()

    def enterEvent(self, event) -> None:
        """可用按钮进入时淡入浅蓝反馈。"""
        if self.isEnabled():
            self.animate(self.hover_animation, 1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """离开按钮后恢复背景，不改变任务状态。"""
        self.animate(self.hover_animation, 0.0)
        super().leaveEvent(event)

    def press_feedback(self) -> None:
        """鼠标或键盘按下时轻微压缩绘制内容。"""
        self.animate(self.press_animation, 1.0)

    def release_feedback(self) -> None:
        """释放后恢复原尺寸，不额外触发点击信号。"""
        self.animate(self.press_animation, 0.0)

    def setText(self, text: str) -> None:
        """沿用业务文字，仅为明确的执行中文字提供局部旋转提示。"""
        super().setText(text)
        if hasattr(self, "loading_timer"):
            if text.startswith("正在") and motion_duration():
                self.loading_timer.start()
            else:
                self.loading_timer.stop()

    def loading_tick(self) -> None:
        """只更新正在执行按钮的小图标。"""
        self._frame = (self._frame + 1) % 12
        self.update()

    def changeEvent(self, event) -> None:
        """禁用时立即取消按压，避免业务切换状态后残留缩放。"""
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self.hover_animation.stop()
            self.press_animation.stop()
            self._hover = self._press = 0.0
        super().changeEvent(event)

    def paintEvent(self, event) -> None:
        """绘制圆角、焦点和文字；执行中与普通禁用状态保持可辨识。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        primary = self.objectName() == "primary"
        secondary = self.objectName() == "secondary"
        enabled = self.isEnabled()
        loading = self.text().startswith("正在")
        if not enabled:
            background = QColor("#b8d7fc" if primary else "#f3f5f8")
            border = QColor("#b8d7fc" if primary else "#e4e9f1")
            foreground = QColor("white" if primary else "#a1acbc")
        else:
            start, end = (QColor("#087cff"), QColor("#0069df")) if primary else (QColor("white"), QColor("#edf5ff"))
            background = QColor.fromRgbF(start.redF() + (end.redF()-start.redF()) * self._hover,
                                        start.greenF() + (end.greenF()-start.greenF()) * self._hover,
                                        start.blueF() + (end.blueF()-start.blueF()) * self._hover)
            border = QColor("#087cff" if primary else "#a6cafa" if self._hover > .1 else "#dbe3ee")
            foreground = QColor("white" if primary else "#087cff" if secondary else "#29384c")
        if self.hasFocus() and enabled:
            painter.setPen(QPen(QColor("#b5d5ff"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 11, 11)
        painter.translate(self.width()/2, self.height()/2)
        scale = 1.0 - .015 * self._press
        painter.scale(scale, scale)
        painter.translate(-self.width()/2, -self.height()/2)
        area = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(area, 10, 10)
        font = self.font()
        if primary:
            font.setBold(True)
        painter.setFont(font)
        painter.setPen(foreground)
        metrics = QFontMetrics(font)
        text = metrics.elidedText(self.text().replace("&", ""), Qt.TextElideMode.ElideRight, max(0, self.width()-28-(22 if loading else 0)))
        text_width = metrics.horizontalAdvance(text)
        left = (self.width()-text_width-(22 if loading else 0))/2
        if loading:
            center = QPoint(int(left+7), self.height()//2)
            painter.setPen(QPen(foreground, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(QRectF(center.x()-6, center.y()-6, 12, 12), -self._frame*30*16, 255*16)
            left += 22
        painter.setPen(foreground)
        painter.drawText(QRectF(left, 0, text_width+1, self.height()), Qt.AlignmentFlag.AlignVCenter, text)


class MenuDelegate(QStyledItemDelegate):
    """统一绘制下拉选项、已选对勾、设备图标与状态提示。"""

    def __init__(self, combo, parent):
        """复用原组合框模型，不复制设备身份或业务选择。"""
        super().__init__(parent)
        self.combo = combo

    def sizeHint(self, option, index) -> QSize:
        """按实际宽度计算长批次名称高度，避免截断或横向滚动。"""
        width = max(150, self.parent().viewport().width()-40-(48 if self.combo.device_mode else 0))
        text = index.data(NAME_ROLE) or index.data() or ""
        bounds = QFontMetrics(self.combo.font()).boundingRect(QRect(0, 0, width, 500), Qt.TextFlag.TextWrapAnywhere, text)
        detail = index.data(DETAIL_ROLE)
        return QSize(200, max(48, bounds.height()+22+(19 if detail else 0)))

    def paint(self, painter, option, index) -> None:
        """选中项浅蓝加对勾，不可用设备保留说明并降低对比度。"""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
        selected = index.row() == self.combo.currentIndex()
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver) or index == self.parent().currentIndex()
        box = QRectF(option.rect).adjusted(3, 2, -3, -2)
        if selected or (hovered and enabled):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#eaf3ff" if selected else "#f4f8fd"))
            painter.drawRoundedRect(box, 7, 7)
        left = option.rect.left()+13
        if self.combo.device_mode and index.data(Qt.ItemDataRole.UserRole):
            phone = QRectF(left, option.rect.center().y()-14, 26, 30)
            painter.setBrush(QColor("#f0f5fc"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(phone, 6, 6)
            painter.setPen(QPen(QColor("#709bd1" if enabled else "#bac8da"), 1.3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(phone.adjusted(7, 5, -7, -5), 2, 2)
            painter.drawLine(QPoint(int(phone.center().x()-1), int(phone.bottom()-8)), QPoint(int(phone.center().x()+1), int(phone.bottom()-8)))
            left += 36
        width = option.rect.right()-left-28
        main = index.data(NAME_ROLE) or index.data() or ""
        detail = index.data(DETAIL_ROLE) or ""
        painter.setFont(self.combo.font())
        painter.setPen(QColor("#a7b1bf" if not enabled else "#087cff" if selected else "#34445b"))
        text_area = QRect(left, option.rect.top()+10, max(20, width), option.rect.height()-18-(18 if detail else 0))
        painter.drawText(text_area, Qt.TextFlag.TextWrapAnywhere | Qt.AlignmentFlag.AlignVCenter, main)
        if detail:
            small = self.combo.font()
            small.setPointSizeF(max(9, small.pointSizeF()-2))
            painter.setFont(small)
            painter.setPen(QColor("#9aa6b7"))
            painter.drawText(QRect(left, option.rect.bottom()-24, max(20, width), 18), Qt.AlignmentFlag.AlignVCenter, detail)
        if selected:
            x, y = option.rect.right()-18, option.rect.center().y()
            painter.setPen(QPen(QColor("#087cff" if enabled else "#a8b8cd"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(x-5, y, x-1, y+4)
            painter.drawLine(x-1, y+4, x+6, y-5)
        painter.restore()


class ComboPopup(QFrame):
    """主窗口内浮层：统一圆角阴影，不使用平台原生下拉窗口。"""

    def __init__(self, combo):
        """浮层复用原模型与启用标志，并把所有输入留在原窗口中。"""
        super().__init__(combo.window())
        self.combo = combo
        self.setObjectName("comboPopupShell")
        self.setStyleSheet("QFrame#comboPopupShell {background:transparent;border:0;}")
        self.opacity = QGraphicsOpacityEffect(self)
        self.opacity.setOpacity(1.0)
        self.setGraphicsEffect(self.opacity)
        self.fade = QPropertyAnimation(self.opacity, b"opacity", self)
        self.panel = QFrame(self)
        self.panel.setObjectName("comboPopupCard")
        self.panel.setStyleSheet("QFrame#comboPopupCard {background:white;border:1px solid #e2e8f1;border-radius:10px;} QLabel {background:transparent;border:0;} QListView {background:transparent;border:0;outline:none;} QPushButton {font-size:13px;}")
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(8, 12, 8, 9)
        layout.setSpacing(5)
        self.title = QLabel(combo.menu_title or "请选择")
        self.title.setStyleSheet("font-weight:600;color:#30415b;padding:0 7px;")
        layout.addWidget(self.title)
        self.subtitle = QLabel()
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet("color:#8b9bb0;font-size:11px;padding:0 7px 5px;")
        layout.addWidget(self.subtitle)
        self.list = QListView()
        self.list.setModel(combo.model())
        self.list.setItemDelegate(MenuDelegate(combo, self.list))
        self.list.setMouseTracking(True)
        self.list.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.list.clicked.connect(self.choose)
        self.list.installEventFilter(self)
        layout.addWidget(self.list)
        self.empty = QLabel("未检测到可用设备\n连接手机并允许 USB 调试后，再重新检测。")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet("padding:18px 10px;color:#8a9bb2;font-size:12px;")
        layout.addWidget(self.empty)
        self.refresh = AppButton("重新检测设备")
        self.refresh.clicked.connect(combo.refreshRequested)
        layout.addWidget(self.refresh)
        self.animation = QPropertyAnimation(self, b"pos", self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        combo.model().rowsInserted.connect(self.model_changed)
        combo.model().rowsRemoved.connect(self.model_changed)
        combo.model().dataChanged.connect(self.model_changed)
        self.hide()

    def paintEvent(self, event) -> None:
        """直接绘制轻阴影，避免嵌套图形效果在 Mac 上与淡入效果争用画布。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        panel = QRectF(self.panel.geometry()).translated(0, 2)
        for spread in range(8, 0, -1):
            painter.setBrush(QColor(35, 55, 85, 3))
            painter.drawRoundedRect(panel.adjusted(-spread, -spread, spread, spread), 10+spread, 10+spread)

    def model_changed(self, *args) -> None:
        """后台设备变更只更新浮层内容和尺寸，不重置角色或关闭菜单。"""
        if self.isVisible():
            self.prepare()
            self.place(False)

    def prepare(self) -> None:
        """根据真实模型显示空设备说明，保留已知但不可用的设备选项。"""
        empty = self.combo.device_mode and self.combo.count() <= 1
        self.list.setVisible(not empty)
        self.empty.setVisible(empty)
        self.refresh.setVisible(self.combo.device_mode)
        self.subtitle.setText("仅选择已连接且已授权的设备；来源和目标不能相同。" if self.combo.device_mode else "方向键选择，Enter 确认，Escape 收起。")
        self.title.setText(self.combo.menu_title or "请选择")

    def place(self, animated: bool = True) -> None:
        """依据主窗口可用空间决定向上或向下展开，几何计算不使用动画中的位置。"""
        window = self.combo.window()
        limits = window.rect().adjusted(8, 8, -8, -8)
        anchor = self.combo.mapTo(window, QPoint(0, 0))
        width = min(limits.width(), max(self.combo.width()+20, 328 if self.combo.device_mode else 300))
        if self.combo.menu_title == "选择导出批次":
            width = min(limits.width(), max(width, 470))
        self.panel.setGeometry(10, 10, width-20, 300)
        self.list.setFixedWidth(max(100, width-38))
        self.list.doItemsLayout()
        rows_height = sum(self.list.sizeHintForRow(row) for row in range(self.combo.count()))
        list_height = 0 if self.combo.device_mode and self.combo.count() <= 1 else min(300, max(44, rows_height))
        self.list.setFixedHeight(list_height)
        self.panel.layout().activate()
        height = min(limits.height(), self.panel.layout().sizeHint().height()+20)
        below = limits.bottom()-(anchor.y()+self.combo.height())
        above = anchor.y()-limits.top()
        upwards = below < height and above > below
        available = above if upwards else below
        if height > available and available >= 170:
            self.list.setFixedHeight(max(44, list_height-(height-available)))
            self.panel.layout().activate()
            height = min(available, self.panel.layout().sizeHint().height()+20)
        x = max(limits.left(), min(anchor.x()-10, limits.right()-width+1))
        y = anchor.y()-height-3 if upwards else anchor.y()+self.combo.height()+3
        y = max(limits.top(), min(y, limits.bottom()-height+1))
        self.resize(width, height)
        self.panel.setGeometry(10, 10, width-20, height-20)
        self.animation.stop()
        target = QPoint(x, y)
        self.animation.setStartValue(target+QPoint(0, 5 if upwards else -5))
        self.animation.setEndValue(target)
        self.animation.setDuration(motion_duration() if animated else 0)
        self.animation.start()
        self.fade.stop()
        self.fade.setStartValue(0.0 if animated and motion_duration() else 1.0)
        self.fade.setEndValue(1.0)
        self.fade.setDuration(motion_duration() if animated else 0)
        self.fade.start()

    def choose(self, index) -> None:
        """只有可用项允许提交，当前角色和设备身份直接来自原组合框模型。"""
        if index.isValid() and index.flags() & Qt.ItemFlag.ItemIsEnabled:
            row = index.row()
            self.combo.hidePopup()
            self.combo.setCurrentIndex(row)
            self.combo.setFocus(Qt.FocusReason.OtherFocusReason)

    def next_row(self, delta: int, edge: bool = False) -> None:
        """键盘跳过离线、待授权与角色冲突选项。"""
        count = self.combo.count()
        if not count:
            return
        row = (-1 if delta > 0 else count) if edge else self.list.currentIndex().row()
        for _ in range(count):
            row = (row+delta+count) % count
            index = self.combo.model().index(row, 0)
            if index.flags() & Qt.ItemFlag.ItemIsEnabled:
                self.list.setCurrentIndex(index)
                self.list.scrollTo(index)
                return

    def eventFilter(self, watched, event) -> bool:
        """菜单键盘与外部点击统一处理；外部事件继续交给原目标控件。"""
        if not self.isVisible():
            return False
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
            if watched is not self and not self.isAncestorOf(watched) and watched is not self.combo:
                self.combo.hidePopup()
        elif event.type() == QEvent.Type.KeyPress and (watched is self.list or self.isAncestorOf(watched)):
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Home, Qt.Key.Key_End):
                self.next_row(1 if key in (Qt.Key.Key_Down, Qt.Key.Key_Home) else -1, key in (Qt.Key.Key_Home, Qt.Key.Key_End))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                self.choose(self.list.currentIndex())
                return True
            if key == Qt.Key.Key_Escape:
                self.combo.hidePopup()
                self.combo.setFocus()
                return True
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                self.combo.hidePopup()
                self.combo.setFocus()
                self.combo.focusNextPrevChild(key == Qt.Key.Key_Tab)
                return True
        elif watched is self.combo.window():
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
                self.place(False)
            elif event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.Hide):
                self.combo.hidePopup()
        return False


class AppComboBox(QComboBox):
    """沿用 QComboBox 模型与信号，提供 A 方案的自绘按钮和浮层菜单。"""
    refreshRequested = Signal()
    opened = None

    def __init__(self, parent=None, *, device_mode: bool = False, menu_title: str = ""):
        """仅增加交互表现，实际设备过滤和任务选择继续由窗口负责。"""
        super().__init__(parent)
        self.device_mode = device_mode
        self.menu_title = menu_title
        self.popup = None
        self._angle = 0.0
        self.arrow_animation = QPropertyAnimation(self, b"arrowAngle", self)
        self.arrow_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setMinimumHeight(42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.currentIndexChanged.connect(self.selection_changed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def get_angle(self) -> float:
        """返回箭头绘制角度。"""
        return self._angle

    def set_angle(self, value: float) -> None:
        """箭头变化只重绘当前组合框。"""
        self._angle = value
        self.update()

    arrowAngle = Property(float, get_angle, set_angle)

    def selection_changed(self, index: int) -> None:
        """截断显示的长批次名称仍可通过悬停提示完整读取。"""
        self.setToolTip(self.currentText())
        self.update()

    def turn_arrow(self, target: float) -> None:
        """打开与关闭时使用同一 150ms 箭头旋转，快速重复操作可中途反向。"""
        self.arrow_animation.stop()
        self.arrow_animation.setStartValue(self._angle)
        self.arrow_animation.setEndValue(target)
        self.arrow_animation.setDuration(motion_duration())
        self.arrow_animation.start()

    def showPopup(self) -> None:
        """只显示一个浮层，空设备状态不再出现原生单行占位框。"""
        if not self.isEnabled():
            return
        if AppComboBox.opened and AppComboBox.opened is not self:
            AppComboBox.opened.hidePopup()
        if self.popup is None:
            self.popup = ComboPopup(self)
        AppComboBox.opened = self
        self.popup.prepare()
        self.popup.show()
        self.popup.raise_()
        self.popup.place()
        self.popup.list.setCurrentIndex(self.model().index(self.currentIndex(), 0))
        if self.popup.list.isVisible():
            self.popup.list.setFocus(Qt.FocusReason.PopupFocusReason)
        else:
            self.popup.refresh.setFocus(Qt.FocusReason.PopupFocusReason)
        QApplication.instance().installEventFilter(self.popup)
        self.turn_arrow(180.0)

    def hidePopup(self) -> None:
        """收起自绘浮层并移除全局过滤器，不重置原有选择。"""
        if self.popup:
            self.popup.animation.stop()
            self.popup.fade.stop()
            self.popup.hide()
            QApplication.instance().removeEventFilter(self.popup)
        if AppComboBox.opened is self:
            AppComboBox.opened = None
        self.turn_arrow(0.0)

    def mousePressEvent(self, event) -> None:
        """点击仅切换菜单，防止原生弹出层与自绘浮层同时出现。"""
        if event.button() == Qt.MouseButton.LeftButton:
            if self.popup and self.popup.isVisible():
                self.hidePopup()
            else:
                self.showPopup()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        """通过方向键、回车或空格进入菜单，避免关闭状态直接误改设备角色。"""
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.showPopup()
            if event.key() == Qt.Key.Key_Up:
                self.popup.next_row(-1, True)
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event) -> None:
        """进入下拉入口时局部重绘浅蓝边框，不改变焦点与选择。"""
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """离开入口恢复边框，已经展开的菜单保持可操作。"""
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        """滚动页面时不能顺带改变所选设备或批次。"""
        event.ignore()

    def changeEvent(self, event) -> None:
        """开始任务禁用控件时立即收起菜单，避免菜单仍可提交旧选项。"""
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self.hidePopup()
        super().changeEvent(event)

    def hideEvent(self, event) -> None:
        """切页隐藏触发器时一并收起浮层。"""
        self.hidePopup()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        """绘制统一的 10px 圆角与旋转箭头，长文字在触发器内省略。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        active = self.popup is not None and self.popup.isVisible()
        painter.setBrush(QColor("white" if enabled else "#f4f6f9"))
        painter.setPen(QPen(QColor("#087cff" if active or self.hasFocus() else "#a6ccfc" if enabled and self.underMouse() else "#dce4ef"), 1.3 if active else 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)
        painter.setFont(self.font())
        painter.setPen(QColor("#35455c" if enabled else "#a6b1c0"))
        text = QFontMetrics(self.font()).elidedText(self.currentText(), Qt.TextElideMode.ElideRight, max(0, self.width()-44))
        painter.drawText(self.rect().adjusted(12, 0, -30, 0), Qt.AlignmentFlag.AlignVCenter, text)
        painter.translate(self.width()-18, self.height()/2)
        painter.rotate(self._angle)
        painter.setPen(QPen(QColor("#087cff" if active else "#8392a8"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPoint(-4, -2), QPoint(0, 2))
        painter.drawLine(QPoint(0, 2), QPoint(4, -2))

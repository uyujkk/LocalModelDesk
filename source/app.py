"""Local Model Desk 0.8 — Python / PySide6 desktop shell."""
from __future__ import annotations

from i18n import set_language, tr

import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import time
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QLocale, Signal, Slot, Qt, QSize
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPalette
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from gpu_power import query_gpus, set_power_limit, watts_value
from controller import Controller, CONTEXTS, DEFAULT_PROFILE, QUANTS, Profile, running_profile, loaded

VERSION = "0.8"
APP_NAME = "Local Model Desk"
ASSETS = Path(__file__).resolve().parent / "assets"


def symbol(name: str, color: str = "#65758b", size: int = 20) -> QIcon:
    glyphs = {"home": "\ue80f", "logs": "\ue9d9", "settings": "\ue713", "refresh": "\ue72c",
              "play": "\ue768", "power": "\ue7e8", "code": "\ue943", "chip": "\ue950",
              "close": "\ue711", "folder": "\ue8b7", "copy": "\ue8c8", "save": "\ue74e"}
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont("Segoe MDL2 Assets")
    font.setPixelSize(int(size * 1.5))
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, glyphs[name])
    painter.end()
    return QIcon(pixmap)


def make_app_icon() -> QIcon:
    existing = ASSETS / "QwenDesk.ico"
    if existing.is_file():
        return QIcon(str(existing))
    pixmap = QPixmap(256, 256)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#0078d4"))
    painter.drawRoundedRect(8, 8, 240, 240, 54, 54)
    painter.setPen(QPen(QColor("white"), 16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(60, 53, 128, 128)
    painter.drawLine(159, 157, 204, 202)
    painter.setPen(QPen(QColor("#a5e6ff"), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(54, 211, 106, 211)
    painter.end()
    return QIcon(pixmap)


class Signals(QObject):
    result = Signal(object)
    error = Signal(str)
    log = Signal(str)


class Task(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.signals = Signals()
        self.function = function

    @Slot()
    def run(self):
        try:
            self.signals.result.emit(self.function(self.signals.log.emit))
        except Exception as exc:
            self.signals.error.emit(str(exc))


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    item = QLabel(text)
    if name:
        item.setObjectName(name)
    item.setWordWrap(wrap)
    return item


def button(text: str, kind: str = "secondary") -> QPushButton:
    item = QPushButton(text)
    item.setProperty("kind", kind)
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    item.setMinimumHeight(38)
    return item


class Card(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(23, 22, 23, 22)
        self.box.setSpacing(16)


class Window(QMainWindow):
    def __init__(self, state_dir: Path, test: bool = False):
        super().__init__()
        self.state_dir = state_dir
        self.test = test
        self.settings = self.read_settings()
        language = self.settings.get("language", "zh" if test else "system")
        if language == "system":
            language = "zh" if QLocale.system().name().startswith("zh") else "en"
        set_language(language)
        self.follow_running = "profile" not in self.settings
        self.last_status = {}
        self.busy = False
        self.checking = False
        self.power_busy = False
        self.dark = False
        self.tasks: set[Task] = set()
        self.pool = QThreadPool(self)
        self.last_message = tr('就绪。选择启动，开始本地工作。')
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_app_icon())
        self.resize(1100, 810)
        self.setMinimumSize(980, 720)
        self.build_ui()
        self.apply_theme()
        QApplication.styleHints().colorSchemeChanged.connect(lambda _: self.apply_theme())
        self.timer = QTimer(self)
        self.timer.setInterval(8000)
        self.timer.timeout.connect(self.refresh)
        self.log(tr('Local Model Desk {0} 已就绪。', VERSION))
        self.render_status({"health": None, "desktop_count": 0})
        if not test:
            self.timer.start()
            QTimer.singleShot(150, self.refresh)

    def read_settings(self):
        try:
            data = json.loads((self.state_dir / "settings.json").read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {"root": r"E:\Strata-Qwen-Test", "theme": "system"}

    def save_settings(self):
        self.settings.update(root=self.root.text().strip(), theme=self.theme.currentData())
        profile = self.selected_profile()
        self.settings["profile"] = {"quant": profile.quant, "context": profile.context}
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            (self.state_dir / "settings.json").write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            self.log(tr('设置未能保存：{0}', exc))

    def build_ui(self):
        shell = QWidget()
        shell.setObjectName("shell")
        self.setCentralWidget(shell)
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(188)
        rail = QVBoxLayout(self.sidebar)
        rail.setContentsMargins(14, 27, 14, 20)
        brand = QHBoxLayout()
        image = QLabel()
        image.setPixmap(make_app_icon().pixmap(34, 34))
        brand.addWidget(image)
        brand.addWidget(label("Local Model\nDesk", "brand"))
        brand.addStretch()
        rail.addLayout(brand)
        rail.addSpacing(35)
        rail.addWidget(label(tr('工作空间'), "railCaption"))
        rail.addSpacing(8)
        self.nav = []
        for title, glyph in [(tr('工作台'), "home"), (tr('监控仪表盘'), "logs"), (tr('运行日志'), "logs"), (tr('设置'), "settings"), (tr('显卡功耗'), "chip")]:
            item = button(title, "nav")
            item.setMinimumHeight(44)
            item.setProperty("glyph", glyph)
            index = len(self.nav)
            item.clicked.connect(lambda checked=False, i=index: self.navigate(i))
            self.nav.append(item)
            rail.addWidget(item)
        rail.addStretch()
        self.rail_state = label(tr('●  本地服务未连接'), "railStatus", True)
        rail.addWidget(self.rail_state)
        rail.addSpacing(12)
        rail.addWidget(label(tr('Local Model Desk {0}\n本地模型控制台', VERSION), "railCaption"))
        layout.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.make_dashboard()
        from monitor_ui import MonitorPage
        self.monitor = MonitorPage(self.monitor_submit, self.test)
        self.stack.addWidget(self.monitor)
        self.make_logs()
        self.make_settings()
        self.make_power_page()
        self.navigate(0)

    def page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("page")
        box = QVBoxLayout(content)
        box.setContentsMargins(30, 27, 30, 22)
        box.setSpacing(18)
        scroll.setWidget(content)
        self.stack.addWidget(scroll)
        return content, box

    def monitor_submit(self, function, on_result, on_error):
        task = Task(function)
        self.tasks.add(task)
        def complete(value):
            self.tasks.discard(task)
            on_result(value)
        def failed(error):
            self.tasks.discard(task)
            on_error(error)
        task.signals.result.connect(complete)
        task.signals.error.connect(failed)
        self.pool.start(task)

    def make_dashboard(self):
        _, box = self.page()
        header = QHBoxLayout()
        head = QVBoxLayout()
        head.setSpacing(6)
        head.addWidget(label(tr('本地工作台'), "pageTitle"))
        head.addWidget(label(tr('模型、代码助手与运行状态，一处管理。'), "muted"))
        header.addLayout(head)
        header.addStretch()
        self.refresh_button = button(tr('刷新'), "subtle")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignTop)
        box.addLayout(header)

        model = Card()
        model.setObjectName("modelCard")
        model.box.setContentsMargins(25, 25, 25, 24)
        row = QHBoxLayout()
        self.model_icon = label("", "iconTile")
        self.model_icon.setFixedSize(48, 48)
        row.addWidget(self.model_icon)
        names = QVBoxLayout()
        names.setSpacing(4)
        names.addWidget(label("Qwen Flash Next", "modelTitle"))
        self.active_model = label(tr('当前服务未连接'), "muted", True)
        self.active_model.setTextFormat(Qt.TextFormat.PlainText)
        names.addWidget(self.active_model)
        row.addLayout(names)
        row.addStretch()
        self.model_badge = label(tr('未连接'), "badge")
        self.model_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.model_badge, 0, Qt.AlignmentFlag.AlignTop)
        model.box.addLayout(row)
        options = QHBoxLayout()
        self.quant = QComboBox()
        self.quant.addItems(QUANTS)
        self.quant.setAccessibleName(tr('模型量化'))
        self.context_pick = QComboBox()
        for value in CONTEXTS:
            self.context_pick.addItem(f"{value // 1024}K", value)
        self.context_pick.setAccessibleName(tr('上下文长度'))
        options.addWidget(label(tr('量化'), "muted"))
        options.addWidget(self.quant, 1)
        options.addWidget(label(tr('上下文'), "muted"))
        options.addWidget(self.context_pick, 1)
        model.box.addLayout(options)
        saved = self.settings.get("profile")
        saved = saved if isinstance(saved, dict) else {}
        quant, context = saved.get("quant"), saved.get("context")
        profile = Profile(quant, context) if quant in QUANTS and context in CONTEXTS else DEFAULT_PROFILE
        self.apply_profile(profile)
        self.quant.currentIndexChanged.connect(self.quant_changed)
        self.context_pick.currentIndexChanged.connect(self.profile_changed)
        model.box.addSpacing(7)
        stats = QHBoxLayout()
        self.context_label = label(tr('上下文预设'), "muted")
        self.context_value = label("256K", "statValue")
        for heading, value in [(self.context_label, self.context_value),
                               (label(tr('磁盘剩余'), "muted"), self.make_disk_label()),
                               (label(tr('OpenCode 压缩阈值'), "muted"), self.make_threshold_label())]:
            cell = QVBoxLayout()
            cell.setSpacing(7)
            cell.addWidget(heading)
            cell.addWidget(value)
            stats.addLayout(cell, 1)
        model.box.addLayout(stats)
        line = QFrame()
        line.setObjectName("divider")
        line.setFixedHeight(1)
        model.box.addWidget(line)
        actions = QHBoxLayout()
        self.start_button = button(tr('启动模型并打开 OpenCode'), "primary")
        self.start_button.setMinimumHeight(44)
        self.start_button.clicked.connect(self.start)
        actions.addWidget(self.start_button)
        self.plan_button = button(tr('下载预检'))
        self.plan_button.clicked.connect(lambda: self.operation("plan"))
        actions.addWidget(self.plan_button)
        actions.addStretch()
        model.box.addLayout(actions)
        self.model_hint = label(tr('模型已卸载时会自动重新加载，不需要再次下载。'), "muted", True)
        model.box.addWidget(self.model_hint)
        box.addWidget(model)

        row = QHBoxLayout()
        row.setSpacing(16)
        desktop = Card()
        desktop.box.addWidget(label(tr('代码助手'), "eyebrow"))
        desktop.box.addWidget(label("OpenCode Desktop", "cardTitle"))
        self.desktop_status = label(tr('未运行'), "muted", True)
        desktop.box.addWidget(self.desktop_status)
        desktop.box.addStretch()
        self.close_button = button(tr('关闭 OpenCode'))
        self.close_button.clicked.connect(lambda: self.close_desktop(False))
        desktop.box.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignLeft)
        row.addWidget(desktop, 1)
        memory = Card()
        memory.box.addWidget(label(tr('模型资源'), "eyebrow"))
        memory.box.addWidget(label(tr('用完即可释放'), "cardTitle"))
        memory.box.addWidget(label(tr('停止模型服务，保留磁盘上的模型文件。'), "muted", True))
        memory.box.addStretch()
        self.unload_button = button(tr('卸载模型'))
        self.unload_button.clicked.connect(self.unload)
        memory.box.addWidget(self.unload_button, 0, Qt.AlignmentFlag.AlignLeft)
        row.addWidget(memory, 1)
        box.addLayout(row)

        footer = Card()
        footer.box.setContentsMargins(19, 15, 19, 15)
        flow = QHBoxLayout()
        words = QVBoxLayout()
        words.addWidget(label(tr('结束本次工作'), "cardSubtitle"))
        words.addWidget(label(tr('先退出代码助手，再卸载模型。'), "muted"))
        flow.addLayout(words, 1)
        self.all_button = button(tr('全部关闭'))
        self.all_button.clicked.connect(self.close_all)
        flow.addWidget(self.all_button)
        footer.box.addLayout(flow)
        box.addWidget(footer)
        box.addStretch()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        box.addWidget(self.progress)
        status_line = QHBoxLayout()
        self.notice = label(self.last_message, "muted", True)
        status_line.addWidget(self.notice, 1)
        details = button(tr('查看日志'), "link")
        details.clicked.connect(lambda: self.navigate(2))
        status_line.addWidget(details)
        box.addLayout(status_line)

    def make_logs(self):
        _, box = self.page()
        box.addWidget(label(tr('运行日志'), "pageTitle"))
        box.addWidget(label(tr('模型启动、停止和配置更新的实际输出。'), "muted"))
        toolbar = QHBoxLayout()
        copy = button(tr('复制日志'))
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.logs.toPlainText()))
        save = button(tr('保存日志…'))
        save.clicked.connect(self.save_log)
        clear = button(tr('清空显示'), "subtle")
        clear.clicked.connect(lambda: self.logs.clear())
        toolbar.addWidget(copy)
        toolbar.addWidget(save)
        toolbar.addStretch()
        toolbar.addWidget(clear)
        box.addLayout(toolbar)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(3000)
        self.logs.setObjectName("logs")
        self.logs.setMinimumHeight(430)
        self.logs.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        box.addWidget(self.logs, 1)

    def make_settings(self):
        _, box = self.page()
        box.addWidget(label(tr('设置'), "pageTitle"))
        box.addWidget(label(tr('安装位置和界面外观。'), "muted"))
        install = Card()
        install.box.addWidget(label(tr('Strata 安装位置'), "cardTitle"))
        install.box.addWidget(label(tr('选择包含 agent、models 和 terminal 的根目录。'), "muted", True))
        line = QHBoxLayout()
        self.root = QLineEdit(str(self.settings.get("root", r"E:\Strata-Qwen-Test")))
        self.root.setMinimumHeight(40)
        self.root.setAccessibleName(tr('Strata 安装根目录'))
        self.browse_button = button(tr('浏览…'))
        self.browse_button.clicked.connect(self.browse)
        line.addWidget(self.root, 1)
        line.addWidget(self.browse_button)
        install.box.addLayout(line)
        self.save_button = button(tr('保存安装位置'), "primary")
        self.save_button.clicked.connect(self.save_root)
        install.box.addWidget(self.save_button, 0, Qt.AlignmentFlag.AlignLeft)
        box.addWidget(install)
        appearance = Card()
        line = QHBoxLayout()
        words = QVBoxLayout()
        words.addWidget(label(tr('外观'), "cardTitle"))
        words.addWidget(label(tr('跟随 Windows，或选择喜欢的明暗主题。'), "muted", True))
        line.addLayout(words, 1)
        self.theme = QComboBox()
        self.theme.addItem(tr('跟随系统'), "system")
        self.theme.addItem(tr('浅色'), "light")
        self.theme.addItem(tr('深色'), "dark")
        index = self.theme.findData(self.settings.get("theme", "system"))
        self.theme.setCurrentIndex(max(index, 0))
        self.theme.setMinimumWidth(135)
        self.theme.setMinimumHeight(38)
        self.theme.currentIndexChanged.connect(self.theme_changed)
        line.addWidget(self.theme)
        appearance.box.addLayout(line)
        box.addWidget(appearance)
        language_card = Card()
        language_card.box.addWidget(label(tr("界面语言"), "cardTitle"))
        language_card.box.addWidget(label(tr("保存后重新打开应用，语言设置即可生效。"), "muted", True))
        language_row = QHBoxLayout()
        self.language_choice = QComboBox()
        self.language_choice.addItem(tr("跟随系统"), "system")
        self.language_choice.addItem("简体中文", "zh")
        self.language_choice.addItem("English", "en")
        index = self.language_choice.findData(self.settings.get("language", "system"))
        self.language_choice.setCurrentIndex(max(index, 0))
        self.language_choice.setMinimumHeight(38)
        language_row.addWidget(self.language_choice, 1)
        save_language = button(tr("保存语言"))
        save_language.clicked.connect(self.save_language)
        language_row.addWidget(save_language)
        language_card.box.addLayout(language_row)
        box.addWidget(language_card)
        about = Card()
        about.box.addWidget(label(tr('使用说明'), "cardTitle"))
        about.box.addWidget(label(tr('支持双卡 IQ2_XS 和 IQ3_XXS。关闭控制台后，已运行的模型和 OpenCode 会继续工作。'), "muted", True))
        about.box.addWidget(label(tr('停止模型会中断生成；强制退出 OpenCode 前会再次确认。'), "muted", True))
        box.addWidget(about)
        box.addStretch()

    def save_language(self):
        self.settings["language"] = self.language_choice.currentData()
        self.save_settings()
        self.confirm(tr("界面语言"), tr("保存后重新打开应用，语言设置即可生效。"), tr("知道了"), True)

    def make_power_page(self):
        self.power_page_index = self.stack.count()
        _, box = self.page()
        row = QHBoxLayout()
        row.addWidget(label(tr('显卡功耗'), "pageTitle"), 1)
        self.power_refresh = button(tr('刷新读数'))
        self.power_refresh.clicked.connect(self.refresh_power)
        row.addWidget(self.power_refresh)
        box.addLayout(row)
        box.addWidget(label(tr('按显卡设置功耗上限。应用时可能需要 Windows 管理员授权。'), "muted", True))
        self.power_status = label(tr('等待读取 NVIDIA 显卡。'), "muted", True)
        self.power_status.setTextFormat(Qt.TextFormat.PlainText)
        box.addWidget(self.power_status)
        self.power_cards = QVBoxLayout()
        self.power_cards.setSpacing(14)
        box.addLayout(self.power_cards)
        box.addStretch()

    def refresh_power(self):
        if self.power_busy or not self.power_refresh.isEnabled():
            return
        self.power_refresh.setEnabled(False)
        self.power_status.setText(tr('正在读取 NVIDIA 驱动…'))
        self.monitor_submit(lambda _: query_gpus(), self.render_power, self.power_error)

    def power_error(self, message):
        self.power_busy = False
        self.power_refresh.setEnabled(True)
        self.power_status.setText(tr('功耗操作失败：{0}', message))
        self.log(tr('显卡功耗：{0}', message))

    def render_power(self, cards, message=None):
        self.power_busy = False
        self.power_refresh.setEnabled(True)
        while self.power_cards.count():
            self.power_cards.takeAt(0).widget().deleteLater()
        self.power_status.setText(message or (
            tr('已读取 {0} 张 NVIDIA 显卡。', len(cards)) if cards else tr('驱动未返回 NVIDIA 显卡。')
        ))
        def display(value):
            return f"{value:g} W" if value is not None else tr('不可用')

        for gpu in cards:
            card = Card()
            title = label(f"GPU {gpu.index}   {gpu.name}", "cardTitle")
            title.setTextFormat(Qt.TextFormat.PlainText)
            card.box.addWidget(title)
            card.box.addWidget(label(
                tr('当前上限  {0}     当前读数  {1}     默认  {2}', display(gpu.current), display(gpu.draw), display(gpu.default)),
                "muted", True,
            ))
            if gpu.adjustable:
                card.box.addWidget(label(
                    tr('可设范围  {0} – {1}', display(gpu.minimum), display(gpu.maximum)), "muted",
                ))
                row = QHBoxLayout()
                watts = QLineEdit(format(gpu.current, "f"))
                watts.setAccessibleName(tr('GPU {0} 目标功耗上限（瓦）', gpu.index))
                watts.setMaximumWidth(115)
                watts.setMaxLength(12)
                watts.setMinimumHeight(38)
                row.addWidget(label(tr('目标上限')))
                row.addWidget(watts)
                row.addWidget(label("W"))
                apply = button(tr('应用'), "primary")
                apply.clicked.connect(lambda _, item=gpu, edit=watts: self.change_power(
                    item, watts_value(edit.text().strip()), False,
                ))
                row.addWidget(apply)
                reset = button(tr('恢复默认'))
                reset.setEnabled(gpu.accepts(gpu.default))
                reset.clicked.connect(lambda _, item=gpu: self.change_power(item, item.default, True))
                row.addWidget(reset)
                row.addStretch()
                card.box.addLayout(row)
            else:
                card.box.addWidget(label(tr('驱动未提供可调整的功耗范围。'), "muted"))
            self.power_cards.addWidget(card)

    def change_power(self, gpu, target, reset):
        if self.power_busy or not self.power_refresh.isEnabled():
            return
        if not gpu.accepts(target):
            self.power_status.setText(
                tr('请输入 GPU {0} 允许的瓦数（{1}–{2} W）。', gpu.index, format(gpu.minimum, 'g'), format(gpu.maximum, 'g'))
            )
            return
        action = tr('恢复默认') if reset else tr('应用')
        if not self.confirm(tr('{0}功耗上限', action), tr('将 GPU {0}（{1}）设为 {2} W。', gpu.index, gpu.name, format(target, 'g')), action):
            return
        self.power_busy = True
        self.power_refresh.setEnabled(False)
        self.power_status.setText(tr('正在应用并读取驱动结果…'))
        message = tr('GPU {0} 已确认设为 {1} W。', gpu.index, format(target, 'g'))
        def done(cards):
            self.render_power(cards, message)
            self.log(message)
        self.monitor_submit(lambda _: set_power_limit(gpu.uuid, target), done, self.power_error)


    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        for number, item in enumerate(self.nav):
            item.setProperty("selected", number == index)
            item.style().unpolish(item)
            item.style().polish(item)
        if index == self.power_page_index and not self.test:
            self.refresh_power()

    def apply_theme(self):
        theme = self.theme.currentData()
        self.dark = theme == "dark" or (theme == "system" and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)
        p = ({"bg": "#17191f", "rail": "#20232b", "card": "#22262f", "border": "#343a46",
              "fg": "#f0f3fa", "muted": "#a6b1c3", "hover": "#2c323d", "field": "#1c2028",
              "accent": "#60b5ff", "accentfg": "#071c30", "selected": "#243c53"} if self.dark else
             {"bg": "#f6f8fc", "rail": "#edf1f7", "card": "#ffffff", "border": "#e0e6ef",
              "fg": "#19283e", "muted": "#6c7b91", "hover": "#f0f4fa", "field": "#ffffff",
              "accent": "#0078d4", "accentfg": "#ffffff", "selected": "#dcebf9"})
        self.colors = p
        self.setStyleSheet("""QWidget { font-family:'Microsoft YaHei UI','Segoe UI'; font-size:14px; color:FG; }
QMainWindow, QWidget#shell, QWidget#page, QScrollArea, QStackedWidget { background:BG; }
QFrame#sidebar { background:RAIL; border-right:1px solid BORDER; }
QLabel { background:transparent; border:0; }
QLabel#brand { font-size:19px; font-weight:650; }
QLabel#railCaption { font-size:12px; color:MUTED; }
QLabel#railStatus { font-size:12px; color:MUTED; }
QLabel#pageTitle { font-size:29px; font-weight:650; }
QLabel#modelTitle { font-size:23px; font-weight:650; }
QLabel#cardTitle { font-size:18px; font-weight:600; }
QLabel#cardSubtitle { font-size:14px; font-weight:600; }
QLabel#muted { font-size:13px; color:MUTED; }
QLabel#eyebrow { font-size:12px; color:MUTED; font-weight:600; }
QLabel#statValue { font-size:29px; font-weight:650; }
QLabel#statText { font-size:15px; font-weight:600; }
QLabel#iconTile { background:SELECTED; border-radius:13px; qproperty-alignment:AlignCenter; }
QLabel#badge { background:SELECTED; color:ACCENT; padding:6px 11px; border-radius:12px; font-size:12px; }
QFrame#card, QFrame#modelCard { background:CARD; border:1px solid BORDER; border-radius:16px; }
QFrame#divider { background:BORDER; border:0; }
QPushButton { background:CARD; border:1px solid BORDER; border-radius:8px; padding:8px 15px; font-weight:500; }
QPushButton:hover { background:HOVER; }
QPushButton:pressed { background:SELECTED; }
QPushButton:focus { border:1px solid ACCENT; }
QPushButton:disabled { color:MUTED; background:BG; border-color:BORDER; }
QPushButton[kind="primary"] { background:ACCENT; color:ACCENTFG; border:1px solid ACCENT; font-weight:600; }
QPushButton[kind="primary"]:hover { background:#168ade; color:white; }
QPushButton[kind="primary"]:disabled { background:BORDER; border-color:BORDER; color:MUTED; }
QPushButton[kind="nav"] { background:transparent; border:0; text-align:left; padding:10px 13px; font-size:14px; }
QPushButton[kind="nav"]:hover { background:HOVER; }
QPushButton[kind="nav"][selected="true"] { background:SELECTED; color:ACCENT; font-weight:600; }
QPushButton[kind="subtle"], QPushButton[kind="link"] { background:transparent; border:0; }
QPushButton[kind="subtle"]:hover, QPushButton[kind="link"]:hover { background:HOVER; }
QPushButton[kind="link"] { color:ACCENT; padding:4px 9px; font-size:12px; }
QLineEdit, QComboBox { background:FIELD; border:1px solid BORDER; border-radius:7px; padding:8px 10px; }
QLineEdit:focus { border-bottom:2px solid ACCENT; }
QComboBox::drop-down { border:0; width:25px; }
QComboBox QAbstractItemView { background:CARD; selection-background-color:SELECTED; border:1px solid BORDER; }
QPlainTextEdit#logs { background:CARD; border:1px solid BORDER; border-radius:12px; padding:14px; font-family:'Cascadia Code','Consolas','Microsoft YaHei UI'; font-size:12px; }
QProgressBar { border:0; background:BORDER; border-radius:1px; }
QProgressBar::chunk { background:ACCENT; border-radius:1px; }
QScrollBar:vertical { background:transparent; width:8px; margin:3px; }
QScrollBar::handle:vertical { background:BORDER; border-radius:3px; min-height:30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QTableWidget#monitorTable { background:CARD; border:0; font-size:12px; }
QHeaderView::section { background:CARD; color:MUTED; border:0; border-bottom:1px solid BORDER; padding:7px 5px; }
QTableWidget#monitorTable::item { padding:5px; border-bottom:1px solid BORDER; }
QDialog { background:BG; }
QToolTip { background:CARD; color:FG; border:1px solid BORDER; padding:6px; }
""".replace("ACCENTFG", p["accentfg"]).replace("SELECTED", p["selected"]).replace("ACCENT", p["accent"])
            .replace("BORDER", p["border"]).replace("MUTED", p["muted"]).replace("FIELD", p["field"])
            .replace("HOVER", p["hover"]).replace("CARD", p["card"]).replace("RAIL", p["rail"])
            .replace("BG", p["bg"]).replace("FG", p["fg"]))
        palette = QApplication.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(p["bg"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(p["fg"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(p["card"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(p["fg"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(p["card"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(p["fg"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(p["accent"]))
        QApplication.setPalette(palette)
        for item in self.nav:
            item.setIcon(symbol(item.property("glyph"), p["accent"] if item.property("selected") else p["muted"]))
            item.setIconSize(QSize(18, 18))
        self.refresh_button.setIcon(symbol("refresh", p["muted"], 16))
        self.start_button.setIcon(symbol("play", p["accentfg"], 16))
        self.model_icon.setPixmap(symbol("chip", p["accent"], 26).pixmap(30, 30))
        self.modern_chrome()

    def modern_chrome(self):
        if os.name != "nt" or self.test:
            return
        try:
            dwm = ctypes.WinDLL("dwmapi")
            dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            for attribute, value in [(20, int(self.dark)), (33, 2)]:
                data = ctypes.c_int(value)
                dwm.DwmSetWindowAttribute(int(self.winId()), attribute, ctypes.byref(data), 4)
        except OSError:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.modern_chrome)

    def theme_changed(self):
        self.apply_theme()
        if not self.test:
            self.save_settings()

    def browse(self):
        selected = QFileDialog.getExistingDirectory(self, tr('选择 Strata 安装根目录'), self.root.text())
        if selected:
            self.root.setText(selected)

    def save_root(self):
        self.save_settings()
        self.log(tr('安装位置已保存：') + self.root.text().strip())
        self.refresh()

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, tr('保存运行日志'), "LocalModelDesk-log.txt", tr('文本文件 (*.txt)'))
        if path:
            try:
                Path(path).write_text(self.logs.toPlainText(), encoding="utf-8")
            except OSError as exc:
                self.confirm(tr('无法保存日志'), str(exc), tr('知道了'), informational=True)

    @Slot(str)
    def log(self, text):
        if text.strip():
            self.logs.appendPlainText(time.strftime("%H:%M:%S") + "  " + text)
            self.last_message = text.splitlines()[-1]
            self.notice.setText(self.last_message[:160])

    def confirm(self, title: str, text: str, action: str, informational: bool = False) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setFixedWidth(460)
        dialog.setStyleSheet(self.styleSheet())
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(26, 25, 26, 22)
        layout.setSpacing(18)
        layout.addWidget(label(title, "cardTitle"))
        layout.addWidget(label(text, "muted", True))
        row = QHBoxLayout()
        row.addStretch()
        if not informational:
            cancel = button(tr('取消'))
            cancel.clicked.connect(dialog.reject)
            row.addWidget(cancel)
        okay = button(action, "primary")
        okay.clicked.connect(dialog.accept)
        row.addWidget(okay)
        layout.addLayout(row)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def set_busy(self, busy: bool):
        self.busy = busy
        for control in [self.start_button, self.close_button, self.unload_button, self.all_button,
                        self.root, self.browse_button, self.save_button,
                        self.quant, self.context_pick, self.plan_button]:
            control.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy and not self.checking)
        self.progress.setVisible(busy)

    def operation(self, operation: str, force: bool = False):
        if self.busy:
            return
        root = Path(self.root.text().strip())
        if not root.is_dir():
            self.confirm(tr('安装位置不可用'), tr('请在设置中选择远端实际的 Strata 安装根目录。'), tr('知道了'), True)
            return
        self.set_busy(True)
        profile = self.selected_profile()
        if operation == "plan":
            self.navigate(2)
        self.notice.setText(tr('正在执行，请稍候。详细输出可在运行日志中查看。'))
        def execute(emit):
            controller = Controller(root, emit)
            if operation == "start":
                return controller.start(profile, force=force)
            if operation == "plan":
                return controller.plan(profile)
            if operation == "unload":
                return controller.stop()
            return controller.close(force=force, stop_after=operation == "all")
        task = Task(execute)
        self.tasks.add(task)
        task.signals.log.connect(self.log)
        task.signals.result.connect(lambda result: self.operation_done(task, result, operation))
        task.signals.error.connect(lambda error: self.operation_error(task, error))
        self.pool.start(task)

    def operation_done(self, task, result, operation):
        self.tasks.discard(task)
        self.set_busy(False)
        if result.get("needs_force"):
            if self.confirm(tr('OpenCode 仍在运行'), tr('它可能已经最小化到托盘。是否强制退出此安装的 OpenCode？未完成的任务可能中断。'), tr('强制退出')):
                self.operation(operation, force=True)
            else:
                self.log(tr('已取消强制退出。没有继续卸载模型。') if operation == "all" else tr('已取消强制退出。'))
        else:
            self.log(result.get("message", tr('操作完成。')))
        self.refresh()

    def operation_error(self, task, error):
        self.tasks.discard(task)
        self.set_busy(False)
        self.log(tr('操作未完成：') + error)
        self.navigate(2)
        self.confirm(tr('操作未完成'), error, tr('知道了'), True)
        self.refresh()

    def start(self):
        profile = self.selected_profile()
        active = running_profile(self.last_status.get("health"))
        if active != profile:
            detail = tr('启动 {0} / {1}。首次启动可能下载模型。', profile.quant, profile.context_name)
            if active:
                detail = tr('从 {0} / {1} 切换到 {2} / {3}。请先保存正在运行的任务。', active.quant, active.context_name, profile.quant, profile.context_name)
            if profile.quant == "IQ3_XXS" and profile.context == 262144:
                detail += tr(' 64GB 内存运行此档可能出现换页、减速或加载失败。')
            if not self.confirm(tr('启动所选配置'), detail, tr('继续')):
                return
        self.operation("start")

    def unload(self):
        if self.confirm(tr('卸载模型'), tr('停止 Qwen 服务并释放内存和显存。模型文件会保留，正在生成的任务会被中断。'), tr('卸载模型')):
            self.operation("unload")

    def close_desktop(self, all_after: bool):
        self.operation("all" if all_after else "close")

    def close_all(self):
        if self.confirm(tr('结束本次工作'), tr('关闭 OpenCode 后卸载模型。请先保存工作并结束正在执行的任务。'), tr('全部关闭')):
            self.close_desktop(True)

    @Slot()
    def refresh(self):
        if self.busy or self.checking or self.test:
            return
        self.checking = True
        self.refresh_button.setEnabled(False)
        root = Path(self.root.text().strip())
        task = Task(lambda _: Controller(root).status())
        self.tasks.add(task)
        task.signals.result.connect(lambda result: self.status_done(task, result))
        task.signals.error.connect(lambda error: self.status_error(task, error))
        self.pool.start(task)

    def status_done(self, task, result):
        self.tasks.discard(task)
        self.checking = False
        self.refresh_button.setEnabled(not self.busy)
        if not self.busy:
            self.render_status(result)

    def status_error(self, task, error):
        self.tasks.discard(task)
        self.checking = False
        self.refresh_button.setEnabled(not self.busy)
        self.model_badge.setText(tr('状态异常'))
        self.rail_state.setText(tr('●  状态检查失败'))
        if self.last_message != tr('状态检查：') + error:
            self.log(tr('状态检查：') + error)

    def render_status(self, result):
        self.last_status = result
        health = result.get("health")
        active = running_profile(health)
        if active and self.follow_running:
            self.apply_profile(active)
            self.follow_running = False
        profile = self.selected_profile()
        ready = active is not None and loaded(health)
        self.model_badge.setText(tr('●  已就绪') if ready else tr('未连接') if health is None else tr('其他配置') if active is None else tr('已卸载'))
        self.active_model.setText(tr('当前运行：{0} / {1}', active.quant, active.context_name) if ready else
                                  tr('当前服务：{0} / {1}（已卸载）', active.quant, active.context_name) if active else
                                  tr('当前服务为其他配置') if health else tr('当前服务未连接'))
        self.context_label.setText(tr('所选上下文'))
        self.context_value.setText(profile.context_name)
        self.threshold_value.setText(tr('{0}K（默认）', (profile.input - profile.reserved) // 1024))
        free = result.get("free_bytes")
        self.disk_value.setText(f"{free / 1024 ** 3:.1f} GiB" if free is not None else "—")
        self.start_button.setText(tr('切换模型并打开 OpenCode') if active and active != profile else tr('启动模型并打开 OpenCode'))
        self.desktop_status.setText(tr('运行中 · 本地代码助手') if result.get("desktop_count") else tr('未运行 · 模型就绪后即可打开'))
        self.rail_state.setText(tr('●  本地模型已就绪') if ready else tr('●  本地服务未连接') if health is None else tr('●  服务在线'))
        if profile.quant == "IQ3_XXS" and profile.context == 262144:
            self.model_hint.setText(tr('IQ3_XXS / 256K 会逼近 64GB 内存上限，建议先通过 64K 或 128K 验证。'))
        elif ready and active == profile:
            self.model_hint.setText(tr('模型已加载，将复用当前服务，不会重复加载。'))
        elif health and active is None:
            self.model_hint.setText(tr('当前服务是其他模型或配置；此工作台不会自动替换它。'))
        else:
            self.model_hint.setText(tr('首次使用此量化前，可预检剩余空间和下载容量。'))

    def make_disk_label(self):
        self.disk_value = label("—", "statText")
        return self.disk_value

    def make_threshold_label(self):
        self.threshold_value = label("—", "statText")
        return self.threshold_value

    def selected_profile(self):
        return Profile(self.quant.currentText(), self.context_pick.currentData())

    def apply_profile(self, profile):
        self.quant.blockSignals(True)
        self.context_pick.blockSignals(True)
        self.quant.setCurrentText(profile.quant)
        self.context_pick.setCurrentIndex(self.context_pick.findData(profile.context))
        self.context_pick.blockSignals(False)
        self.quant.blockSignals(False)

    def quant_changed(self):
        self.apply_profile(Profile(self.quant.currentText(), 65536 if self.quant.currentText() == "IQ3_XXS" else 262144))
        self.profile_changed()

    def profile_changed(self):
        self.follow_running = False
        self.render_status(self.last_status)
        if not self.test:
            self.save_settings()

    def closeEvent(self, event):
        if self.busy or self.power_busy:
            event.ignore()
            self.log(tr('操作正在进行，请等待完成后关闭控制台。'))
        else:
            self.timer.stop()
            if not self.test:
                self.save_settings()
            event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--generate-icon", type=Path)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    if args.generate_icon:
        args.generate_icon.mkdir(parents=True, exist_ok=True)
        icon = make_app_icon()
        icon.pixmap(256, 256).save(str(args.generate_icon / "QwenDesk.png"))
        if not icon.pixmap(256, 256).save(str(args.generate_icon / "QwenDesk.ico"), "ICO"):
            raise RuntimeError("ICO output failed")
        return 0
    if args.smoke_test:
        from smoke import run
        return run(app, args.smoke_test)
    state_dir = args.state_dir or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "QwenDesk" / "Python"
    socket = QLocalSocket()
    server_name = "QwenDesk-Python-v03-" + os.environ.get("USERNAME", "local")
    socket.connectToServer(server_name)
    if socket.waitForConnected(400):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(400)
        return 0
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    window = Window(state_dir)
    def activate():
        connection = server.nextPendingConnection()
        if connection:
            connection.disconnectFromServer()
        window.showNormal()
        window.raise_()
        window.activateWindow()
    server.newConnection.connect(activate)
    if not server.listen(server_name):
        raise RuntimeError(tr('无法建立本机单实例控制通道。'))
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        try:
            (base / "LocalModelDesk-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            raise

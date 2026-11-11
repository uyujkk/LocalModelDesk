"""Render Strata's Rich Overview into the current Qt page."""
from i18n import tr

from io import StringIO

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QKeyEvent, QPainter
from PySide6.QtWidgets import QLabel, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from strata_overview import overview
from telemetry import fetch, normalize, number, obj, rows


def clean_fields(mapping):
    result = {}
    for key, value in obj(mapping).items():
        if isinstance(value, str):
            result[key] = "".join(char for char in value[:128] if char.isprintable())
        else:
            bounded = number(value)
            result[key] = value if isinstance(value, bool) or (type(value) is int and bounded is not None) else bounded
    return result


def overview_model(raw, source):
    if source == "telemetry":
        data = normalize(raw, source)
        raw = {
            "time": data["sampled_at"],
            "engine": {"max_context": data["context_total"], "model": obj(raw.get("model")).get("id")},
            "live": {"state": data["state"], "queued": data["queued"],
                     "tok_s": data["speed"], "prefill_tok_s_mean": data["speed"]},
            "hardware": {"gpus": data["gpus"], "ram_used": data["ram_used"],
                         "ram_total": data["ram_total"]},
            "requests": raw.get("recent"),
        }
    if not isinstance(raw, dict) or not isinstance(raw.get("live"), dict):
        raise ValueError(tr('Strata 未返回 Overview 所需的监控字段。'))
    hardware = obj(raw.get("hardware"))
    cleaned = {key: clean_fields(raw.get(key)) for key in
               ("engine", "live", "hardware", "hardware_static", "totals")}
    cleaned["hardware"]["gpus"] = [clean_fields(gpu) for gpu in rows(hardware.get("gpus"), 16)
                                   if type(gpu.get("index")) is int and isinstance(gpu.get("name"), str)]
    cleaned["history"] = {key: [number(value) for value in series[-60:]]
                          for key, series in obj(raw.get("history")).items() if isinstance(series, list)}
    cleaned["requests"] = [clean_fields(row) for row in rows(raw.get("requests"), 20)]
    for request in cleaned["requests"]:
        stamp = number(request.get("time"))
        request["time"] = stamp if stamp is not None and stamp <= 4102444800 else None
    cleaned["time"] = number(raw.get("time"))
    model = overview.build(cleaned)
    if source == "telemetry" and data["context_used"] is not None:
        model["context"] = (data["context_used"], data["context_total"])
    return model


class OverviewCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(625)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Strata Overview")
        self.model = None
        self.frozen = False
        self.status = "connecting"
        self.message = "Waiting for Strata metrics..."
        self.lines = []
        self.refresh_layout()

    def refresh_layout(self):
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        for size in range(16, 10, -1):
            font.setPixelSize(size)
            metrics = QFontMetricsF(font)
            columns = int((self.width() - 32) / metrics.horizontalAdvance("M"))
            if columns >= 106:
                break
        self.text_font = font
        self.cell_width = metrics.horizontalAdvance("M")
        self.row_height = round(metrics.height()) + 2
        self.ascent = metrics.ascent()
        columns = max(78, min(110, columns))
        console = Console(file=StringIO(), width=columns, force_terminal=True, color_system="truecolor",
                          legacy_windows=False, highlight=False)
        if self.model is not None:
            content = overview.render(self.model, width=columns, height=34,
                                      connection="http://127.0.0.1:8080",
                                      connection_state=self.status, frozen=self.frozen,
                                      glyphs="solid")
        else:
            content = Text.assemble(("STRATA  Overview   http://127.0.0.1:8080", "bold #a3e635"),
                                    "\n\n", self.message,
                                    "\n\nRead only   |   Space freeze / resume   R refresh")
        self.lines = console.render_lines(content, console.options.update(width=columns), pad=False)
        self.setMinimumHeight(max(210, 32 + len(self.lines) * self.row_height))
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.refresh_layout()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#212121"))
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        for row, segments in enumerate(self.lines):
            x = 16.0
            y = 16.0 + row * self.row_height
            for segment in segments:
                if segment.control:
                    continue
                style = segment.style
                font = QFont(self.text_font)
                font.setBold(bool(style and style.bold))
                painter.setFont(font)
                rgb = style.color.get_truecolor() if style and style.color and not style.color.is_default else None
                color = QColor(rgb.red, rgb.green, rgb.blue) if rgb else QColor("#dadada")
                if style and style.dim:
                    color = QColor("#999999")
                painter.setPen(color)
                position = 0
                for index, char in enumerate(segment.text):
                    if char not in "█▄":
                        continue
                    prefix = segment.text[position:index]
                    painter.drawText(QPointF(x, y + self.ascent), prefix)
                    x += cell_len(prefix) * self.cell_width
                    offset = self.row_height / 2 if char == "▄" else 0
                    painter.fillRect(QRectF(x, y + offset, self.cell_width + 0.5,
                                            self.row_height - offset), color)
                    x += self.cell_width
                    position = index + 1
                suffix = segment.text[position:]
                painter.drawText(QPointF(x, y + self.ascent), suffix)
                x += cell_len(suffix) * self.cell_width


class MonitorPage(QWidget):
    def __init__(self, submit, test=False):
        super().__init__()
        self.submit = submit
        self.test = test
        self.pending = False
        self.paused = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        header = QHBoxLayout()
        title = QLabel(tr('Strata 监控'))
        title.setObjectName("pageTitle")
        header.addWidget(title, 1)
        self.pause = QPushButton(tr('暂停刷新'))
        self.pause.clicked.connect(self.toggle_pause)
        refresh = QPushButton(tr('立即刷新'))
        refresh.clicked.connect(self.resume_and_refresh)
        header.addWidget(self.pause)
        header.addWidget(refresh)
        layout.addLayout(header)
        self.canvas = OverviewCanvas()
        self.canvas.installEventFilter(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.canvas)
        layout.addWidget(scroll, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        if not test:
            self.timer.start()

    def eventFilter(self, watched, event):
        if watched is self.canvas and isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Space:
                self.toggle_pause()
                return True
            if event.key() == Qt.Key.Key_R:
                self.resume_and_refresh()
                return True
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def toggle_pause(self):
        self.paused = not self.paused
        self.canvas.frozen = self.paused
        self.pause.setText(tr('继续刷新') if self.paused else tr('暂停刷新'))
        self.canvas.refresh_layout()
        self.refresh()

    def resume_and_refresh(self):
        if self.paused:
            self.toggle_pause()
        else:
            self.refresh()

    def refresh(self):
        if self.test or self.paused or self.pending or not self.isVisible():
            return
        self.pending = True
        self.submit(lambda _: overview_model(*fetch(raw_snapshot=True)), self.received, self.failed)

    def received(self, model):
        self.pending = False
        if not self.paused:
            self.canvas.model = model
            self.canvas.status = "connected"
            self.canvas.refresh_layout()

    def failed(self, error):
        self.pending = False
        if not self.paused:
            self.canvas.model = None
            self.canvas.message = "Strata monitor unavailable\n" + str(error)[:180]
            self.canvas.refresh_layout()

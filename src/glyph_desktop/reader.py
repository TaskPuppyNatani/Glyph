"""Local PDF document state and asynchronous single-page reader."""

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QIntValidator, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument, QPdfPageRenderer
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QWidget, QVBoxLayout)


class Document(QObject):
    changed = Signal()
    rendered = Signal(QImage, QSize)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pdf = None
        self.renderer = None
        self.path = None
        self.page = 0
        self.mode = "Fit Page"
        self.zoom = 1.0
        self.request = None
        self.generation = 0

    @property
    def count(self):
        return self.pdf.pageCount() if self.pdf else 0

    def open(self, path):
        candidate = QPdfDocument(self)
        error = candidate.load(str(path))
        if (error != QPdfDocument.Error.None_ or candidate.status() != QPdfDocument.Status.Ready
                or candidate.pageCount() < 1):
            candidate.close()
            candidate.deleteLater()
            if error == QPdfDocument.Error.IncorrectPassword:
                return "Password-protected PDFs are not supported yet."
            return "This PDF could not be opened. Check that the file is readable and valid."
        self.close()
        self.pdf = candidate
        self.path = Path(path)
        self.page = 0
        self.mode, self.zoom = "Fit Page", 1.0
        renderer = QPdfPageRenderer(self)
        renderer.setRenderMode(QPdfPageRenderer.RenderMode.MultiThreaded)
        renderer.setDocument(candidate)
        self.renderer = renderer
        generation = self.generation
        renderer.pageRendered.connect(lambda page, size, image, options, request:
                                      self._result(generation, page, size, image, request))
        self.changed.emit()
        return None

    def close(self):
        self.generation += 1
        self.request = None
        if self.renderer:
            self.renderer.setDocument(None)
            self.renderer.deleteLater()
            self.renderer = None
        if self.pdf:
            self.pdf.close()
            self.pdf.deleteLater()
            self.pdf = None
        self.path = None
        self.page = 0
        self.changed.emit()

    def set_page(self, page):
        if self.pdf and 0 <= page < self.count and page != self.page:
            self.page = page
            self.changed.emit()

    def set_zoom(self, mode, value=None):
        self.mode = mode
        if value is not None:
            self.zoom = max(0.25, min(5.0, value))
        self.changed.emit()

    def render(self, logical_size, dpr):
        if not self.renderer or logical_size.isEmpty():
            return
        width = max(1, round(logical_size.width() * dpr))
        height = max(1, round(logical_size.height() * dpr))
        factor = min(1.0, 4096 / width, 4096 / height, (16_000_000 / (width * height)) ** 0.5)
        size = QSize(max(1, round(width * factor)), max(1, round(height * factor)))
        self.request = (self.renderer.requestPage(self.page, size), self.page, size, logical_size)

    def _result(self, generation, page, size, image, request):
        if (generation != self.generation or not self.request or
                (request, page, size) != self.request[:3] or page != self.page):
            return
        self.rendered.emit(image, self.request[3])


class PageCanvas(QLabel):
    """Page image surface; future page overlays can be layered here."""

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def show_image(self, image, logical_size):
        pixmap = QPixmap(image.size())
        pixmap.fill(Qt.GlobalColor.white)
        painter = QPainter(pixmap)
        painter.drawImage(0, 0, image)
        painter.end()
        pixmap.setDevicePixelRatio(image.width() / logical_size.width())
        self.setPixmap(pixmap)
        self.setFixedSize(logical_size)


class Reader(QWidget):
    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.timer = QTimer(self, singleShot=True)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.render)
        self.wheel_guard = QTimer(self, singleShot=True)
        self.wheel_guard.setInterval(220)
        self.wheel_direction = 0
        self.wheel_amount = 0
        self.wheel_kind = None
        self.page_scroll_destination = None
        self.wheel_gesture_active = False
        self.wheel_gesture_changed_page = False
        self._last_navigation_state = (bool(document.pdf), document.generation, document.page)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.previous = QPushButton("Previous")
        self.previous.setToolTip("Previous page (Page Up)")
        self.next = QPushButton("Next")
        self.next.setToolTip("Next page (Page Down)")
        self.page = QLineEdit()
        self.page.setAccessibleName("Current page")
        self.page.setFixedWidth(55)
        self.page.setValidator(QIntValidator(1, 9999999, self.page))
        self.total = QLabel("/ 0")
        self.out = QPushButton("−")
        self.out.setToolTip("Zoom out")
        self.zoom = QComboBox()
        self.zoom.setAccessibleName("Zoom")
        self.zoom.addItems(["Fit Page", "Fit Width", "100%"])
        self.inside = QPushButton("+")
        self.inside.setToolTip("Zoom in")
        for widget in (self.previous, self.page, self.total, self.next, self.out, self.zoom, self.inside):
            bar.addWidget(widget)
        bar.addStretch()
        layout.addLayout(bar)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.surface = QWidget()
        self.surface_layout = QVBoxLayout(self.surface)
        self.surface_layout.setContentsMargins(24, 24, 24, 24)
        self.canvas = PageCanvas()
        self.surface_layout.addWidget(self.canvas, alignment=Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.surface)
        layout.addWidget(self.scroll)
        self.scroll.viewport().installEventFilter(self)
        self.surface.installEventFilter(self)
        self.canvas.installEventFilter(self)
        self.previous.clicked.connect(lambda: document.set_page(document.page - 1))
        self.next.clicked.connect(lambda: document.set_page(document.page + 1))
        self.page.returnPressed.connect(self.enter_page)
        self.zoom.activated.connect(self.select_zoom)
        self.out.clicked.connect(lambda: self.step_zoom(-1))
        self.inside.clicked.connect(lambda: self.step_zoom(1))
        document.changed.connect(self.update_state)
        document.rendered.connect(self.canvas.show_image)
        self.update_state()

    def eventFilter(self, watched, event):
        if (watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize
                and self.document.mode != "Manual"):
            self.schedule()
        elif (watched in (self.scroll.viewport(), self.surface, self.canvas)
              and event.type() == QEvent.Type.Wheel and self.document.pdf):
            if self.handle_wheel(event):
                return True
            if watched is not self.scroll.viewport():
                QApplication.sendEvent(self.scroll.viewport(), event)
                return True
        return super().eventFilter(watched, event)

    def handle_wheel(self, event):
        modifiers = event.modifiers()
        if modifiers not in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier):
            return False
        if event.phase() == Qt.ScrollPhase.ScrollBegin:
            self.wheel_gesture_active = True
            self.wheel_gesture_changed_page = False
        elif event.phase() == Qt.ScrollPhase.ScrollEnd:
            self.wheel_gesture_active = False
            self.wheel_gesture_changed_page = False
            self.wheel_amount = 0
            self.wheel_kind = None
        pixel = event.pixelDelta().y()
        delta = pixel if pixel else event.angleDelta().y()
        if not delta:
            if event.phase() == Qt.ScrollPhase.ScrollEnd:
                self.wheel_amount = 0
                self.wheel_kind = None
            return False
        direction = 1 if delta > 0 else -1
        kind = (bool(pixel), modifiers == Qt.KeyboardModifier.ControlModifier, direction)
        if kind != self.wheel_kind:
            self.wheel_amount = 0
            self.wheel_kind = kind
        if modifiers == Qt.KeyboardModifier.ControlModifier:
            self.wheel_amount += abs(delta)
            if self.wheel_amount >= (40 if pixel else 120):
                self.wheel_amount = 0
                self.step_zoom(direction)
            event.accept()
            return True

        bar = self.scroll.verticalScrollBar()
        if (direction > 0 and bar.value() > bar.minimum()) or (direction < 0 and bar.value() < bar.maximum()):
            self.wheel_amount = 0
            return False
        target = self.document.page - direction
        if not 0 <= target < self.document.count:
            self.wheel_amount = 0
            event.accept()
            return True
        if self.wheel_gesture_active and self.wheel_gesture_changed_page:
            event.accept()
            return True
        if self.wheel_guard.isActive() and self.wheel_direction == direction:
            self.wheel_guard.start()
            event.accept()
            return True
        self.wheel_amount += abs(delta)
        if self.wheel_amount >= (40 if pixel else 120):
            self.wheel_amount = 0
            self.document.set_page(target)
            self.page_scroll_destination = "bottom" if direction > 0 else "top"
            if self.wheel_gesture_active:
                self.wheel_gesture_changed_page = True
            self.wheel_direction = direction
            self.wheel_guard.start()
        event.accept()
        return True

    def enter_page(self):
        text = self.page.text()
        if text.isdigit() and 1 <= int(text) <= self.document.count:
            self.document.set_page(int(text) - 1)
        self.page.setText(str(self.document.page + 1))

    def select_zoom(self, index):
        text = self.zoom.itemText(index)
        if text == "100%":
            self.document.set_zoom("Manual", 1.0)
        elif text in ("Fit Page", "Fit Width"):
            self.document.set_zoom(text)

    def step_zoom(self, direction):
        current = self.effective_zoom()
        steps = (0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 5.0)
        values = [v for v in steps if v > current + 0.001] if direction > 0 else [v for v in steps if v < current - 0.001]
        if values:
            self.document.set_zoom("Manual", values[0] if direction > 0 else values[-1])

    def effective_zoom(self):
        if not self.document.pdf:
            return 1.0
        page = self.document.pdf.pagePointSize(self.document.page)
        if page.isEmpty():
            return 1.0
        if self.document.mode == "Manual":
            return self.document.zoom
        viewport = self.scroll.viewport().size()
        width = max(1, viewport.width() - 50) / page.width()
        height = max(1, viewport.height() - 50) / page.height()
        return width if self.document.mode == "Fit Width" else min(width, height)

    def update_state(self):
        active = bool(self.document.pdf)
        navigation_state = (active, self.document.generation, self.document.page)
        page_changed = active and navigation_state != self._last_navigation_state
        self._last_navigation_state = navigation_state
        if page_changed:
            self.page_scroll_destination = "top"
        elif not active:
            self.page_scroll_destination = None
        self.wheel_amount = 0
        self.wheel_kind = None
        self.previous.setEnabled(active and self.document.page > 0)
        self.next.setEnabled(active and self.document.page + 1 < self.document.count)
        self.page.setText(str(self.document.page + 1) if active else "")
        self.total.setText(f"/ {self.document.count}")
        label = self.document.mode if self.document.mode != "Manual" else f"{round(self.document.zoom * 100)}%"
        self.zoom.blockSignals(True)
        if self.zoom.count() > 3:
            self.zoom.removeItem(3)
        if label not in ("Fit Page", "Fit Width", "100%"):
            self.zoom.addItem(label)
        self.zoom.setCurrentText(label)
        self.zoom.blockSignals(False)
        if active:
            self.canvas.clear()
            self.schedule()
        else:
            self.timer.stop()
            self.canvas.clear()

    def schedule(self):
        self.document.request = None
        self.timer.start()

    def render(self):
        if not self.document.pdf:
            return
        page = self.document.pdf.pagePointSize(self.document.page)
        scale = self.effective_zoom()
        size = QSize(max(1, round(page.width() * scale)), max(1, round(page.height() * scale)))
        self.canvas.setFixedSize(size)
        self.document.render(size, self.devicePixelRatioF())
        if self.page_scroll_destination is not None:
            QTimer.singleShot(0, self.apply_page_scroll_destination)

    def apply_page_scroll_destination(self):
        if self.page_scroll_destination is not None:
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.maximum() if self.page_scroll_destination == "bottom" else bar.minimum())
            self.page_scroll_destination = None

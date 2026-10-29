"""PDF document state and a virtualized, continuously scrolling page reader."""

from bisect import bisect_left, bisect_right
from pathlib import Path

from PySide6.QtCore import (QAbstractListModel, QEvent, QModelIndex, QObject, QRectF,
                            QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QImage, QIntValidator, QPainter, QPen, QPixmap,
                           QPalette)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QComboBox,
                               QHBoxLayout, QLabel, QLineEdit, QListView, QPushButton,
                               QStyledItemDelegate, QStyle, QVBoxLayout, QWidget)

from .rendering import BoundedImageCache, PageRenderController, RenderJob, make_render_job


class Document(QObject):
    """Lightweight PDF state; render workers belong to the reader."""

    changed = Signal()
    aboutToChange = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pdf: QPdfDocument | None = None
        self.path: Path | None = None
        self.page_sizes: list[tuple[float, float]] = []
        self.page = 0
        self.mode = "Fit Page"
        self.zoom = 1.0
        self.generation = 0

    @property
    def count(self) -> int:
        return self.pdf.pageCount() if self.pdf else 0

    def open(self, path: str | Path) -> str | None:
        candidate = QPdfDocument(self)
        error = candidate.load(str(path))
        if (error != QPdfDocument.Error.None_ or candidate.status() != QPdfDocument.Status.Ready
                or candidate.pageCount() < 1):
            candidate.close()
            candidate.deleteLater()
            if error == QPdfDocument.Error.IncorrectPassword:
                return "Password-protected PDFs are not supported yet."
            return "This PDF could not be opened. Check that the file is readable and valid."

        self.aboutToChange.emit()
        self._release_document()
        self.generation += 1
        self.pdf = candidate
        self.path = Path(path)
        self.page_sizes = []
        for page in range(candidate.pageCount()):
            size = candidate.pagePointSize(page)
            self.page_sizes.append((max(1.0, size.width()), max(1.0, size.height())))
        self.page = 0
        self.mode, self.zoom = "Fit Page", 1.0
        self.changed.emit()
        return None

    def _release_document(self) -> None:
        if self.pdf:
            self.pdf.close()
            self.pdf.deleteLater()
        self.pdf = None
        self.path = None
        self.page_sizes = []
        self.page = 0

    def close(self) -> None:
        self.aboutToChange.emit()
        self._release_document()
        self.generation += 1
        self.changed.emit()

    def set_page(self, page: int) -> None:
        if self.pdf and 0 <= page < self.count and page != self.page:
            self.page = page
            self.changed.emit()

    def set_zoom(self, mode: str, value: float | None = None) -> None:
        if mode == "Manual":
            value = max(0.25, min(5.0, value if value is not None else self.zoom))
        if self.mode == mode and (mode != "Manual" or self.zoom == value):
            return
        self.mode = mode
        if value is not None:
            self.zoom = value
        self.changed.emit()


class PageCanvas(QLabel):
    """Small image-compositing helper retained for transparent PDF render results."""

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def show_image(self, image: QImage, logical_size: QSize) -> None:
        pixmap = QPixmap(image.size())
        pixmap.fill(Qt.GlobalColor.white)
        painter = QPainter(pixmap)
        painter.drawImage(0, 0, image)
        painter.end()
        pixmap.setDevicePixelRatio(image.width() / max(1, logical_size.width()))
        self.setPixmap(pixmap)
        self.setFixedSize(logical_size)


def current_page_for_viewport(page_tops: list[int], page_heights: list[int],
                              scroll_top: int, viewport_height: int) -> int:
    """Return the page with the largest visible area; ties favor the earlier page."""
    if not page_tops:
        return 0
    view_top = max(0, scroll_top)
    view_bottom = view_top + max(1, viewport_height)
    first = max(0, bisect_right(page_tops, view_top) - 1)
    best_page = first
    best_overlap = -1
    for page in range(max(0, first - 1), len(page_tops)):
        top = page_tops[page]
        if top >= view_bottom and page > first:
            break
        bottom = top + page_heights[page]
        overlap = max(0, min(view_bottom, bottom) - max(view_top, top))
        if overlap > best_overlap:
            best_overlap = overlap
            best_page = page
    if best_overlap:
        return best_page
    center = view_top + viewport_height / 2
    before = max(0, min(len(page_tops) - 1, bisect_right(page_tops, center) - 1))
    after = min(len(page_tops) - 1, before + 1)
    return min((before, after),
               key=lambda page: abs(page_tops[page] + page_heights[page] / 2 - center))


class ContinuousPageView(QAbstractScrollArea):
    """Virtual document surface that paints only pages near the viewport."""

    currentPageChanged = Signal(int)
    layoutChanged = Signal(float, float)
    layoutMargin = 24
    pageGap = 24

    def __init__(self, reader, parent=None):
        super().__init__(parent)
        self.reader = reader
        self.page_sizes: list[tuple[float, float]] = []
        self.page_rects: list[QRectF] = []
        self.page_tops: list[int] = []
        self.page_heights: list[int] = []
        self.page_bottoms: list[int] = []
        self.virtual_width = 0
        self.total_height = 0
        self.zoom_scale = 1.0
        self._laying_out = False
        self.current_timer = QTimer(self, singleShot=True)
        self.current_timer.setInterval(90)
        self.current_timer.timeout.connect(self._emit_current_page)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setBackgroundRole(QPalette.ColorRole.Base)
        self.viewport().setBackgroundRole(QPalette.ColorRole.Base)
        self.viewport().setAutoFillBackground(True)
        self.horizontalScrollBar().setSingleStep(40)
        self.verticalScrollBar().setSingleStep(40)
        self.horizontalScrollBar().valueChanged.connect(self._scroll_changed)
        self.verticalScrollBar().valueChanged.connect(self._scroll_changed)

    def set_pages(self, sizes: list[tuple[float, float]]) -> None:
        self.page_sizes = list(sizes)
        self.relayout(preserve_anchor=False)
        self.verticalScrollBar().setValue(0)
        self.horizontalScrollBar().setValue(0)
        self.viewport().update()
        self._schedule_current_page()

    def clear_document(self) -> None:
        self.current_timer.stop()
        self.page_sizes.clear()
        self.page_rects.clear()
        self.page_tops.clear()
        self.page_heights.clear()
        self.page_bottoms.clear()
        self.total_height = 0
        self.virtual_width = 0
        self.horizontalScrollBar().setRange(0, 0)
        self.verticalScrollBar().setRange(0, 0)
        self.viewport().update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.relayout(preserve_anchor=True)
        self.reader.schedule_render()

    def _fit_scale(self) -> float:
        if not self.page_sizes:
            return 1.0
        available_width = max(1, self.viewport().width() - 2 * self.layoutMargin)
        available_height = max(1, self.viewport().height() - 2 * self.layoutMargin)
        widest = max(width for width, _ in self.page_sizes)
        if self.reader.document.mode == "Manual":
            return self.reader.document.zoom
        width_scale = available_width / widest
        if self.reader.document.mode == "Fit Width":
            return width_scale
        heights = sorted(height for _, height in self.page_sizes)
        typical_height = heights[len(heights) // 2]
        return min(width_scale, available_height / typical_height)

    def _capture_anchor(self) -> tuple[int, float]:
        if not self.page_rects:
            return 0, 0.5
        center_y = self.verticalScrollBar().value() + self.viewport().height() / 2
        page = current_page_for_viewport(self.page_tops, self.page_heights,
                                         round(center_y - self.viewport().height() / 2),
                                         self.viewport().height())
        rect = self.page_rects[page]
        relative = (center_y - rect.top()) / max(1.0, rect.height())
        return page, max(0.0, min(1.0, relative))

    def relayout(self, preserve_anchor: bool = True) -> None:
        if not self.page_sizes:
            self.zoom_scale = 1.0
            return
        anchor = self._capture_anchor() if preserve_anchor else None
        old_scale = self.zoom_scale
        scale = self._fit_scale()
        widths = [max(1, round(width * scale)) for width, _ in self.page_sizes]
        heights = [max(1, round(height * scale)) for _, height in self.page_sizes]
        widest = max(widths)
        self.virtual_width = max(self.viewport().width(), widest + 2 * self.layoutMargin)
        cursor = self.layoutMargin
        rects = []
        tops = []
        for width, height in zip(widths, heights):
            tops.append(cursor)
            rects.append(QRectF((self.virtual_width - width) / 2, cursor, width, height))
            cursor += height + self.pageGap
        self.total_height = max(0, cursor - self.pageGap + self.layoutMargin)
        self.page_rects = rects
        self.page_tops = tops
        self.page_heights = heights
        self.page_bottoms = [top + height for top, height in zip(tops, heights)]
        self.zoom_scale = scale

        self._laying_out = True
        self.horizontalScrollBar().setPageStep(self.viewport().width())
        self.verticalScrollBar().setPageStep(self.viewport().height())
        self.horizontalScrollBar().setRange(0, max(0, self.virtual_width - self.viewport().width()))
        self.verticalScrollBar().setRange(0, max(0, self.total_height - self.viewport().height()))
        if anchor is None:
            self.verticalScrollBar().setValue(0)
            self.horizontalScrollBar().setValue(0)
        else:
            page, relative = anchor
            page = min(page, len(rects) - 1)
            target = rects[page].top() + rects[page].height() * relative - self.viewport().height() / 2
            self.verticalScrollBar().setValue(round(target))
        self._laying_out = False
        self.viewport().update()
        self._schedule_current_page()
        if abs(old_scale - scale) > 0.0001:
            self.layoutChanged.emit(old_scale, scale)

    def _scroll_changed(self, _value: int) -> None:
        if not self._laying_out:
            self.viewport().update()
            self._schedule_current_page()
            self.reader.schedule_render()

    def _schedule_current_page(self) -> None:
        if self.page_rects and not self.current_timer.isActive():
            self.current_timer.start()

    def _emit_current_page(self) -> None:
        if self.page_rects:
            page = current_page_for_viewport(
                self.page_tops, self.page_heights,
                self.verticalScrollBar().value(), self.viewport().height())
            self.currentPageChanged.emit(page)

    def visible_pages(self, prefetch: int = 1) -> list[int]:
        if not self.page_rects:
            return []
        top = self.verticalScrollBar().value()
        bottom = top + max(1, self.viewport().height())
        first = bisect_left(self.page_bottoms, top)
        last = bisect_right(self.page_tops, bottom)
        first = max(0, first - prefetch)
        last = min(len(self.page_rects), last + prefetch)
        return list(range(first, last))

    def scroll_to_page(self, page: int) -> None:
        if 0 <= page < len(self.page_rects):
            rect = self.page_rects[page]
            value = rect.center().y() - self.viewport().height() / 2
            self.verticalScrollBar().setValue(round(value))
            self.horizontalScrollBar().setValue(0)
            self.currentPageChanged.emit(page)

    def reading_position(self):
        return self._capture_anchor()

    def restore_position(self, page: int, offset: float) -> None:
        if not self.page_rects:
            return
        page = min(max(0, page), len(self.page_rects) - 1)
        rect = self.page_rects[page]
        target = rect.top() + rect.height() * offset - self.viewport().height() / 2
        self.verticalScrollBar().setValue(round(target))
        self.currentPageChanged.emit(page)

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if not self.page_rects:
            return
        v_offset = self.verticalScrollBar().value()
        h_offset = self.horizontalScrollBar().value()
        dirty_top = event.rect().top() + v_offset
        dirty_bottom = event.rect().bottom() + v_offset
        first = bisect_left(self.page_bottoms, dirty_top)
        for page in range(first, len(self.page_rects)):
            source_rect = self.page_rects[page]
            if source_rect.top() > dirty_bottom:
                break
            rect = source_rect.translated(-h_offset, -v_offset)
            painter.fillRect(rect.translated(4, 5), QColor(0, 0, 0, 24))
            painter.fillRect(rect, Qt.GlobalColor.white)
            image = self.reader.page_image(page)
            if image is not None:
                painter.drawImage(rect, image)
            painter.setPen(QPen(QColor("#aeb4bb"), 1))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.end()


class ThumbnailModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.page_count = 0

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self.page_count

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < self.page_count:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return str(index.row() + 1)
        return None

    def set_page_count(self, count: int) -> None:
        if count == self.page_count:
            return
        self.beginResetModel()
        self.page_count = count
        self.endResetModel()


class ThumbnailDelegate(QStyledItemDelegate):
    item_width = 168
    item_height = 190

    def __init__(self, reader, parent=None):
        super().__init__(parent)
        self.reader = reader

    def sizeHint(self, option, index):
        return QSize(self.item_width, self.item_height)

    def paint(self, painter, option, index):
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        background = option.palette.color(
            QPalette.ColorRole.Highlight if selected else QPalette.ColorRole.Base)
        painter.fillRect(option.rect, background)
        content = option.rect.adjusted(12, 8, -12, -30)
        image = self.reader.thumbnail_image(index.row())
        if image is None:
            page_width, page_height = self.reader.document.page_sizes[index.row()]
            ratio = min(content.width() / page_width, content.height() / page_height)
            image_width = max(1, round(page_width * ratio))
            image_height = max(1, round(page_height * ratio))
            image_rect = QRectF(content.center().x() - image_width / 2,
                                content.center().y() - image_height / 2,
                                image_width, image_height)
            painter.fillRect(image_rect, Qt.GlobalColor.white)
            painter.setPen(QPen(QColor("#aeb4bb"), 1))
            painter.drawRect(image_rect.adjusted(0, 0, -1, -1))
        else:
            ratio = min(content.width() / image.width(), content.height() / image.height())
            image_width = image.width() * ratio
            image_height = image.height() * ratio
            image_rect = QRectF(content.center().x() - image_width / 2,
                                content.center().y() - image_height / 2,
                                image_width, image_height)
            painter.fillRect(image_rect, Qt.GlobalColor.white)
            painter.drawImage(image_rect, image)
            painter.setPen(QPen(QColor("#aeb4bb"), 1))
            painter.drawRect(image_rect.adjusted(0, 0, -1, -1))
        painter.setPen(option.palette.color(QPalette.ColorRole.Text))
        painter.drawText(option.rect.adjusted(4, option.rect.height() - 24, -4, -2),
                         Qt.AlignmentFlag.AlignCenter, index.data())
        painter.restore()


class Reader(QWidget):
    """Navigation, zoom, virtual page drawing, and lazy thumbnail coordination."""

    def __init__(self, document: Document, parent=None):
        super().__init__(parent)
        self.document = document
        self.full_cache = BoundedImageCache(8, 96 * 1024 * 1024)
        self.thumbnail_cache = BoundedImageCache(32, 8 * 1024 * 1024)
        self.controller: PageRenderController | None = None
        self._full_jobs: dict[int, RenderJob] = {}
        self._zoom_revision = 0
        self._view_generation = -1
        self._view_zoom = (document.mode, document.zoom)
        self._thumb_visible = True
        self._render_timer = QTimer(self, singleShot=True)
        self._render_timer.setInterval(70)
        self._render_timer.timeout.connect(self._request_visible_pages)
        self.wheel_amount = 0
        self.wheel_kind = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
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
        for widget in (self.previous, self.page, self.total, self.next,
                       self.out, self.zoom, self.inside):
            bar.addWidget(widget)
        bar.addStretch()
        layout.addLayout(bar)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self.sidebar = QWidget(self)
        self.sidebar.setFixedWidth(184)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(4, 4, 8, 4)
        sidebar_layout.setSpacing(4)
        heading = QLabel("Thumbnails")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar_layout.addWidget(heading)
        self.thumbnail_model = ThumbnailModel(self)
        self.thumbnail_list = QListView(self.sidebar)
        self.thumbnail_list.setAccessibleName("Page thumbnails")
        self.thumbnail_list.setModel(self.thumbnail_model)
        self.thumbnail_list.setItemDelegate(ThumbnailDelegate(self, self.thumbnail_list))
        self.thumbnail_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.thumbnail_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.thumbnail_list.setUniformItemSizes(True)
        self.thumbnail_list.setSpacing(0)
        sidebar_layout.addWidget(self.thumbnail_list, 1)
        content.addWidget(self.sidebar)
        self.page_view = ContinuousPageView(self, self)
        content.addWidget(self.page_view, 1)
        layout.addLayout(content, 1)

        self.previous.clicked.connect(lambda: self.navigate_to_page(document.page - 1))
        self.next.clicked.connect(lambda: self.navigate_to_page(document.page + 1))
        self.page.returnPressed.connect(self.enter_page)
        self.zoom.activated.connect(self.select_zoom)
        self.out.clicked.connect(lambda: self.step_zoom(-1))
        self.inside.clicked.connect(lambda: self.step_zoom(1))
        self.thumbnail_list.clicked.connect(lambda index: self.navigate_to_page(index.row()))
        self.thumbnail_list.verticalScrollBar().valueChanged.connect(self.schedule_render)
        self.page_view.currentPageChanged.connect(self._on_current_page)
        self.page_view.layoutChanged.connect(self._on_layout_changed)
        self.page_view.viewport().installEventFilter(self)
        self.document.aboutToChange.connect(self._reset_document_state)
        self.document.changed.connect(self.update_state)
        self.update_state()

    def eventFilter(self, watched, event):
        if (watched is self.page_view.viewport() and event.type() == QEvent.Type.Wheel
                and self.document.pdf):
            if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                pixel = event.pixelDelta().y()
                delta = pixel if pixel else event.angleDelta().y()
                if delta:
                    kind = (bool(pixel), 1 if delta > 0 else -1)
                    if kind != self.wheel_kind:
                        self.wheel_amount = 0
                        self.wheel_kind = kind
                    self.wheel_amount += abs(delta)
                    threshold = 40 if pixel else 120
                    if self.wheel_amount >= threshold:
                        self.wheel_amount = 0
                        self.step_zoom(kind[1])
                    event.accept()
                    return True
                if event.phase() == Qt.ScrollPhase.ScrollEnd:
                    self.wheel_amount = 0
                    self.wheel_kind = None
            elif (event.modifiers() == Qt.KeyboardModifier.NoModifier
                  and event.pixelDelta().y()):
                bar = self.page_view.verticalScrollBar()
                bar.setValue(bar.value() - event.pixelDelta().y())
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _reset_document_state(self) -> None:
        self._render_timer.stop()
        if self.controller:
            self.controller.close()
            self.controller = None
        self.full_cache.clear()
        self.thumbnail_cache.clear()
        self._full_jobs.clear()
        self.thumbnail_model.set_page_count(0)
        self.thumbnail_list.setCurrentIndex(QModelIndex())
        self.page_view.clear_document()
        self._view_generation = -1
        self._zoom_revision += 1

    def update_state(self) -> None:
        active = self.document.pdf is not None
        if not active:
            self._reset_document_state()
        elif self._view_generation != self.document.generation:
            self._view_generation = self.document.generation
            self.thumbnail_model.set_page_count(self.document.count)
            self.page_view.set_pages(self.document.page_sizes)
            self._view_zoom = (self.document.mode, self.document.zoom)
            self._zoom_revision += 1
            self.controller = PageRenderController(self.document.pdf, self.document.generation, self)
            self.controller.rendered.connect(self._on_rendered)
        elif self._view_zoom != (self.document.mode, self.document.zoom):
            self._view_zoom = (self.document.mode, self.document.zoom)
            self._zoom_revision += 1
            self.full_cache.clear()
            self._full_jobs.clear()
            self.page_view.relayout(preserve_anchor=True)

        self.wheel_amount = 0
        self.wheel_kind = None
        self.previous.setEnabled(active and self.document.page > 0)
        self.next.setEnabled(active and self.document.page + 1 < self.document.count)
        self.page.setText(str(self.document.page + 1) if active else "")
        self.total.setText(f"/ {self.document.count}")
        label = self.document.mode if self.document.mode != "Manual" else f"{round(self.document.zoom * 100)}%"
        self.zoom.blockSignals(True)
        while self.zoom.count() > 3:
            self.zoom.removeItem(3)
        if label not in ("Fit Page", "Fit Width", "100%"):
            self.zoom.addItem(label)
        self.zoom.setCurrentText(label)
        self.zoom.blockSignals(False)
        if active:
            self._sync_thumbnail(self.document.page)
            self.schedule_render()

    def _on_layout_changed(self, _old_scale: float, _new_scale: float) -> None:
        self._zoom_revision += 1
        self.full_cache.clear()
        self._full_jobs.clear()
        self.schedule_render()

    def _on_current_page(self, page: int) -> None:
        if self.document.pdf and page != self.document.page:
            self.document.set_page(page)
        self._sync_thumbnail(page)

    def _sync_thumbnail(self, page: int) -> None:
        if 0 <= page < self.thumbnail_model.page_count:
            self.thumbnail_list.setCurrentIndex(self.thumbnail_model.index(page, 0))

    def navigate_to_page(self, page: int) -> None:
        if not self.document.pdf or not 0 <= page < self.document.count:
            return
        self.page_view.scroll_to_page(page)
        self.document.set_page(page)
        self._sync_thumbnail(page)

    def enter_page(self) -> None:
        text = self.page.text()
        if text.isdigit() and 1 <= int(text) <= self.document.count:
            self.navigate_to_page(int(text) - 1)
        self.page.setText(str(self.document.page + 1) if self.document.pdf else "")

    def select_zoom(self, index: int) -> None:
        text = self.zoom.itemText(index)
        if text == "100%":
            self.document.set_zoom("Manual", 1.0)
        elif text in ("Fit Page", "Fit Width"):
            self.document.set_zoom(text)

    def step_zoom(self, direction: int) -> None:
        current = self.effective_zoom()
        steps = (0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 5.0)
        values = [value for value in steps if value > current + 0.001] if direction > 0 else [
            value for value in steps if value < current - 0.001]
        if values:
            self.document.set_zoom("Manual", values[0] if direction > 0 else values[-1])

    def effective_zoom(self) -> float:
        return self.page_view.zoom_scale if self.document.pdf else 1.0

    def set_thumbnail_visible(self, visible: bool) -> None:
        self._thumb_visible = visible
        self.sidebar.setVisible(visible)
        self.schedule_render()

    def schedule_render(self, _value=None) -> None:
        if self.document.pdf and self.controller:
            self._render_timer.start()

    def _thumbnail_job(self, page: int) -> RenderJob:
        width, height = self.document.page_sizes[page]
        scale = min(132 / width, 150 / height)
        logical = QSize(max(1, round(width * scale)), max(1, round(height * scale)))
        return make_render_job(page, logical, self.devicePixelRatioF(), "thumbnail")

    def _request_visible_pages(self) -> None:
        if not self.document.pdf or not self.controller:
            return
        jobs = []
        self._full_jobs.clear()
        visible = set(self.page_view.visible_pages(prefetch=0))
        pages = self.page_view.visible_pages(prefetch=1)
        current = current_page_for_viewport(
            self.page_view.page_tops, self.page_view.page_heights,
            self.page_view.verticalScrollBar().value(), self.page_view.viewport().height())
        pages.sort(key=lambda page: (page not in visible, abs(page - current)))
        for page in pages:
            rect = self.page_view.page_rects[page]
            logical = QSize(max(1, round(rect.width())), max(1, round(rect.height())))
            job = make_render_job(page, logical, self.devicePixelRatioF(),
                                  "page", self._zoom_revision)
            self._full_jobs[page] = job
            key = self._cache_key(job, self.document.generation)
            if not self.full_cache.contains(key):
                jobs.append(job)
        if self._thumb_visible:
            for page in self._visible_thumbnail_pages():
                job = self._thumbnail_job(page)
                if not self.thumbnail_cache.contains(self._cache_key(job, self.document.generation)):
                    jobs.append(job)
        self.controller.set_jobs(jobs)
        self.page_view.viewport().update()

    def _visible_thumbnail_pages(self) -> list[int]:
        count = self.thumbnail_model.page_count
        if not count or not self._thumb_visible:
            return []
        first_index = self.thumbnail_list.indexAt(self.thumbnail_list.viewport().rect().topLeft())
        first = first_index.row() if first_index.isValid() else 0
        rows = self.thumbnail_list.viewport().height() // ThumbnailDelegate.item_height + 3
        first = max(0, first - 2)
        return list(range(first, min(count, first + rows + 2)))

    @staticmethod
    def _cache_key(job: RenderJob, generation: int) -> tuple:
        return (generation, *job.identity)

    def page_image(self, page: int) -> QImage | None:
        job = self._full_jobs.get(page)
        if job is None:
            return None
        return self.full_cache.get(self._cache_key(job, self.document.generation))

    def thumbnail_image(self, page: int) -> QImage | None:
        if not 0 <= page < len(self.document.page_sizes):
            return None
        job = self._thumbnail_job(page)
        return self.thumbnail_cache.get(self._cache_key(job, self.document.generation))

    def _on_rendered(self, job: RenderJob, image: QImage, generation: int) -> None:
        if generation != self.document.generation or not self.document.pdf:
            return
        key = self._cache_key(job, generation)
        if job.purpose == "page":
            if job.revision != self._zoom_revision or job.page not in self._full_jobs:
                return
            if job.identity != self._full_jobs[job.page].identity:
                return
            self.full_cache.put(key, image)
            self.page_view.viewport().update()
        elif job.purpose == "thumbnail":
            self.thumbnail_cache.put(key, image)
            self.thumbnail_list.viewport().update()

"""Embedded PDF outline model and tree UI."""

from dataclasses import dataclass

from PySide6.QtCore import QModelIndex, QPointF, Qt, Signal
from PySide6.QtPdf import QPdfBookmarkModel
from PySide6.QtWidgets import QLabel, QStackedWidget, QTreeView, QVBoxLayout, QWidget


@dataclass(frozen=True)
class OutlineDestination:
    """Bookmark destination values detached from the source model index."""

    generation: int
    page: int
    location: tuple[float, float] | None
    zoom: float | None


class OutlineTreeView(QTreeView):
    """Tree view that activates the current item with Enter or Return."""

    enterPressed = Signal(QModelIndex)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            index = self.currentIndex()
            if index.isValid():
                self.enterPressed.emit(index)
                event.accept()
                return
        super().keyPressEvent(event)


class OutlinePanel(QWidget):
    """Display the current document's native QtPdf outline as a real tree."""

    destinationActivated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._empty_model = QPdfBookmarkModel(self)
        self.model = self._empty_model
        self.tree = OutlineTreeView(self)
        self.tree.setAccessibleName("Document outline")
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.setModel(self.model)

        self.empty_label = QLabel("No document outline", self)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self.tree)
        self._stack.addWidget(self.empty_label)
        layout.addWidget(self._stack)

        self._document = None
        self._generation: int | None = None
        self.tree.clicked.connect(self._on_tree_activated)
        self.tree.enterPressed.connect(self._on_tree_activated)
        self._connect_model(self.model)
        self._refresh_empty_state()

    def set_document(self, document, generation: int | None) -> None:
        if document is None:
            if self._document is None and self.model is self._empty_model:
                return
            previous_model = self.model
            self._document = None
            self._generation = None
            self.model = self._empty_model
            self.tree.setModel(self.model)
            if previous_model is not self._empty_model:
                self._retire_model(previous_model)
            self._refresh_empty_state()
            return

        if document is self._document and generation == self._generation:
            return

        previous_model = self.model
        self._document = document
        self._generation = generation
        self.model = QPdfBookmarkModel(self)
        self._connect_model(self.model)
        self.tree.setModel(self.model)
        if previous_model is not self._empty_model:
            self._retire_model(previous_model)
        self.model.setDocument(document)
        self._refresh_empty_state()

    def _retire_model(self, model: QPdfBookmarkModel) -> None:
        self._disconnect_model(model)
        model.setDocument(None)
        model.deleteLater()

    def _connect_model(self, model: QPdfBookmarkModel) -> None:
        model.modelReset.connect(self._refresh_empty_state)
        model.rowsInserted.connect(self._refresh_empty_state)
        model.rowsRemoved.connect(self._refresh_empty_state)

    def _disconnect_model(self, model: QPdfBookmarkModel) -> None:
        model.modelReset.disconnect(self._refresh_empty_state)
        model.rowsInserted.disconnect(self._refresh_empty_state)
        model.rowsRemoved.disconnect(self._refresh_empty_state)

    def _refresh_empty_state(self, *_args) -> None:
        has_outline = self.model.rowCount() > 0
        self._stack.setCurrentWidget(self.tree if has_outline else self.empty_label)

    def _on_tree_activated(self, index: QModelIndex) -> None:
        self._activate_index(index, self._generation, self.model)

    def _activate_index(self, index: QModelIndex, generation: int | None,
                        model: QPdfBookmarkModel | None = None) -> None:
        # QModelIndex is a short-lived GUI input. Only immutable destination
        # values leave this handler, together with the generation they came from.
        model = self.model if model is None else model
        if generation is None or generation != self._generation:
            return
        if (self._document is None or model is not self.model
                or index.model() != model):
            return
        if not index.isValid():
            return
        page = index.data(int(QPdfBookmarkModel.Role.Page))
        if not isinstance(page, int) or isinstance(page, bool):
            return
        location = index.data(int(QPdfBookmarkModel.Role.Location))
        zoom = index.data(int(QPdfBookmarkModel.Role.Zoom))
        location_value = ((float(location.x()), float(location.y()))
                          if isinstance(location, QPointF) else None)
        if isinstance(zoom, (int, float)) and not isinstance(zoom, bool):
            try:
                zoom_value = float(zoom)
            except (OverflowError, ValueError):
                zoom_value = None
        else:
            zoom_value = None
        if (generation != self._generation or model is not self.model
                or self._document is None):
            return
        self.destinationActivated.emit(OutlineDestination(
            generation, page, location_value, zoom_value))

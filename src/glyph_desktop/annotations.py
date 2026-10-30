"""In-memory, document-scoped text markup in PDF page coordinates."""

from dataclasses import dataclass
from uuid import uuid4

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QPolygonF
from PySide6.QtCore import QPointF


@dataclass(frozen=True)
class Markup:
    id: str
    kind: str
    page: int
    generation: int
    regions: tuple[tuple[tuple[float, float], ...], ...]
    text: str


class Annotations(QObject):
    changed = Signal()

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self._items = []
        self.selected_id = None
        document.aboutToChange.connect(self.clear)

    @property
    def items(self):
        return tuple(self._items)

    def clear(self):
        self._items.clear()
        self.selected_id = None
        self.changed.emit()

    def create(self, kind, selection):
        if kind not in ("highlight", "underline", "strikeout") or not selection.valid():
            return None
        regions = tuple(tuple((float(point.x()), float(point.y())) for point in polygon)
                        for polygon in selection.result.bounds() if len(polygon) >= 3)
        if not regions or not 0 <= selection.page < self.document.count:
            return None
        item = Markup(uuid4().hex, kind, selection.page, self.document.generation,
                      regions, selection.result.text())
        self._items.append(item)
        self.selected_id = None
        selection.clear()
        self.changed.emit()
        return item

    def at(self, page, point):
        for item in reversed(self._items):
            if item.generation != self.document.generation or item.page != page:
                continue
            if any(QPolygonF([QPointF(x, y) for x, y in region]).containsPoint(
                    point, Qt.FillRule.OddEvenFill) for region in item.regions):
                return item
        return None

    def select(self, item_id, generation):
        if generation != self.document.generation:
            return
        self.selected_id = next((item.id for item in self._items
                                 if item.id == item_id and item.generation == generation), None)
        self.changed.emit()

    def delete_selected(self):
        selected = self.selected_id
        self._items[:] = [item for item in self._items if not (
            item.id == selected and item.generation == self.document.generation)]
        self.selected_id = None
        self.changed.emit()

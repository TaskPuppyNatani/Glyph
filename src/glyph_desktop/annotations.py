"""In-memory, document-scoped annotations (text markup and freehand ink) in PDF page coordinates."""

import math
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


@dataclass(frozen=True)
class Ink:
    id: str
    kind: str
    page: int
    generation: int
    points: tuple[tuple[float, float], ...]
    width: float


INK_WIDTH = 2.5
INK_MIN_LENGTH = 4.0
INK_HIT_TOLERANCE = 6.0


def stroke_length(points):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def near_polyline(points, point, tolerance):
    """True when point lies within tolerance of any segment of the polyline."""
    x, y = point.x(), point.y()
    if len(points) == 1:
        return math.hypot(points[0][0] - x, points[0][1] - y) <= tolerance
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        if not span:
            if math.hypot(ax - x, ay - y) <= tolerance:
                return True
            continue
        along = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / span))
        if math.hypot(ax + along * dx - x, ay + along * dy - y) <= tolerance:
            return True
    return False


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

    def add_ink(self, page, points, width=INK_WIDTH):
        if not 0 <= page < self.document.count or len(points) < 2:
            return None
        normalized = tuple((float(point[0]), float(point[1])) for point in points)
        if stroke_length(normalized) < INK_MIN_LENGTH:
            return None
        item = Ink(uuid4().hex, "ink", page, self.document.generation, normalized, float(width))
        self._items.append(item)
        self.selected_id = None
        self.changed.emit()
        return item

    def at(self, page, point):
        for item in reversed(self._items):
            if item.generation != self.document.generation or item.page != page:
                continue
            if item.kind == "ink":
                if near_polyline(item.points, point, item.width + INK_HIT_TOLERANCE):
                    return item
            elif any(QPolygonF([QPointF(x, y) for x, y in region]).containsPoint(
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

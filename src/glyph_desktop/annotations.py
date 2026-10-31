"""In-memory, document-scoped annotations (text markup, freehand ink, and sticky notes)
in PDF page coordinates, with a bounded undo/redo history over their mutations."""

import math
from dataclasses import dataclass, replace
from uuid import uuid4

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QPolygonF
from PySide6.QtCore import QPointF

from .history import AnnotationHistory, CREATE, DELETE, UPDATE


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


@dataclass(frozen=True)
class Note:
    id: str
    kind: str
    page: int
    generation: int
    anchor: tuple[float, float]
    text: str


INK_WIDTH = 2.5
INK_MIN_LENGTH = 4.0
INK_HIT_TOLERANCE = 6.0
# Marker edge in viewport pixels: sizing only, never stored as annotation data.
NOTE_MARKER_SIZE = 18.0


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
        self.history = AnnotationHistory(self)
        document.aboutToChange.connect(self.clear)

    @property
    def items(self):
        return tuple(self._items)

    def clear(self):
        self._items.clear()
        self.selected_id = None
        self.history.clear()
        self.changed.emit()

    def get(self, item_id):
        """Return the current-generation annotation with this id, or None."""
        index = self._index_of(item_id)
        return self._items[index] if index >= 0 else None

    def restore(self, index, item):
        """Put an existing immutable record back, keeping its id and generation."""
        if item.generation != self.document.generation:
            return False
        self._items.insert(max(0, min(index, len(self._items))), item)
        return True

    def replace(self, index, item):
        """Swap an updated record into its original position, keeping id and generation."""
        if not 0 <= index < len(self._items):
            return False
        current = self._items[index]
        if current.id != item.id or current.generation != self.document.generation:
            return False
        if item.generation != self.document.generation:
            return False
        self._items[index] = item
        return True

    def remove(self, item_id):
        """Take one current-generation record out of the store, or return None."""
        index = self._index_of(item_id)
        if index < 0:
            return None
        item = self._items.pop(index)
        if self.selected_id == item_id:
            self.selected_id = None
        return item

    def _index_of(self, item_id):
        generation = self.document.generation
        for index, item in enumerate(self._items):
            if item.id == item_id and item.generation == generation:
                return index
        return -1

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
        self.history.record(CREATE, item, len(self._items) - 1)
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
        self.history.record(CREATE, item, len(self._items) - 1)
        self.changed.emit()
        return item

    def add_note(self, page, anchor, text):
        """Create one Note from plain PDF-page values; blank text creates nothing."""
        if not 0 <= page < self.document.count:
            return None
        text = (text or "").strip()
        if not text:
            return None
        x, y = anchor
        item = Note(uuid4().hex, "note", page, self.document.generation,
                    (float(x), float(y)), text)
        self._items.append(item)
        self.selected_id = None
        self.history.record(CREATE, item, len(self._items) - 1)
        self.changed.emit()
        return item

    def update_note_text(self, item_id, text):
        """Edit one Note in place, keeping id, page, anchor, and generation."""
        index = self._index_of(item_id)
        if index < 0:
            return None
        previous = self._items[index]
        if previous.kind != "note":
            return None
        text = (text or "").strip()
        if not text or text == previous.text:
            return None
        item = replace(previous, text=text)
        self._items[index] = item
        self.history.record(UPDATE, item, index, previous)
        self.changed.emit()
        return item

    def at(self, page, point, note_radius=NOTE_MARKER_SIZE / 2):
        for item in reversed(self._items):
            if item.generation != self.document.generation or item.page != page:
                continue
            if item.kind == "ink":
                if near_polyline(item.points, point, item.width + INK_HIT_TOLERANCE):
                    return item
            elif item.kind == "note":
                if (abs(point.x() - item.anchor[0]) <= note_radius
                        and abs(point.y() - item.anchor[1]) <= note_radius):
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
        """Remove the selected annotation as one reversible history entry."""
        index = self._index_of(self.selected_id)
        item = self._items.pop(index) if index >= 0 else None
        self.selected_id = None
        if item is not None:
            self.history.record(DELETE, item, index)
        self.changed.emit()
        return item

    @property
    def can_undo(self):
        return self.history.can_undo

    @property
    def can_redo(self):
        return self.history.can_redo

    def undo(self):
        """Reverse the newest remembered mutation, or refuse a stale one."""
        return self.history.undo()

    def redo(self):
        """Replay the newest reversed mutation, or refuse a stale one."""
        return self.history.redo()

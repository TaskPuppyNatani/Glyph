"""In-memory, document-scoped annotations (text markup, rectangles, freehand ink, and sticky notes)
in PDF page coordinates, with a bounded undo/redo history over their mutations."""

import math
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QPolygonF
from PySide6.QtCore import QPointF

from .history import AnnotationHistory, AnnotationOperation, CREATE, DELETE, UPDATE


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


@dataclass(frozen=True)
class Rectangle:
    id: str
    kind: str
    page: int
    generation: int
    rect: tuple[float, float, float, float]


@dataclass(frozen=True)
class AnnotationSessionSnapshot:
    """Value-only recovery state for one open document."""
    path: Path
    identity: tuple[int, int]
    generation: int
    items: tuple
    selected_id: str | None
    undo: tuple
    redo: tuple


INK_WIDTH = 2.5
INK_MIN_LENGTH = 4.0
INK_HIT_TOLERANCE = 6.0
RECTANGLE_COLOR = (200, 52, 66)
RECTANGLE_WIDTH = 2.5
RECTANGLE_HIT_TOLERANCE = 4.0
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


def _normalize_rectangle(rect):
    """Return a finite, nondegenerate rectangle ordered left/top/right/bottom."""
    if type(rect) not in (tuple, list) or len(rect) != 4:
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           for value in rect):
        return None
    try:
        values = tuple(float(value) for value in rect)
    except (OverflowError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    left, right = sorted((values[0], values[2]))
    top, bottom = sorted((values[1], values[3]))
    if left >= right or top >= bottom:
        return None
    return left, top, right, bottom


def _rectangle_outline(rect):
    left, top, right, bottom = rect
    return ((left, top), (right, top), (right, bottom),
            (left, bottom), (left, top))


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

    def capture_session(self) -> AnnotationSessionSnapshot:
        """Capture annotations and history for explicit recovery after reopen."""
        if self.document.pdf is None or self.document.path is None:
            raise ValueError("An open document is required")
        path = Path(self.document.path).resolve(strict=True)
        info = path.stat()
        if not info.st_ino:
            raise ValueError("Document identity cannot be verified")
        return AnnotationSessionSnapshot(path, (info.st_dev, info.st_ino),
                                         self.document.generation, self.items,
                                         self.selected_id, tuple(self.history._undo),
                                         tuple(self.history._redo))

    def restore_session(self, snapshot: AnnotationSessionSnapshot) -> None:
        """Restore a captured session to the same reopened document atomically."""
        if not isinstance(snapshot, AnnotationSessionSnapshot):
            raise ValueError("Invalid annotation session snapshot")
        if self.document.pdf is None or self.document.path is None:
            raise ValueError("An open document is required")
        if (type(snapshot.generation) is not int or snapshot.generation < 0
                or self.document.generation <= snapshot.generation):
            raise ValueError("Document has not been reopened after the snapshot")
        path = Path(self.document.path).resolve(strict=True)
        info = path.stat()
        if path != snapshot.path or not info.st_ino or (info.st_dev, info.st_ino) != snapshot.identity:
            raise ValueError("Snapshot belongs to a different document")
        if self._items or self.history._undo or self.history._redo or self.selected_id is not None:
            raise ValueError("Cannot replace a nonempty annotation session")
        generation = self.document.generation

        def rebind(item):
            if (type(item) not in (Markup, Ink, Note, Rectangle) or item.generation != snapshot.generation
                    or type(item.page) is not int or not 0 <= item.page < self.document.count):
                raise ValueError("Snapshot contains an incompatible annotation")
            if type(item) is Rectangle:
                normalized = _normalize_rectangle(item.rect)
                if (item.kind != "rectangle" or not isinstance(item.id, str) or not item.id
                        or type(item.rect) is not tuple or normalized is None
                        or item.rect != normalized):
                    raise ValueError("Snapshot contains an invalid rectangle")
                width, height = self.document.page_sizes[item.page]
                if (normalized[0] < 0 or normalized[1] < 0
                        or normalized[2] > width or normalized[3] > height):
                    raise ValueError("Snapshot rectangle lies outside its page")
            return replace(item, generation=generation)

        def rebind_operation(operation):
            if (type(operation) is not AnnotationOperation or operation.generation != snapshot.generation
                    or operation.action not in (CREATE, DELETE, UPDATE)
                    or type(operation.index) is not int or operation.index < 0):
                raise ValueError("Snapshot contains incompatible history")
            item = rebind(operation.annotation)
            previous = rebind(operation.previous) if operation.previous is not None else None
            if ((operation.action == UPDATE) != (previous is not None)
                    or (previous is not None and (previous.id != item.id or previous.page != item.page
                                                  or type(previous) is not type(item)))):
                raise ValueError("Snapshot contains incompatible history")
            return replace(operation, generation=generation, annotation=item, previous=previous)

        if not all(type(value) is tuple for value in (snapshot.items, snapshot.undo, snapshot.redo)):
            raise ValueError("Snapshot contains mutable session data")
        items = [rebind(item) for item in snapshot.items]
        ids = [item.id for item in items]
        if len(set(ids)) != len(ids) or (snapshot.selected_id is not None and snapshot.selected_id not in ids):
            raise ValueError("Snapshot contains invalid selection or duplicate annotations")
        undo = [rebind_operation(operation) for operation in snapshot.undo]
        redo = [rebind_operation(operation) for operation in snapshot.redo]
        if len(undo) > self.history.limit or len(redo) > self.history.limit:
            raise ValueError("Snapshot history exceeds the configured limit")
        self._items[:] = items
        self.selected_id = snapshot.selected_id
        self.history._undo[:] = undo
        self.history._redo[:] = redo
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

    def add_rectangle(self, page, rect):
        """Create one normalized, page-bounded Rectangle from PDF-page coordinates."""
        if type(page) is not int or not 0 <= page < self.document.count:
            return None
        normalized = _normalize_rectangle(rect)
        if normalized is None:
            return None
        width, height = self.document.page_sizes[page]
        if (normalized[0] < 0 or normalized[1] < 0
                or normalized[2] > width or normalized[3] > height):
            return None
        item = Rectangle(uuid4().hex, "rectangle", page, self.document.generation, normalized)
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

    def bounded_move_delta(self, item, dx, dy):
        """Return one same-page PDF translation that keeps an annotation in bounds."""
        if (not isinstance(item, (Note, Ink, Rectangle)) or item.generation != self.document.generation
                or not math.isfinite(dx) or not math.isfinite(dy)):
            return None
        if isinstance(item, Ink) and not item.points:
            return None
        geometry = (item.anchor if isinstance(item, Note) else item.rect
                    if isinstance(item, Rectangle) else
                    tuple(value for point in item.points for value in point))
        if not all(math.isfinite(value) for value in geometry):
            return None
        index = self._index_of(item.id)
        if (index < 0 or self._items[index] != item
                or not 0 <= item.page < len(self.document.page_sizes)):
            return None
        width, height = self.document.page_sizes[item.page]
        if isinstance(item, Note):
            x = min(max(item.anchor[0] + dx, 0.0), width)
            y = min(max(item.anchor[1] + dy, 0.0), height)
            return x - item.anchor[0], y - item.anchor[1]
        if isinstance(item, Rectangle):
            left, top, right, bottom = item.rect
            if left < 0 or top < 0 or right > width or bottom > height:
                return None
            return (min(max(dx, -left), width - right),
                    min(max(dy, -top), height - bottom))
        xs = [point[0] for point in item.points]
        ys = [point[1] for point in item.points]
        if not math.isfinite(item.width):
            return None
        pad = max(0.0, item.width / 2)
        min_x, max_x = pad - min(xs), width - pad - max(xs)
        min_y, max_y = pad - min(ys), height - pad - max(ys)
        if min_x > max_x or min_y > max_y:
            return 0.0, 0.0
        return min(max(dx, min_x), max_x), min(max(dy, min_y), max_y)

    def move(self, item, dx, dy):
        """Commit one bounded rigid movement as an ordinary immutable UPDATE."""
        if not isinstance(item, (Note, Ink, Rectangle)):
            return None
        index = self._index_of(item.id)
        if index < 0 or self._items[index] != item:
            return None
        delta = self.bounded_move_delta(item, dx, dy)
        if delta is None or delta == (0.0, 0.0):
            return None
        dx, dy = delta
        previous = self._items[index]
        if isinstance(previous, Note):
            updated = replace(previous, anchor=(previous.anchor[0] + dx,
                                                previous.anchor[1] + dy))
        elif isinstance(previous, Rectangle):
            left, top, right, bottom = previous.rect
            updated = replace(previous, rect=(left + dx, top + dy,
                                              right + dx, bottom + dy))
        elif isinstance(previous, Ink):
            updated = replace(previous, points=tuple((x + dx, y + dy)
                                                     for x, y in previous.points))
        else:
            return None
        if updated == previous:
            return None
        self._items[index] = updated
        self.history.record(UPDATE, updated, index, previous)
        self.changed.emit()
        return updated

    def at(self, page, point, note_radius=NOTE_MARKER_SIZE / 2,
           rectangle_tolerance=RECTANGLE_HIT_TOLERANCE):
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
            elif item.kind == "rectangle":
                if near_polyline(_rectangle_outline(item.rect), point,
                                 rectangle_tolerance + RECTANGLE_WIDTH / 2):
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

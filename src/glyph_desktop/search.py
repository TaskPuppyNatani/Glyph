"""Incremental, document-wide text search backed by QtPdf text geometry."""

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRectF, QTimer, Signal
from PySide6.QtGui import QPolygonF


@dataclass(frozen=True, slots=True)
class SearchResult:
    generation: int
    page: int
    order: int
    start_index: int
    length: int
    text: str
    bounds: tuple[QPolygonF, ...]
    bounding_rect: QRectF


def _casefold_index(text: str):
    folded_chars = []
    starts = []
    ends = []
    qt_offsets = [0]
    for index, char in enumerate(text):
        folded = char.casefold()
        folded_chars.append(folded)
        starts.extend([index] * len(folded))
        ends.extend([index + 1] * len(folded))
        qt_offsets.append(qt_offsets[-1] + (2 if ord(char) > 0xFFFF else 1))
    return "".join(folded_chars), starts, ends, qt_offsets


class DocumentSearch(QObject):
    """Search one QtPdf page per event-loop turn and retain only match geometry."""

    changed = Signal()
    activeResultChanged = Signal(object)
    queryCleared = Signal()

    debounce_ms = 120
    matches_per_tick = 64

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.query = ""
        self._effective_query = ""
        self._results: list[SearchResult] = []
        self._results_by_page: dict[int, list[SearchResult]] = {}
        self._active_index = -1
        self.searching = False
        self._request_id = 0
        self._document_generation = -1
        self._page = 0
        self._folded_text = ""
        self._fold_starts: list[int] = []
        self._fold_ends: list[int] = []
        self._qt_offsets: list[int] = []
        self._base_index = 0
        self._cursor = 0

        self._debounce_timer = QTimer(self, singleShot=True)
        self._debounce_timer.setInterval(self.debounce_ms)
        self._debounce_timer.timeout.connect(self._begin_scan)
        self._scan_timer = QTimer(self, singleShot=True)
        self._scan_timer.timeout.connect(self._scan_step)
        document.aboutToChange.connect(self._clear_for_document_change)

    @property
    def results(self) -> tuple[SearchResult, ...]:
        return tuple(self._results)

    @property
    def result_count(self) -> int:
        return len(self._results)

    @property
    def current_index(self) -> int:
        return self._active_index

    @property
    def active_result(self) -> SearchResult | None:
        if 0 <= self._active_index < len(self._results):
            result = self._results[self._active_index]
            if result.generation == self.document.generation:
                return result
        return None

    def results_for_page(self, page: int) -> tuple[SearchResult, ...]:
        return tuple(self._results_by_page.get(page, ()))

    def set_query(self, query: str) -> None:
        query = str(query)
        effective = query.strip()
        if not effective.casefold():
            effective = ""
        if query == self.query:
            return
        self.query = query
        if effective == self._effective_query:
            return

        had_active = self._active_index >= 0
        self._invalidate_request()
        self._effective_query = effective
        self._results.clear()
        self._results_by_page.clear()
        self._active_index = -1
        self._reset_page_state()
        self.searching = bool(effective and self.document.pdf)
        self._document_generation = self.document.generation
        if self.searching:
            self._debounce_timer.start(self.debounce_ms)
        if had_active:
            self.activeResultChanged.emit(None)
        self.changed.emit()

    def clear(self) -> None:
        had_query = bool(self.query)
        had_active = self._active_index >= 0
        self._invalidate_request()
        self.query = ""
        self._effective_query = ""
        self._results.clear()
        self._results_by_page.clear()
        self._active_index = -1
        self.searching = False
        self._document_generation = self.document.generation
        self._reset_page_state()
        if had_active:
            self.activeResultChanged.emit(None)
        if had_query:
            self.queryCleared.emit()
        self.changed.emit()

    def next(self) -> None:
        if not self._results:
            return
        self._active_index = (self._active_index + 1) % len(self._results)
        self.activeResultChanged.emit(self.active_result)
        self.changed.emit()

    def previous(self) -> None:
        if not self._results:
            return
        self._active_index = (self._active_index - 1) % len(self._results)
        self.activeResultChanged.emit(self.active_result)
        self.changed.emit()

    def _clear_for_document_change(self) -> None:
        self.clear()

    def _invalidate_request(self) -> None:
        self._request_id += 1
        self._debounce_timer.stop()
        self._scan_timer.stop()

    def _reset_page_state(self) -> None:
        self._page = 0
        self._clear_page_text()

    def _clear_page_text(self) -> None:
        self._folded_text = ""
        self._fold_starts = []
        self._fold_ends = []
        self._qt_offsets = []
        self._base_index = 0
        self._cursor = 0

    def _is_current_request(self, request_id: int) -> bool:
        return (request_id == self._request_id and bool(self._effective_query)
                and self.document.pdf is not None
                and self.document.generation == self._document_generation)

    def _begin_scan(self) -> None:
        request_id = self._request_id
        if not self._is_current_request(request_id):
            return
        self._reset_page_state()
        self._scan_timer.start(0)

    def _load_page_text(self) -> None:
        selection = self.document.pdf.getAllText(self._page)
        text = selection.text() if selection.isValid() else ""
        (self._folded_text, self._fold_starts,
         self._fold_ends, self._qt_offsets) = _casefold_index(text)
        self._base_index = selection.startIndex() if selection.isValid() else 0
        self._cursor = 0

    def _scan_step(self) -> None:
        request_id = self._request_id
        if not self._is_current_request(request_id):
            return
        if self._page >= self.document.count:
            self._finish_scan(request_id)
            return
        if not self._qt_offsets:
            self._load_page_text()

        needle = self._effective_query.casefold()
        page_results = []
        page_finished = False
        for _ in range(self.matches_per_tick):
            found = self._folded_text.find(needle, self._cursor)
            if found < 0:
                self._page += 1
                self._clear_page_text()
                page_finished = True
                break

            fold_end = found + len(needle)
            starts_at_character = (found == 0 or
                                   self._fold_starts[found] != self._fold_starts[found - 1])
            ends_at_character = (fold_end == len(self._fold_ends) or
                                 self._fold_ends[fold_end - 1] != self._fold_ends[fold_end])
            if not starts_at_character or not ends_at_character:
                self._cursor = found + 1
                continue

            text_start = self._fold_starts[found]
            text_end = self._fold_ends[fold_end - 1]
            qt_start = self._base_index + self._qt_offsets[text_start]
            qt_length = self._qt_offsets[text_end] - self._qt_offsets[text_start]
            self._cursor = fold_end
            selection = self.document.pdf.getSelectionAtIndex(self._page, qt_start, qt_length)
            if not selection.isValid() or not selection.text():
                continue

            bounds = tuple(QPolygonF(polygon) for polygon in selection.bounds())
            if not bounds:
                continue
            bounding_rect = bounds[0].boundingRect()
            for polygon in bounds[1:]:
                bounding_rect = bounding_rect.united(polygon.boundingRect())
            page_results.append(SearchResult(
                generation=self._document_generation,
                page=self._page,
                order=len(self._results) + len(page_results),
                start_index=qt_start,
                length=qt_length,
                text=selection.text(),
                bounds=bounds,
                bounding_rect=bounding_rect,
            ))

        if page_results:
            self._results.extend(page_results)
            self._results_by_page.setdefault(page_results[0].page, []).extend(page_results)
            if self._active_index < 0:
                self._active_index = 0
                self.activeResultChanged.emit(self.active_result)

        if page_results:
            self.changed.emit()
        if not self._is_current_request(request_id):
            return
        if page_finished and self._page >= self.document.count:
            self._finish_scan(request_id)
        else:
            self._scan_timer.start(0)

    def _finish_scan(self, request_id: int) -> None:
        if not self._is_current_request(request_id):
            return
        self.searching = False
        self._reset_page_state()
        self.changed.emit()

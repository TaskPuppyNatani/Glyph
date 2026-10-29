"""Transient PDF-point text selection for the current document."""

from PySide6.QtCore import QObject, Signal


class TextSelection(QObject):
    changed = Signal()

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.page = None
        self.generation = None
        self.start = None
        self.end = None
        self.result = None
        self._text_page = None
        self._text_generation = None
        self._all_text = None
        self._hit_bounds = []
        document.aboutToChange.connect(self.clear_document)

    def clear(self):
        self.page = self.generation = self.start = self.end = self.result = None
        self.changed.emit()

    def clear_document(self):
        self._text_page = self._text_generation = self._all_text = None
        self._hit_bounds = []
        self.clear()

    def all_text(self, page):
        if not self.document.pdf or not 0 <= page < self.document.count:
            return None
        if (self._text_page, self._text_generation) != (page, self.document.generation):
            self._all_text = self.document.pdf.getAllText(page)
            self._hit_bounds = [polygon.boundingRect().adjusted(-2, -2, 2, 2)
                                for polygon in self._all_text.bounds()]
            self._text_page, self._text_generation = page, self.document.generation
        return self._all_text

    def text_hit(self, page, point):
        selection = self.all_text(page)
        return bool(selection and selection.isValid() and
                    any(rect.contains(point) for rect in self._hit_bounds))

    def set_result(self, page, selection):
        if selection and selection.isValid() and selection.text() and selection.bounds():
            self.page, self.generation, self.result = page, self.document.generation, selection
            self.start, self.end = selection.startIndex(), selection.endIndex()
        else:
            self.page = self.generation = self.start = self.end = self.result = None
        self.changed.emit()

    def drag(self, page, start, end):
        if self.document.pdf:
            self.set_result(page, self.document.pdf.getSelection(page, start, end))

    def select_all(self, page):
        # M2A selects only the current page.
        self.set_result(page, self.all_text(page))

    def valid(self):
        return bool(self.result and self.document.pdf and self.generation == self.document.generation)

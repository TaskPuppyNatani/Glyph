"""Compact find controls for the PDF reader."""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget


class FindBar(QWidget):
    closeRequested = Signal()

    def __init__(self, search, parent=None):
        super().__init__(parent)
        self.search = search
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(4)

        self.query_edit = QLineEdit(self)
        self.query_edit.setAccessibleName("Find in document")
        self.query_edit.setPlaceholderText("Find in document")
        self.query_edit.setClearButtonEnabled(True)
        self.query_edit.installEventFilter(self)

        self.previous_button = QPushButton("‹", self)
        self.previous_button.setAccessibleName("Previous search result")
        self.previous_button.setToolTip("Previous result (Shift+Enter)")
        self.next_button = QPushButton("›", self)
        self.next_button.setAccessibleName("Next search result")
        self.next_button.setToolTip("Next result (Enter)")
        self.result_label = QLabel("0 / 0", self)
        self.result_label.setAccessibleName("Search result count")
        self.close_button = QPushButton("×", self)
        self.close_button.setAccessibleName("Close find bar")
        self.close_button.setToolTip("Close find bar (Escape)")
        for button in (self.previous_button, self.next_button, self.close_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout.addWidget(self.query_edit, 1)
        layout.addWidget(self.previous_button)
        layout.addWidget(self.next_button)
        layout.addWidget(self.result_label)
        layout.addWidget(self.close_button)

        self.query_edit.textChanged.connect(self.search.set_query)
        self.previous_button.clicked.connect(self.search.previous)
        self.next_button.clicked.connect(self.search.next)
        self.close_button.clicked.connect(lambda _checked=False: self.closeRequested.emit())
        self.search.changed.connect(self._refresh)
        self.search.queryCleared.connect(self.query_edit.clear)
        self._refresh()

    def show_and_focus(self) -> None:
        self.show()
        self.query_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.query_edit.selectAll()

    def eventFilter(self, watched, event):
        if watched is self.query_edit and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.search.previous()
                else:
                    self.search.next()
                event.accept()
                return True
            if event.key() == Qt.Key.Key_Escape:
                self.closeRequested.emit()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _refresh(self) -> None:
        count = self.search.result_count
        if self.search.searching:
            self.result_label.setText(f"Searching… {count} found" if count else "Searching…")
        elif count:
            self.result_label.setText(f"{self.search.current_index + 1} / {count}")
        else:
            self.result_label.setText("0 / 0")
        self.previous_button.setEnabled(count > 0)
        self.next_button.setEnabled(count > 0)
